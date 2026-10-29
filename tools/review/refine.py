"""候选筛选与排序的实验台。

要解决的问题不是"信号太少"，而是"信号太多、没有排序"：基础规则平均每个交易日
451 条候选，而 70% 仓位 + 20% 单股上限只装得下 5~7 只。于是"买哪几只"实际上由
代码字典序决定。

这里把两件事分开量化，因为代价完全不同：

  硬过滤（filter）  直接删掉不满足条件的信号。省事，但每加一条都会损失样本，
                    而且很容易像"严格复筛"那样越加越差。
  排序（ranking）   保留全部信号，按某个分数取前 N 只。不损失信息，只决定先买谁。

评价口径：
  excess_pool  该子集 10 日收益 − 同池全部股票日的 10 日收益（"有没有超过随便买一只"）
  delta        该子集 10 日收益 − 同模型全部信号的 10 日收益（"这条过滤本身有没有用"）
并要求前半段/后半段子样本符号一致，否则视为噪音。
"""

import zlib
from statistics import mean

from backend.engine import STRATEGY, stop_reference
from tools.review.panel import flags, iter_panels, pool_ok, wilder

HORIZON = 10
HOLD = {"S1": STRATEGY["max_hold_s1"], "S2": STRATEGY["max_hold_s2"]}


# ---------------------------------------------------------------- 附加指标
def _ema(values, period):
    if not values:
        return []
    alpha = 2 / (period + 1)
    out = [values[0]]
    for value in values[1:]:
        out.append(out[-1] + alpha * (value - out[-1]))
    return out


def _sma(values, period):
    out, total = [], 0.0
    for i, value in enumerate(values):
        total += value
        if i >= period:
            total -= values[i - period]
        out.append(total / period if i >= period - 1 else None)
    return out


def _atr(panel, period):
    tr = []
    for i in range(1, panel.n):
        prev = panel.c[i - 1]
        tr.append(max(panel.h[i] - panel.l[i],
                      abs(panel.h[i] - prev),
                      abs(panel.l[i] - prev)))
    return [None] * period + list(wilder(tr, period))


def extra_indicators(panel):
    """只用 OHLCV 的经典指标：MACD、布林 %B、OBV 斜率、KDJ、ATR 压缩。"""
    closes, highs, lows, volumes = panel.c, panel.h, panel.l, panel.v
    n = panel.n

    dif = [a - b for a, b in zip(_ema(closes, 12), _ema(closes, 26))]
    dea = _ema(dif, 9)
    macd_hist = [d - e for d, e in zip(dif, dea)]

    ma20 = _sma(closes, 20)
    pct_b = [0.5] * n
    for t in range(19, n):
        window = closes[t - 19:t + 1]
        mid = ma20[t]
        std = (sum((x - mid) ** 2 for x in window) / 20) ** 0.5
        pct_b[t] = (closes[t] - (mid - 2 * std)) / (4 * std) if std else 0.5

    obv = [0.0]
    for t in range(1, n):
        if closes[t] > closes[t - 1]:
            obv.append(obv[-1] + volumes[t])
        elif closes[t] < closes[t - 1]:
            obv.append(obv[-1] - volumes[t])
        else:
            obv.append(obv[-1])
    obv_slope = [0.0] * n
    for t in range(20, n):
        base = mean(volumes[t - 19:t + 1]) * 20
        obv_slope[t] = (obv[t] - obv[t - 20]) / base if base else 0.0

    k_value = d_value = 50.0
    kdj_j = [50.0] * n
    for t in range(8, n):
        low9, high9 = min(lows[t - 8:t + 1]), max(highs[t - 8:t + 1])
        span = high9 - low9
        rsv = 50.0 if span == 0 else (closes[t] - low9) / span * 100
        k_value = k_value * 2 / 3 + rsv / 3
        d_value = d_value * 2 / 3 + k_value / 3
        kdj_j[t] = 3 * k_value - 2 * d_value

    atr60 = _atr(panel, 60)
    squeeze = [1.0] * n
    for t in range(60, n):
        if atr60[t]:
            squeeze[t] = panel.atr[t] / atr60[t]

    return {"macd_hist": macd_hist, "pct_b": pct_b, "obv_slope": obv_slope,
            "kdj_j": kdj_j, "squeeze": squeeze}


