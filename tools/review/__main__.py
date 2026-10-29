"""命令行入口：python -m tools.review [snapshot|study|sensitivity|refine|backtest|all]

只读：不联网、不改持仓、不写账本。结果同时打印和写入 data/review/*.json。
"""

import argparse
import json
import sys
from pathlib import Path

from backend.data import load
from tools.review import panel as panel_mod
from tools.review import quality, refine, study

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_OUT = ROOT / "data" / "review"


# ---------------------------------------------------------------- 打印辅助
def line(title):
    print(f"\n{'=' * 72}\n{title}\n{'=' * 72}")


def pct(value, digits=2):
    return "—" if value is None else f"{100 * value:+.{digits}f}%"


def pct_abs(value, digits=2):
    return "—" if value is None else f"{100 * value:.{digits}f}%"


def table(rows, headers):
    widths = [max(len(str(h)), *(len(str(r[i])) for r in rows)) for i, h in enumerate(headers)]
    fmt = "  ".join("{:<%d}" % w for w in widths)
    print(fmt.format(*headers))
    print("  ".join("-" * w for w in widths))
    for row in rows:
        print(fmt.format(*[str(c) for c in row]))


def save(name, payload, out_dir):
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"{name}.json"
    path.write_text(json.dumps(payload, indent=1, ensure_ascii=False), encoding="utf-8")
    print(f"\n[已写入] {path.relative_to(ROOT)}")


def positions_for(mode, use_store):
    if not use_store:
        return []
    from backend.store import Store
    return Store().positions(mode)


# ---------------------------------------------------------------- 各命令
def cmd_snapshot(snapshot, args, out_dir):
    line("01 快照覆盖度：as_of 是否真的对全市场成立")
    cov = quality.coverage(snapshot)
    print(f"  as_of={cov['as_of']}  provider={cov['provider']}  复权口径={cov['adjust']}")
    print(f"  抓取时间 fetched_at={cov['fetched_at']}")
    print(f"  股票数={cov['stocks']}  以 as_of 为最后交易日的股票={cov['covered_by_as_of']}"
          f"  覆盖率={pct_abs(cov['coverage_ratio'])}")
    print(f"  出现最多的最后交易日={cov['modal_last_date']}（{cov['modal_count']} 只）")
    print("  最后交易日分布：" + "，".join(f"{d}:{c}" for d, c in cov["last_date_distribution"]))
    if cov["coverage_ratio"] < 0.95:
        print("\n  【警告】as_of 只覆盖了少数股票。daily() 会把其余股票判为"
              "\"停牌或个股行情过期\"，\n"
              "         股票池会被静默压缩，新版会由 freshness() 报警。")
        print("         建议：同步后按覆盖率决定 as_of（覆盖不足就回退到上一交易日，"
              "或把 complete 置 False）。")

    line("02 不复权价格的可检测污染")
    dq = quality.data_quality(snapshot)
    print(f"  交易日={dq['sessions']}  区间={dq['span'][0]}..{dq['span'][-1]}")
    print(f"  超过涨跌停幅度的单日跳空={dq['impossible_moves']} 次，"
          f"涉及 {dq['affected_stocks']} 只（{pct_abs(dq['affected_ratio'])}），"
          f"其中向下 {dq['down_moves']} 次、幅度>30% 的 {dq['moves_over_30pct']} 次")
    if dq["worst_examples"]:
        worst = "，".join(f"{g['code']}@{g['date']}({pct(g['ret'], 1)})" for g in dq["worst_examples"][:3])
        print(f"  最极端：{worst}")
    print(f"  停牌缺口={dq['suspension_gaps']} 处（最长 {dq['longest_gap_sessions']} 个交易日）"
          f"  缺失股票日={dq['missing_stock_days']}")
    print("  说明：不复权下这些跳空会污染 MA/ATR/唐奇安通道，并制造假止损。"
          "信号与回测应改用前复权。")

    line("03 日报现在会给出什么")
    positions = positions_for(args.mode, not args.no_positions)
    dash = quality.dashboard(snapshot, positions, args.capital, args.strict, args.adx)
    print(f"  持仓={dash['holdings']} 只  策略版本={dash['strategy']}  严格复筛={'开' if args.strict else '关'}"
          f"  振幅/弱市过滤=开（与页面一致）")
    market = dash.get("market") or {}
    if market:
        print(f"  市场状态={market.get('regime')}  宽度={pct_abs(market.get('breadth'))}"
              f"（{market.get('samples')}/{market.get('total')} 只）"
              f"  等权指数{'在' if market.get('index_above_ma20') else '低于'}MA20"
              + ("" if market.get('trustworthy') else "  [样本不足一半，未据此拦截]"))
    print(f"  股票池 pool_count={dash['pool_count']}  "
          f"（不看 as_of、只按今日可交易判断则为 {quality.pool_if_ignoring_asof(snapshot)} 只）")
    stats = dash.get("signal_stats") or {}
    by_model = dash.get("candidates_by_model") or {}
    print(f"  命中={dash['candidates_total']} 条"
          f"（S1 {by_model.get('S1', 0)} / S2 {by_model.get('S2', 0)}）"
          f" → 显示前 {dash['candidate_limit']} 条（另有 {dash['candidates_overflow']} 条备查）")
    print(f"  振幅剔除={stats.get('rejected_day_range', 0)} 条"
          f"（{stats.get('rejected_by_model') or {}}）"
          f"  振幅上限={stats.get('amplitude_limits') or {}}"
          f"  可下单={dash['candidates_with_quantity']} 只  "
          f"合计名义={dash['notional']:,.0f} 元（{pct_abs(dash['notional_pct'])}）")
    if dash["excluded_reasons"]:
        print("  被排除原因：" + "；".join(f"{r} {c} 只" for r, c in dash["excluded_reasons"]))
    if dash["warnings"]:
        print("  warnings：" + " / ".join(dash["warnings"]))
    if dash["chosen"]:
        rows = [[c["code"], c["model"], c["quantity"], f"{100 * c['risk_pct']:.2f}%"] for c in dash["chosen"]]
        print()
        table(rows, ("代码", "模型", "参考数量", "单笔风险占权益"))
        risks = [c["risk_pct"] for c in dash["chosen"]]
        print(f"\n  单笔风险：最大 {100 * max(risks):.2f}%、最小 {100 * min(risks):.2f}%"
              f"（目标 {100 * 0.01:.2f}%）")
        print("  新版按20日成交额降序分配，非预期收益排名；低于最小计划风险预算会跳过。")

    line("04 指标快算层自检")
    check = panel_mod.selfcheck(snapshot, trials=args.selfcheck)
    print(f"  与 engine.features 对比 {check['pairs']} 个 (股票, 日) 样本，"
          f"最大相对误差={check['max_rel_diff']:.3g}")
    session = cov["as_of"] if cov["coverage_ratio"] >= 0.95 else cov["modal_last_date"]
    agree = panel_mod.signal_agreement(snapshot, as_of=session)
    print(f"  与 engine.signals 在 {session} 上逐股对比：一致 {agree['agree']}、"
          f"不一致 {agree['disagree']}")
    if agree["examples"]:
        print(f"  不一致样本（通常是浮点边界平局）：{agree['examples'][:3]}")
    payload = {"coverage": cov, "data_quality": dq, "dashboard": dash,
               "selfcheck": check, "signal_agreement": agree}
    save("snapshot", payload, out_dir)
    return payload


