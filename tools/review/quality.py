"""快照体检：数据是否可信、股票池是否被静默掏空、日报现在会选出什么。"""

from collections import Counter

from backend.engine import daily, allowed, valid_bars
from tools.review.panel import Panel, pool_ok


def coverage(snapshot):
    """as_of 与个股实际最新交易日的覆盖度。

    这是最容易被忽略、后果最大的一项：`sync_live()` 用交易日历推断 expected，
    但供应商当天收盘后并不会立刻对全市场都生成当日 K 线。一旦 as_of 落在
    "只有少数股票有数据"的那一天，daily() 会把其余股票判为"停牌/过期"。
    """
    stocks = snapshot["stocks"]
    total = len(stocks)
    as_of = snapshot.get("as_of")
    last = Counter((s["bars"][-1]["date"] if s.get("bars") else "EMPTY") for s in stocks)
    first = Counter((s["bars"][0]["date"] if s.get("bars") else "EMPTY") for s in stocks)
    covered = last.get(as_of, 0)
    ratio = covered / total if total else 0.0
    modal, modal_count = (last.most_common(1)[0] if last else ("", 0))
    return {
        "as_of": as_of,
        "expected_session": snapshot.get("expected_session"),
        "fetched_at": snapshot.get("fetched_at"),
        "provider": snapshot.get("provider"),
        "adjust": snapshot.get("adjust"),
        "stocks": total,
        "covered_by_as_of": covered,
        "coverage_ratio": round(ratio, 4),
        "modal_last_date": modal,
        "modal_count": modal_count,
        "last_date_distribution": last.most_common(6),
        "first_date_distribution": first.most_common(3),
        "stale_stocks": total - covered - sum(c for d, c in last.items() if d > (as_of or "")),
        "ahead_of_as_of": sum(c for d, c in last.items() if d > (as_of or "")),
        "complete_flag": snapshot.get("complete"),
        "error_count": len(snapshot.get("errors") or []),
    }


def data_quality(snapshot):
    """不复权价格的可检测污染：超过涨跌停幅度的单日跳空 = 除权/送转或脏数据。"""
    stocks = snapshot["stocks"]
    calendar = sorted({b["date"] for s in stocks for b in s["bars"]})
    position = {d: i for i, d in enumerate(calendar)}
    gaps, suspension_gaps, missing_days = [], [], 0
    for stock in stocks:
        bars = stock["bars"]
        if not bars:
            continue
        code = stock["code"]
        missing_days += max(0, len(calendar) - len(bars))
        limit = 0.20 if code.startswith(("300", "301")) else 0.10
        closes = [b["close"] for b in bars]
        for i in range(1, len(closes)):
            if closes[i - 1] <= 0:
                continue
            ret = closes[i] / closes[i - 1] - 1
            if abs(ret) > limit + 0.011:
                gaps.append({"code": code, "date": bars[i]["date"], "ret": round(ret, 4)})
        indexes = [position[b["date"]] for b in bars]
        suspension_gaps.extend(b - a - 1 for a, b in zip(indexes, indexes[1:]) if b - a > 1)
    big = [g for g in gaps if abs(g["ret"]) > 0.30]
    return {
        "sessions": len(calendar),
        "span": [calendar[0], calendar[-1]] if calendar else [],
        "impossible_moves": len(gaps),
        "affected_stocks": len({g["code"] for g in gaps}),
        "affected_ratio": round(len({g["code"] for g in gaps}) / len(stocks), 4) if stocks else 0,
        "down_moves": sum(1 for g in gaps if g["ret"] < 0),
        "moves_over_30pct": len(big),
        "worst_examples": sorted(gaps, key=lambda g: g["ret"])[:5],
        "suspension_gaps": len(suspension_gaps),
        "longest_gap_sessions": max(suspension_gaps) if suspension_gaps else 0,
        "missing_stock_days": missing_days,
    }


def pool_if_ignoring_asof(snapshot):
    """不看 as_of、只看"今天这根 K 线是否可交易"时，池子有多大。

    与 daily() 的 pool_count 对比，可以量化"as_of 设错一天"造成的塌缩。
    """
    count = 0
    for stock in snapshot["stocks"]:
        if not allowed(stock["code"], stock["name"]):
            continue
        bars = stock.get("bars") or []
        if len(bars) < 61 or not valid_bars(bars):
            continue
        panel = Panel(stock)
        if pool_ok(panel, panel.n - 1):
            count += 1
    return count


def dashboard(snapshot, positions=(), capital=100000, strict_filter=True, adx_filter=False,
              amplitude_filter=True, market_filter=True, top_n=3, model_filter='ALL'):
    """跑一次和页面完全相同参数的 daily()，报告它会给出什么。"""
    report = daily(snapshot, list(positions), capital, adx_filter, strict_filter,
                   amplitude_filter=amplitude_filter, market_filter=market_filter, top_n=top_n,
                   model_filter=model_filter)
    candidates = report["candidates"]
    chosen = [c for c in candidates if c["suggested_quantity"]]
    reasons = Counter(e["reason"] for e in report["excluded"])
    risk_per_trade = [
        {"code": c["code"], "model": c["model"], "quantity": c["suggested_quantity"],
         "risk_pct": round(c["suggested_quantity"] * (c["reference_close"] - c["stop_reference"]) / capital, 4)}
        for c in chosen
    ]
    return {
        "warnings": report["warnings"],
        "market": report.get("market"),
        "signal_stats": report.get("signal_stats"),
        "pool_count": report["pool_count"],
        "excluded_total": len(report["excluded"]),
        "excluded_reasons": reasons.most_common(),
        "model_filter": report.get("model_filter"),
        "candidates_total": report.get("candidates_total", len(candidates)),
        "candidate_limit": report.get("candidate_limit"),
        "candidates": len(candidates),
        "candidates_overflow": len(report.get("candidates_overflow") or []),
        "candidates_by_model": report.get("candidates_by_model") or dict(Counter(c["model"] for c in candidates)),
        "candidates_with_quantity": len(chosen),
        "notional": round(sum(c["reference_close"] * c["suggested_quantity"] for c in chosen), 2),
        "notional_pct": round(sum(c["reference_close"] * c["suggested_quantity"] for c in chosen) / capital, 4),
        "chosen": risk_per_trade,
        "holdings": len(report["holdings"]),
        "exposure": report["risk"]["exposure"],
        "strategy": report["strategy"]["version"],
    }