# ---------------------------------------------------------------- 市场状态
def market_context(snapshot):
    """等权指数与宽度：每天多大比例的可交易股票站在自己的 MA20 上方。"""
    ret_sum, above, count = {}, {}, {}
    for panel in iter_panels(snapshot):
        for t in range(59, panel.n):
            if not pool_ok(panel, t):
                continue
            day = panel.dates[t]
            ret_sum[day] = ret_sum.get(day, 0.0) + panel.c[t] / panel.c[t - 1] - 1
            above[day] = above.get(day, 0) + (1 if panel.c[t] > panel.ma20[t] else 0)
            count[day] = count.get(day, 0) + 1
    dates = sorted(count)
    level, series = 1.0, []
    for day in dates:
        level *= 1 + ret_sum[day] / count[day]
        series.append(level)
    ma20 = _sma(series, 20)
    return {
        day: {
            "breadth": above[day] / count[day],
            "index_above_ma20": bool(ma20[i] is not None and series[i] > ma20[i]),
            "index_ret20": (series[i] / series[i - 20] - 1) if i >= 20 else None,
        }
        for i, day in enumerate(dates)
    }


# ---------------------------------------------------------------- 收集信号
class Signal:
    __slots__ = ("code", "date", "model", "fwd", "target_first",
                 "adx", "di_up", "rsi", "atr_distance", "atr_pct", "volume_ratio",
                 "return20", "amount20", "macd_hist", "pct_b", "obv_slope", "kdj_j",
                 "squeeze", "fresh", "gap", "amplitude", "breadth", "index_up",
                 "rs_rank", "vol_rank", "liq_rank")


def collect_signals(snapshot, context):
    """把每个信号日压成一个对象；顺带给出 10 日前向收益与 2R 竞速结果。

    返回 (signals, pool_mean)：pool_mean[日期] 是当天池内全部股票日的平均 10 日收益，
    用来做"同日匹配"的超额——否则把不同日期的收益混在一起平均，会混入行情阶段差异。
    """
    signals = []
    pool_sum, pool_cnt = {}, {}
    for panel in iter_panels(snapshot):
        extra = extra_indicators(panel)
        for t in range(59, panel.n - HORIZON):
            if not pool_ok(panel, t):
                continue
            f = panel.at(t)
            if f is None:
                continue
            day = panel.dates[t]
            pool_fwd = panel.c[t + HORIZON] / panel.c[t] - 1
            pool_sum[day] = pool_sum.get(day, 0.0) + pool_fwd
            pool_cnt[day] = pool_cnt.get(day, 0) + 1
            s1, s2 = flags(f)
            if not (s1 or s2):
                continue
            close, atr = panel.c[t], panel.atr[t]
            stop = stop_reference(close, atr)
            if stop <= 0 or stop >= close:
                continue
            target = close + 2 * (close - stop)
            fwd = panel.c[t + HORIZON] / close - 1
            ctx = context.get(panel.dates[t], {})
            amplitude = (panel.h[t] - panel.l[t]) / close if close else 0.0
            fresh = 1
            for back in range(1, 6):
                if t - back < 20:
                    break
                if panel.c[t - back] > panel.ph20[t - back]:
                    fresh = 0
                    break
            for model, on in (("S1", s1), ("S2", s2)):
                if not on:
                    continue
                # 2R 与止损谁先到（按该模型自己的最长持有窗口）
                end = min(t + HOLD[model], panel.n - 1)
                target_first = 0
                for d in range(1, end - t + 1):
                    if panel.l[t + d] <= stop:
                        break
                    if panel.h[t + d] >= target:
                        target_first = 1
                        break
                record = Signal()
                record.code = panel.code
                record.date = panel.dates[t]
                record.model = model
                record.fwd = fwd
                record.target_first = target_first
                record.adx = f["adx"]
                record.di_up = f["plus_di"] > f["minus_di"]
                record.rsi = f["rsi"]
                record.atr_distance = f["atr_distance"]
                record.atr_pct = atr / close if close else 0.0
                record.volume_ratio = f["volume_ratio"]
                record.return20 = f["return20"]
                record.amount20 = f["amount20"]
                record.macd_hist = extra["macd_hist"][t]
                record.pct_b = extra["pct_b"][t]
                record.obv_slope = extra["obv_slope"][t]
                record.kdj_j = extra["kdj_j"][t]
                record.squeeze = extra["squeeze"][t]
                record.fresh = fresh
                record.gap = panel.o[t] / panel.c[t - 1] - 1
                record.amplitude = amplitude
                record.breadth = ctx.get("breadth", 0.0)
                record.index_up = 1 if ctx.get("index_above_ma20") else 0
                record.rs_rank = record.vol_rank = record.liq_rank = 0.0
                signals.append(record)
    pool_mean = {day: pool_sum[day] / pool_cnt[day] for day in pool_sum}
    _assign_ranks(signals)
    return signals, pool_mean