def cmd_study(snapshot, args, out_dir):
    line("信号事件研究：信号之后到底发生了什么")
    result = study.event_study(snapshot)
    base = result["baseline"]
    print(f"  池内股票日={result['pool_days']:,}（同池基准已包含大盘涨跌）")
    print("  基准前向收益：" + "  ".join(f"{h}日 {pct(base[str(h)]['mean'])}" for h in (1, 5, 10, 20)))
    sess = result["per_session"]
    if sess:
        print(f"  信号密度：平均每个交易日 {sess['mean_per_session']:.0f} 条"
              f"（{sess['sessions_scanned']} 个交易日），"
              f"有信号时中位 {sess['median']}、p90 {sess['p90']}、最大 {sess['max']}")
    rows = []
    for tag, label in (("S1", "S1 突破"), ("S2", "S2 回调"), ("S1s", "S1+严格"), ("S2s", "S2+严格")):
        ev = result["events"][tag]
        if not ev["n"]:
            continue
        row = [label, f"{ev['n']:,}", pct(ev["fwd"]["10"]["mean"]), pct(ev["excess"]["10"]),
               pct_abs(ev["fwd"]["10"]["win_rate"], 1)]
        race = ev["two_r_race"]
        row += [f"{race['target_first_pct']:.1f}%", f"{race['stop_first_pct']:.1f}%"]
        flow = ev["exit_flow"] or {}
        row += [f"{flow.get('STOP', 0):.0f}/{flow.get('MA20', 0):.0f}/{flow.get('TIME', 0):.0f}"]
        gap = ev["gap_split"]
        row += [f"{gap['executable_share_pct']:.1f}%" if gap["executable_share_pct"] is not None else "—",
                pct(gap["fwd10_executable"]) if gap["fwd10_executable"] is not None else "—",
                pct(gap["fwd10_skipped"]) if gap["fwd10_skipped"] is not None else "—"]
        rows.append(row)
    print()
    table(rows, ("模型", "信号日", "10日均值", "10日超额", "胜率",
                 "先到2R", "先到止损", "止损/破线/到期%", "≤+1%占比", "其10日", "被跳过10日"))
    print("\n  解读：超额为负 = 该信号不如「随便在同池买一只」。2R 命中率远低于止损命中率时，"
          "触价次数比不能视为实际盈亏比，新版界面不展示预测止盈。")
    save("study", result, out_dir)
    return result


