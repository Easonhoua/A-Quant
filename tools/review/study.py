"""事件研究、参数敏感性、真实引擎抽样回测。

事件研究回答的是"信号有没有边际"，而不是"回测赚不赚钱"：
对每个信号日，记录之后 1/3/5/8/10/20 日的收益，并与"同池全部股票日"的同期限
收益比较（同池 = 同样通过板块/ST/流动性/成交过滤的股票日）。基准里已经包含了
市场整体的涨跌，所以超额收益才是信号本身贡献的部分。
"""

import math
import random
from collections import Counter

from backend.engine import STRATEGY, backtest, stop_reference
from tools.review.panel import flags, iter_panels, pool_ok

HORIZONS = (1, 3, 5, 8, 10, 20)


def describe(values):
    """均值/中位/胜率/分位数；t 值只作参考（横截面相关会使其虚高）。"""
    if not values:
        return None
    ordered = sorted(values)
    n = len(ordered)
    mean = sum(ordered) / n
    median = ordered[n // 2] if n % 2 else (ordered[n // 2 - 1] + ordered[n // 2]) / 2
    var = sum((x - mean) ** 2 for x in ordered) / (n - 1) if n > 1 else 0.0
    std = math.sqrt(var)
    stderr = std / math.sqrt(n) if n > 1 else 0.0

    def quantile(q):
        if n == 1:
            return ordered[0]
        pos = (n - 1) * q
        low = int(math.floor(pos))
        high = min(low + 1, n - 1)
        return ordered[low] + (ordered[high] - ordered[low]) * (pos - low)

    return {
        "n": n,
        "mean": round(mean, 5),
        "median": round(median, 5),
        "std": round(std, 5),
        "win_rate": round(sum(1 for x in ordered if x > 0) / n, 4),
        "t_stat": round(mean / stderr, 2) if stderr else None,
        "p05": round(quantile(0.05), 5),
        "p95": round(quantile(0.95), 5),
    }


def _measure(panel, t, hold):
    """一个信号日之后发生的事：前向收益、2R 与止损谁先到、按规则会怎么离场。"""
    atr = panel.atr[t]
    close = panel.c[t]
    stop = stop_reference(close, atr)
    if stop <= 0 or stop >= close:
        return None
    risk = close - stop
    target = close + 2 * risk
    n = panel.n
    fwd = {str(h): panel.c[t + h] / close - 1 for h in HORIZONS if t + h < n}
    end = min(t + hold, n - 1)

    race = {"outcome": "NONE", "day": None}
    for d in range(1, end - t + 1):
        if panel.l[t + d] <= stop:
            race = {"outcome": "STOP", "day": d}
            break
        if panel.h[t + d] >= target:
            race = {"outcome": "TARGET", "day": d}
            break

    flow = {"outcome": "TIME", "day": end - t}
    for d in range(1, end - t + 1):
        ma20 = panel.ma20[t + d]
        if panel.c[t + d] <= stop:
            flow = {"outcome": "STOP", "day": d}
            break
        if ma20 is not None and panel.c[t + d] < ma20:
            flow = {"outcome": "MA20", "day": d}
            break

    window_h = panel.h[t + 1:end + 1]
    window_l = panel.l[t + 1:end + 1]
    return {
        "fwd": fwd,
        "race": race,
        "flow": flow,
        "gap": panel.o[t + 1] / close - 1,
        "mfe": (max(window_h) / close - 1) if window_h else None,
        "mae": (min(window_l) / close - 1) if window_l else None,
        "realized": panel.c[end] / close - 1,
    }


def _race_stats(races):
    if not races:
        return None
    n = len(races)
    targets = [r for r in races if r["outcome"] == "TARGET"]
    stops = [r for r in races if r["outcome"] == "STOP"]
    return {
        "target_first_pct": round(100 * len(targets) / n, 2),
        "stop_first_pct": round(100 * len(stops) / n, 2),
        "neither_pct": round(100 * (n - len(targets) - len(stops)) / n, 2),
        "median_day_target": sorted(r["day"] for r in targets)[len(targets) // 2] if targets else None,
        "median_day_stop": sorted(r["day"] for r in stops)[len(stops) // 2] if stops else None,
    }


def _flow_stats(flows):
    if not flows:
        return None
    counts = Counter(f["outcome"] for f in flows)
    return {k: round(100 * v / len(flows), 2) for k, v in sorted(counts.items())}


def event_study(snapshot, include_strict=True):
    """返回基准分布与 S1/S2（及严格复筛）的超额收益、2R 竞速、离场归因。"""
    baseline = {str(h): [] for h in HORIZONS}
    by_date={}
    matched={tag:{str(h):[] for h in HORIZONS} for tag in ("S1","S2","S1s","S2s")}
    events = {tag: [] for tag in ("S1", "S2", "S1s", "S2s")}
    per_date = Counter()
    scanned_dates = set()
    pool_days = 0
    skipped = 0

    for panel in iter_panels(snapshot):
        for t in range(59, panel.n - 2):
            if not pool_ok(panel, t):
                continue
            f = panel.at(t)
            if f is None:
                continue
            pool_days += 1
            scanned_dates.add(panel.dates[t])
            for h in HORIZONS:
                if t + h < panel.n:
                    value=panel.c[t + h] / panel.c[t] - 1
                    baseline[str(h)].append(value)
                    key=(panel.dates[t],str(h)); total,count=by_date.get(key,(0,0))
                    by_date[key]=(total+value,count+1)
            s1, s2 = flags(f)
            s1s, s2s = flags(f, strict=True) if include_strict else (False, False)
            if not (s1 or s2 or s1s or s2s):
                continue
            per_date[panel.dates[t]] += 1
            for tag, on, hold in (("S1", s1, STRATEGY["max_hold_s1"]), ("S2", s2, STRATEGY["max_hold_s2"]),
                                  ("S1s", s1s, STRATEGY["max_hold_s1"]), ("S2s", s2s, STRATEGY["max_hold_s2"])):
                if not on:
                    continue
                row = _measure(panel, t, hold)
                if row is None:
                    skipped += 1
                    continue
                row['signal_date']=panel.dates[t]
                events[tag].append(row)

    base = {h: describe(v) for h, v in baseline.items()}
    result = {
        "pool_days": pool_days,
        "sessions_scanned": len(scanned_dates),
        "invalid_stop_skipped": skipped,
        "baseline": base,
        "method_notes":["excess为全期混合基准差，date_matched_excess按信号日匹配同池基准，仍未做行业市值中性化",
                        "股票日相关、持有窗口重叠，t值不作为显著性结论",
                        "two_r_race为信号收盘后的理论触价实验，非次日开盘成交回测；先到次数比不等于实际盈亏比",
                        "不复权、幸存者偏差仍存在；旧评审输出不会自动成为新版有效性证据"],
        "events": {},
        "per_session": _per_session(per_date, len(scanned_dates)),
    }
    for tag, rows in events.items():
        for row in rows:
            for h,value in row['fwd'].items():
                total,count=by_date[(row['signal_date'],h)]
                matched[tag][h].append(value-total/count)
        fwd = {str(h): describe([r["fwd"][str(h)] for r in rows if str(h) in r["fwd"]]) for h in HORIZONS}
        result["events"][tag] = {
            "n": len(rows),
            "date_matched_excess":{h:describe(v) for h,v in matched[tag].items()},
            "fwd": fwd,
            "excess": {str(h): (round(fwd[str(h)]["mean"] - base[str(h)]["mean"], 5)
                                if fwd[str(h)] and base[str(h)] else None) for h in HORIZONS},
            "two_r_race": _race_stats([r["race"] for r in rows]),
            "exit_flow": _flow_stats([r["flow"] for r in rows]),
            "entry_gap": describe([r["gap"] for r in rows]),
            "gap_split": _gap_split(rows),
            "mfe_mae": {"mfe": describe([r["mfe"] for r in rows if r["mfe"] is not None]),
                        "mae": describe([r["mae"] for r in rows if r["mae"] is not None])},
        }
    return result


def _per_session(per_date, sessions_scanned):
    if not per_date:
        return None
    counts = sorted(per_date.values())
    n = len(counts)
    total = sum(counts)
    return {
        "sessions_scanned": sessions_scanned,
        "sessions_with_signals": n,
        "signal_stock_days": total,
        "mean_per_session": round(total / sessions_scanned, 2) if sessions_scanned else None,
        "mean_when_any": round(total / n, 2),
        "median": counts[n // 2],
        "p90": counts[int((n - 1) * 0.9)],
        "max": counts[-1],
    }


def _gap_split(rows):
    """页面要求"次日开盘不高于参考收盘价 1%"才买；这个过滤是否伤害了信号？"""
    def avg(items):
        values = [r["fwd"]["10"] for r in items if "10" in r["fwd"]]
        return round(sum(values) / len(values), 5) if values else None

    executable = [r for r in rows if r["gap"] is not None and r["gap"] <= 0.01]
    skipped = [r for r in rows if r["gap"] is not None and r["gap"] > 0.01]
    total = len(executable) + len(skipped)
    return {
        "executable_share_pct": round(100 * len(executable) / total, 2) if total else None,
        "fwd10_executable": avg(executable),
        "fwd10_skipped": avg(skipped),
        "n_executable": len(executable),
        "n_skipped": len(skipped),
    }


def sensitivity(snapshot, max_stocks=None, windows=(10, 15, 20, 30, 40),
                ratios=(1.0, 1.1, 1.2, 1.3, 1.4, 1.5, 1.7, 2.0), horizon=10, seed=5):
    """S1 的 10 日超额收益随"唐奇安窗口 × 量比阈值"的变化。

    一次遍历，所有格子同时累计：判断参数是在平台上、悬崖上，还是整片都没边际。
    """
    panels = list(iter_panels(snapshot))
    if max_stocks and len(panels) > max_stocks:
        panels = random.Random(seed).sample(panels, max_stocks)
    cells = {(w, r): [0, 0.0, 0] for w in windows for r in ratios}
    base_sum, base_n, pool_days = 0.0, 0, 0

    for panel in panels:
        for t in range(59, panel.n - horizon):
            if not pool_ok(panel, t):
                continue
            f = panel.at(t)
            if f is None:
                continue
            pool_days += 1
            fwd = panel.c[t + horizon] / panel.c[t] - 1
            base_sum += fwd
            base_n += 1
            if panel.c[t] <= f["ma20"]:
                continue
            for w in windows:
                if panel.c[t] <= max(panel.h[t - w:t]):
                    continue
                for r in ratios:
                    if f["volume_ratio"] >= r:
                        cell = cells[(w, r)]
                        cell[0] += 1
                        cell[1] += fwd
                        cell[2] += 1 if fwd > 0 else 0

    base_mean = base_sum / base_n if base_n else 0.0
    grid = {}
    for (w, r), (count, total, wins) in cells.items():
        grid[f"w{w}_r{r}"] = {
            "signals": count,
            "per_1000_days": round(1000 * count / pool_days, 2) if pool_days else None,
            "fwd_mean": round(total / count, 5) if count else None,
            "excess": round(total / count - base_mean, 5) if count else None,
            "win_rate": round(wins / count, 4) if count else None,
        }
    return {"stocks": len(panels), "pool_days": pool_days, "horizon": horizon,
            "baseline_mean": round(base_mean, 5), "grid": grid}


def sample_backtest(snapshot, max_stocks=150, models=("S1", "S2"), seed=3, progress=None):
    """用项目自带的 engine.backtest 抽样回测，给出"每笔交易"的真实分布。

    engine.backtest 每天都会从第一根 K 线重算指标（O(n²)），单只股票约 0.4 秒，
    所以默认只抽 150 只（约 1 分钟）；要更细的分布用 `--stocks 350`。
    progress 是可选的进度回调，形如 progress(done, total)。
    """
    pool = []
    from backend.engine import allowed, valid_bars
    for stock in snapshot["stocks"]:
        bars = stock.get("bars") or []
        if len(bars) < 150 or not allowed(stock["code"], stock["name"]) or not valid_bars(bars):
            continue
        if bars[-1]["volume"] <= 0:
            continue
        pool.append(stock)
    if max_stocks and len(pool) > max_stocks:
        pool = random.Random(seed).sample(pool, max_stocks)

    result = {"stocks": len(pool), "models": {}}
    buy_hold = [s["bars"][-1]["close"] / s["bars"][0]["close"] - 1 for s in pool]
    for model in models:
        trade_returns, holds, totals, with_trades = [], [], [], 0
        for done, stock in enumerate(pool, 1):
            try:
                run = backtest(stock, model=model)
            except ValueError:
                continue
            trades = run["trades"]
            if trades:
                with_trades += 1
                totals.append(run["total_return"])
            buys = [t for t in trades if t["side"] == "BUY"]
            sells = [t for t in trades if t["side"] == "SELL"]
            index = {b["date"]: i for i, b in enumerate(stock["bars"])}
            for buy, sell in zip(buys, sells):
                cost = buy["quantity"] * buy["price"] + buy["fee"]
                proceeds = sell["quantity"] * sell["price"] - sell["fee"]
                trade_returns.append(proceeds / cost - 1)
                holds.append(index[sell["date"]] - index[buy["date"]])
            if progress and (done % 25 == 0 or done == len(pool)):
                progress(model, done, len(pool))
        hold_stats = describe([float(h) for h in holds]) or {}
        result["models"][model] = {
            "stocks_with_trades": with_trades,
            "trades": len(trade_returns),
            "trades_per_stock": round(len(trade_returns) / with_trades, 2) if with_trades else None,
            "trade_return": describe(trade_returns),
            "total_return": describe(totals),
            "holding_days": {"mean": round(hold_stats.get("mean", 0), 2) if holds else None,
                             "median": hold_stats.get("median"),
                             "max": max(holds) if holds else None},
        }
    result["buy_and_hold"] = describe(buy_hold)
    result["universe_note"] = f"池内 {len(pool)} 只抽样；股票池为当前在册股票，含幸存者偏差"
    return result