def matched_excess(records, pool_mean):
    """同日匹配的超额：每条记录减去"当天池内均值"，再取平均。"""
    if not records:
        return None
    values = [r.fwd - pool_mean.get(r.date, 0.0) for r in records]
    return mean(values)


def _assign_ranks(signals):
    """按"同一天、同模型"做横截面分位，避免用绝对值阈值。"""
    buckets = {}
    for s in signals:
        buckets.setdefault((s.date, s.model), []).append(s)
    for same in buckets.values():
        for field, attr in (("return20", "rs_rank"), ("atr_pct", "vol_rank"), ("amount20", "liq_rank")):
            ordered = sorted(same, key=lambda x: getattr(x, field))
            last = max(1, len(ordered) - 1)
            for i, s in enumerate(ordered):
                setattr(s, attr, i / last)


# ---------------------------------------------------------------- 评价
def _same_day_delta(rows, buckets, predicate):
    """同日对照：把"选中的信号"与"当天其余信号"比，剥离市场择时的影响。

    市场状态类过滤会改变"哪些天在交易"，如果只看它与全体信号的差，这个差里混着
    "那几天大盘好"的成分。只有同日比较才是纯粹的选股/择时分离后的效应。
    """
    total_weight = 0
    weighted = 0.0
    for day, same in buckets.items():
        kept = [r for r in same if predicate(r)]
        if not kept:
            continue
        diff = mean(r.fwd for r in kept) - mean(r.fwd for r in same)
        weighted += diff * len(kept)
        total_weight += len(kept)
    return (weighted / total_weight) if total_weight else None