def cmd_sensitivity(snapshot, args, out_dir):
    line("S1 参数敏感性：唐奇安窗口 × 量比阈值（10 日超额）")
    result = study.sensitivity(snapshot, max_stocks=args.stocks)
    grid = result["grid"]
    windows = sorted({int(k.split("_r")[0][1:]) for k in grid})
    ratios = sorted({float(k.split("_r")[1]) for k in grid})
    print(f"  样本股票={result['stocks']}  池内股票日={result['pool_days']:,}  "
          f"基准10日均值={pct(result['baseline_mean'])}")
    rows = []
    for w in windows:
        cells = []
        for r in ratios:
            cell = grid[f"w{w}_r{r}"]
            cells.append("—" if cell["excess"] is None else f"{100 * cell['excess']:+.2f}")
        rows.append([w] + cells)
    table(rows, ("窗口", *[f"量比{r}" for r in ratios]))
    best = max((c for c in grid.values() if c["excess"] is not None), key=lambda c: c["excess"])
    print(f"\n  全部格子中最高的 10 日超额 = {pct(best['excess'])}（信号 {best['signals']:,} 条）")
    print("  若最高值仍接近 0 或为负，说明问题不在参数，而在数据口径与退出规则。")
    save("sensitivity", result, out_dir)
    return result


def cmd_backtest(snapshot, args, out_dir):
    line("真实引擎抽样回测（engine.backtest，未改写）")
    stocks = args.stocks or 150
    print(f"  抽样 {stocks} 只，单只约 0.4 秒（engine 逐日重算指标，O(n²)）…", flush=True)

    def progress(model, done, total):
        print(f"    {model} {done}/{total}", flush=True)

    result = study.sample_backtest(snapshot, max_stocks=stocks, progress=progress)
    print(f"  抽样股票={result['stocks']} 只；{result['universe_note']}")
    rows = []
    for model, data in result["models"].items():
        trade = data["trade_return"]
        if not trade:
            continue
        rows.append([model, f"{data['trades']:,}", pct(trade["mean"]), pct(trade["median"]),
                     pct_abs(trade["win_rate"], 1),
                     f"{data['holding_days']['median']}",
                     pct(data["total_return"]["median"])])
    table(rows, ("模型", "成交笔数", "每笔均值", "每笔中位", "胜率", "持仓中位(日)", "单股累计中位"))
    hold = result["buy_and_hold"]
    print(f"\n  同期同样本买入持有：中位 {pct(hold['median'])}、均值 {pct(hold['mean'])}")
    print("  单股回测有20%单股上限；不能代替含70%组合上限及板块约束的组合回测。")
    save("backtest", result, out_dir)
    return result