def evaluate(signals, pool_mean):
    report = {}
    for model in ("S1", "S2"):
        rows = [s for s in signals if s.model == model]
        if not rows:
            continue
        cutoff = sorted(r.date for r in rows)[len(rows) // 2]
        buckets = {}
        for r in rows:
            buckets.setdefault(r.date, []).append(r)
        base = mean(r.fwd for r in rows)
        base1 = mean(r.fwd for r in rows if r.date < cutoff)
        base2 = mean(r.fwd for r in rows if r.date >= cutoff)

        filters = []
        for name, predicate in _filters(model):
            kept = [r for r in rows if predicate(r)]
            if not kept:
                continue
            kept1 = [r for r in kept if r.date < cutoff]
            kept2 = [r for r in kept if r.date >= cutoff]
            same_day = _same_day_delta(rows, buckets, predicate)
            same_day1 = _same_day_delta(rows, {d: v for d, v in buckets.items() if d < cutoff}, predicate)
            same_day2 = _same_day_delta(rows, {d: v for d, v in buckets.items() if d >= cutoff}, predicate)
            filters.append({
                "name": name,
                "kept": len(kept),
                "keep_pct": round(100 * len(kept) / len(rows), 1),
                "excess_pool": round(matched_excess(kept, pool_mean), 5),
                "delta": round(mean(r.fwd for r in kept) - base, 5),
                "same_day": round(same_day, 5) if same_day is not None else None,
                "win_rate": round(sum(1 for r in kept if r.fwd > 0) / len(kept), 4),
                "delta_h1": round(mean(r.fwd for r in kept1) - base1, 5) if kept1 else None,
                "delta_h2": round(mean(r.fwd for r in kept2) - base2, 5) if kept2 else None,
                "same_day_h1": round(same_day1, 5) if same_day1 is not None else None,
                "same_day_h2": round(same_day2, 5) if same_day2 is not None else None,
                "target_first_pct": round(100 * sum(r.target_first for r in kept) / len(kept), 1),
            })
        filters.sort(key=lambda x: -x["delta"])

        rankings = []
        for name, key, reverse in _rankings():
            picks = _top_n_per_date(rows, key, reverse, n=3)
            if not picks:
                continue
            picks1 = [r for r in picks if r.date < cutoff]
            picks2 = [r for r in picks if r.date >= cutoff]
            rankings.append({
                "name": name,
                "picks": len(picks),
                "excess_pool": round(matched_excess(picks, pool_mean), 5),
                "delta": round(mean(r.fwd for r in picks) - base, 5),
                "win_rate": round(sum(1 for r in picks if r.fwd > 0) / len(picks), 4),
                "delta_h1": round(mean(r.fwd for r in picks1) - base1, 5) if picks1 else None,
                "delta_h2": round(mean(r.fwd for r in picks2) - base2, 5) if picks2 else None,
            })
        rankings.sort(key=lambda x: -x["delta"])

        designs = []
        for name, predicate, top in _designs(model):
            universe = [r for r in rows if predicate(r)]
            picks = _top_n_per_date(universe, lambda r: r.amount20, True, n=top)
            if not picks:
                continue
            days = len({r.date for r in picks})
            picks1 = [r for r in picks if r.date < cutoff]
            picks2 = [r for r in picks if r.date >= cutoff]
            designs.append({
                "name": name,
                "days": days,
                "picks": len(picks),
                "per_day": round(len(picks) / days, 1) if days else None,
                "mean": round(mean(r.fwd for r in picks), 5),
                "excess_pool": round(matched_excess(picks, pool_mean), 5),
                "win_rate": round(sum(1 for r in picks if r.fwd > 0) / len(picks), 4),
                "delta_h1": round(mean(r.fwd for r in picks1) - base1, 5) if picks1 else None,
                "delta_h2": round(mean(r.fwd for r in picks2) - base2, 5) if picks2 else None,
            })
        designs.sort(key=lambda x: -x["excess_pool"])

        sessions = len({r.date for r in rows})
        report[model] = {
            "signals": len(rows),
            "sessions": sessions,
            "per_session": round(len(rows) / sessions, 1),
            "signal_mean": round(base, 5),
            "excess_pool": round(matched_excess(rows, pool_mean), 5),
            "filters": filters,
            "rankings": rankings,
            "designs": designs,
        }
    return report


def _designs(model):
    """把"过滤 + 排序 + 每日取前 N"组成可执行方案，直接看结果。"""
    if model == "S1":
        return [
            ("不过滤，按成交额取前3", lambda r: True, 3),
            ("宽度≥50% 后取前3", lambda r: r.breadth >= 0.5, 3),
            ("指数>MA20 后取前3", lambda r: bool(r.index_up), 3),
            ("首次突破+宽度≥50% 后取前3", lambda r: r.fresh == 1 and r.breadth >= 0.5, 3),
            ("宽度≥50%+RS前30% 后取前3", lambda r: r.breadth >= 0.5 and r.rs_rank >= 0.7, 3),
            ("宽度≥50% 后取前5", lambda r: r.breadth >= 0.5, 5),
        ]
    return [
        ("不过滤，按成交额取前3", lambda r: True, 3),
        ("宽度≥50% 后取前3", lambda r: r.breadth >= 0.5, 3),
        ("指数>MA20 后取前3", lambda r: bool(r.index_up), 3),
        ("宽度≥50%+RS前30% 后取前3", lambda r: r.breadth >= 0.5 and r.rs_rank >= 0.7, 3),
        ("宽度≥50% 后取前5", lambda r: r.breadth >= 0.5, 5),
    ]


def _filters(model):
    common = [
        ("ADX≥25 且 +DI>−DI", lambda r: r.adx >= 25 and r.di_up),
        ("MACD 柱>0", lambda r: r.macd_hist > 0),
        ("布林 %B>0.5", lambda r: r.pct_b > 0.5),
        ("OBV 20日斜率>0", lambda r: r.obv_slope > 0),
        ("KDJ J<80（不过热）", lambda r: r.kdj_j < 80),
        ("RSI≤70", lambda r: r.rsi <= 70),
        ("波动压缩 ATR14/ATR60<0.9", lambda r: r.squeeze < 0.9),
        ("距 MA20 ≤2ATR（不追高）", lambda r: r.atr_distance <= 2),
        ("低波动：ATR% 同类后 50%", lambda r: r.vol_rank <= 0.5),
        ("相对强度：同类前 30%", lambda r: r.rs_rank >= 0.7),
        ("相对强度：同类前 10%", lambda r: r.rs_rank >= 0.9),
        ("流动性：成交额同类前 50%", lambda r: r.liq_rank >= 0.5),
        ("市场宽度≥50%", lambda r: r.breadth >= 0.5),
        ("等权指数 > MA20", lambda r: bool(r.index_up)),
        ("信号日跳空 ≤1%", lambda r: r.gap <= 0.01),
        ("信号日振幅 ≤1.5×ATR", lambda r: r.amplitude <= 1.5 * r.atr_pct),
    ]
    if model == "S1":
        return common + [
            ("量比≥1.5（放量确认）", lambda r: r.volume_ratio >= 1.5),
            ("首次突破（前5日无突破）", lambda r: r.fresh == 1),
            ("首次突破 + 宽度≥50%", lambda r: r.fresh == 1 and r.breadth >= 0.5),
            ("首次突破 + 相对强度前30%", lambda r: r.fresh == 1 and r.rs_rank >= 0.7),
            ("宽度≥50% + 相对强度前30%", lambda r: r.breadth >= 0.5 and r.rs_rank >= 0.7),
        ]
    return common + [
        ("20日收益>0（趋势确认）", lambda r: r.return20 > 0),
        ("宽度≥50% + 相对强度前30%", lambda r: r.breadth >= 0.5 and r.rs_rank >= 0.7),
        ("距 MA20 ≥0.3ATR（别贴太近）", lambda r: r.atr_distance >= 0.3),
        ("MACD 柱>0 + 相对强度前30%", lambda r: r.macd_hist > 0 and r.rs_rank >= 0.7),
    ]


def _stable_hash(record):
    return (zlib.crc32(f"{record.date}|{record.model}|{record.code}".encode()) & 0xFFFF) / 0xFFFF


def _composite(record):
    """把"趋势强、不追高、量能配合、相对强度高"揉成一个 0~2 的分。"""
    return (record.rs_rank
            + (record.adx / 50.0) * 0.5
            + min(record.volume_ratio / 3.0, 1.0) * 0.5
            - min(abs(record.atr_distance) / 3.0, 1.0) * 0.5)


def _rankings():
    return [
        ("随机（对照）", _stable_hash, True),
        ("相对强度 RS20 高→低", lambda r: r.return20, True),
        ("量比 高→低", lambda r: r.volume_ratio, True),
        ("ADX 高→低", lambda r: r.adx, True),
        ("距 MA20 近→远", lambda r: abs(r.atr_distance), False),
        ("ATR% 低→高", lambda r: r.atr_pct, False),
        ("成交额 高→低", lambda r: r.amount20, True),
        ("MACD 柱 高→低", lambda r: r.macd_hist, True),
        ("综合分（RS+ADX+量比−延伸）", _composite, True),
    ]


def _top_n_per_date(rows, key, reverse, n=3):
    buckets = {}
    for r in rows:
        buckets.setdefault(r.date, []).append(r)
    picks = []
    for same_day in buckets.values():
        picks.extend(sorted(same_day, key=key, reverse=reverse)[:n])
    return picks