def cmd_refine(snapshot, args, out_dir):
    line("候选太多怎么办：硬过滤 vs 排序，逐个量化")
    print("  先算市场状态，再收集信号（约 1 分钟）…", flush=True)
    context = refine.market_context(snapshot)
    signals, pool_mean = refine.collect_signals(snapshot, context)
    base_mean = sum(pool_mean.values()) / len(pool_mean) if pool_mean else 0.0
    print(f"  基准（同日匹配）10 日收益均值 = {pct(base_mean)}；共收集信号 {len(signals):,} 条", flush=True)
    report = refine.evaluate(signals, pool_mean)

    for model, label in (("S1", "S1 突破"), ("S2", "S2 回调")):
        data = report.get(model)
        if not data:
            continue
        print(f"\n{'-' * 72}")
        print(f"{label}：{data['signals']:,} 条 / {data['sessions']} 个交易日"
              f"（{data['per_session']} 条/日）  信号均值 {pct(data['signal_mean'])}"
              f"  相对池 {pct(data['excess_pool'])}")

        rows = []
        for item in data["filters"]:
            rows.append([item["name"], f"{item['kept']:,}", f"{item['keep_pct']:.0f}%",
                         f"{item['kept'] / data['sessions']:.1f}",
                         pct(item["excess_pool"]), pct(item["delta"]),
                         pct(item["same_day"]), pct_abs(item["win_rate"], 1),
                         pct(item["delta_h1"]), pct(item["delta_h2"]),
                         f"{item['target_first_pct']:.0f}%"])
        print("\n  【硬过滤】保留子集的表现")
        print("    delta  = 相对全部信号的变化（含市场择时，可能是「那几天大盘好」）")
        print("    同日   = 与「当天其余信号」比较（剥离择时，纯选股效应）")
        table(rows, ("过滤条件", "保留", "占比", "剩/日", "超额(池)", "delta", "同日",
                     "胜率", "前段", "后段", "先到2R"))

        rows = []
        for item in data["rankings"]:
            rows.append([item["name"], f"{item['picks']:,}", pct(item["excess_pool"]),
                         pct(item["delta"]), pct_abs(item["win_rate"], 1),
                         pct(item["delta_h1"]), pct(item["delta_h2"])])
        print("\n  【排序】每天只买前 3 只（不删信号，只决定先买谁；每天都有 3 只，故无择时偏差）")
        table(rows, ("排序键", "笔数", "超额(池)", "delta", "胜率", "前段", "后段"))

        rows = []
        for item in data["designs"]:
            rows.append([item["name"], item["days"], f"{item['per_day']}",
                         pct(item["mean"]), pct(item["excess_pool"]),
                         pct_abs(item["win_rate"], 1),
                         pct(item["delta_h1"]), pct(item["delta_h2"])])
        print("\n  【组合方案】先过滤再按成交额取前 N（可执行的完整规则）")
        table(rows, ("方案", "交易日", "只/日", "10日均值", "超额(池)", "胜率", "前段", "后段"))

        timing = [f for f in data["filters"]
                  if f["delta"] and f["delta_h1"] and f["delta_h2"]
                  and f["delta_h1"] > 0 and f["delta_h2"] > 0 and f["keep_pct"] >= 10]
        picking = [f for f in data["filters"]
                   if f["same_day"] and f["same_day_h1"] and f["same_day_h2"]
                   and f["same_day_h1"] > 0 and f["same_day_h2"] > 0 and f["keep_pct"] >= 10]
        print(f"\n  两段都为正、且保留≥10%候选的过滤：")
        print("    含择时：" + ("、".join(f["name"] for f in timing) if timing else "（没有）"))
        print("    纯选股：" + ("、".join(f["name"] for f in picking) if picking else "（没有）"))

    save("refine", report, out_dir)
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(
        prog="python -m tools.review",
        description="A-Quant V1 策略评审工具（只读：不联网、不改持仓、不下单）",
    )
    parser.add_argument("command", nargs="?", default="snapshot",
                        choices=["snapshot", "study", "sensitivity", "refine", "backtest", "all"],
                        help="snapshot=快照体检（默认）；study=事件研究；"
                             "sensitivity=参数网格；refine=候选过滤/排序实验；"
                             "backtest=抽样回测；all=全部（不含 refine）")
    parser.add_argument("--mode", choices=["live", "demo"], default="live", help="读取哪个行情（默认 live）")
    parser.add_argument("--capital", type=float, default=100000, help="账户总权益，用于仓位检查")
    parser.add_argument("--stocks", type=int, default=None, help="sensitivity/backtest 的抽样股票数")
    parser.add_argument("--strict", action="store_true", default=True, help="快照体检时开启严格复筛（默认开）")
    parser.add_argument("--no-strict", dest="strict", action="store_false", help="关闭严格复筛")
    parser.add_argument("--adx", action="store_true", help="S1 叠加 ADX 过滤")
    parser.add_argument("--no-positions", action="store_true", help="不读取账本持仓")
    parser.add_argument("--selfcheck", type=int, default=200, help="快算层自检抽样次数")
    parser.add_argument("--out", default=str(DEFAULT_OUT), help="JSON 输出目录（默认 data/review）")
    args = parser.parse_args(argv)
    out_dir = Path(args.out)

    try:
        snapshot = load(args.mode)
    except (ValueError, OSError) as exc:
        print(f"读取行情失败：{exc}", file=sys.stderr)
        return 2

    print(f"A-Quant 策略评审 · mode={args.mode} · 数据截至 {snapshot.get('as_of')}")
    if args.command in ("snapshot", "all"):
        cmd_snapshot(snapshot, args, out_dir)
    if args.command in ("study", "all"):
        cmd_study(snapshot, args, out_dir)
    if args.command in ("sensitivity", "all"):
        cmd_sensitivity(snapshot, args, out_dir)
    if args.command == "refine":
        cmd_refine(snapshot, args, out_dir)
    if args.command in ("backtest", "all"):
        cmd_backtest(snapshot, args, out_dir)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
