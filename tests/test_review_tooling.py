"""评审工具的正确性测试：快算层必须与引擎逐字段、逐信号一致。

全部用示例行情（data/demo.json，9 只 × 200 根），不需要正式行情，几秒内跑完。
"""

import unittest

from backend.data import demo
from backend.engine import allowed, features, signals
from tools.review import quality, refine, study
from tools.review.panel import Panel, flags, pool_ok, selfcheck, signal_agreement


class PanelParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = demo()

    def test_every_day_matches_engine_features(self):
        """每一只股票、每一个可用交易日，Panel.at 都要等于 features(bars[:t+1])。"""
        checked = 0
        for stock in self.snapshot["stocks"]:
            bars = stock["bars"]
            if len(bars) < 61:
                continue
            panel = Panel(stock)
            for t in range(59, len(bars)):
                fast = panel.at(t)
                if fast is None:
                    continue
                reference = features(bars[:t + 1])
                for key, expected in reference.items():
                    actual = fast[key]
                    if expected is None or actual is None:
                        self.assertEqual(expected, actual, f"{stock['code']} t={t} {key}")
                        continue
                    scale = max(1.0, abs(expected))
                    self.assertLess(abs(expected - actual) / scale, 1e-9,
                                    f"{stock['code']} t={t} {key}: {expected} != {actual}")
                checked += 1
        self.assertGreater(checked, 500, "示例行情应覆盖足够多的股票日")

    def test_signals_match_engine_every_day(self):
        """信号判定必须与 engine.signals 一致（浮点平局除外，比例 < 0.5%）。"""
        total = mismatch = 0
        for stock in self.snapshot["stocks"]:
            bars = stock["bars"]
            if len(bars) < 61 or not allowed(stock["code"], stock["name"]):
                continue
            panel = Panel(stock)
            for t in range(59, len(bars)):
                fast = panel.at(t)
                if fast is None:
                    continue
                ours = {m for m, on in zip(("S1", "S2"), flags(fast)) if on}
                theirs = {m for m, _ in signals(bars[:t + 1])}
                total += 1
                if ours != theirs:
                    mismatch += 1
        self.assertGreater(total, 100)
        self.assertLessEqual(mismatch / total, 0.005,
                             f"{mismatch}/{total} 个股票日信号不一致")

    def test_selfcheck_reports_tiny_error(self):
        result = selfcheck(self.snapshot, trials=60)
        self.assertGreater(result["pairs"], 20)
        self.assertLess(result["max_rel_diff"], 1e-9)

    def test_signal_agreement_on_single_session(self):
        result = signal_agreement(self.snapshot, as_of=self.snapshot["as_of"])
        self.assertGreater(result["agree"], 0)
        self.assertLessEqual(result["disagree"], max(1, result["agree"] // 200))


class ReviewCommandTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = demo()

    def test_pool_filter_needs_volume_and_liquidity(self):
        for stock in self.snapshot["stocks"]:
            if stock["code"] != "600001":
                continue
            panel = Panel(stock)
            self.assertTrue(pool_ok(panel, panel.n - 1))
            self.assertFalse(pool_ok(panel, 10), "少于 60 根历史不应入池")

    def test_event_study_shape_and_baseline(self):
        result = study.event_study(self.snapshot)
        self.assertIn("10", result["baseline"])
        self.assertGreater(result["pool_days"], 0)
        for tag in ("S1", "S2"):
            event = result["events"][tag]
            self.assertIn("excess", event)
            self.assertIn("two_r_race", event)
            if event["n"]:
                self.assertIsNotNone(event["excess"]["10"])
                race = event["two_r_race"]
                self.assertAlmostEqual(
                    race["target_first_pct"] + race["stop_first_pct"] + race["neither_pct"], 100.0, places=1)

    def test_sensitivity_grid_is_complete(self):
        result = study.sensitivity(self.snapshot, max_stocks=4, windows=(10, 20),
                                   ratios=(1.0, 2.0), horizon=5)
        self.assertEqual(len(result["grid"]), 4)
        for cell in result["grid"].values():
            self.assertIn("excess", cell)

    def test_coverage_counts_stale_stocks(self):
        result = quality.coverage(self.snapshot)
        self.assertEqual(result["stocks"], len(self.snapshot["stocks"]))
        self.assertLessEqual(result["coverage_ratio"], 1.0)
        self.assertEqual(result["covered_by_as_of"], result["modal_count"])

    def test_dashboard_blocks_demo_trading(self):
        result = quality.dashboard(self.snapshot, [], 100000)
        self.assertTrue(result["warnings"], "示例行情必须给出禁止买入的警告")
        self.assertEqual(result["candidates_with_quantity"], 0)
        self.assertGreater(result["pool_count"], 0)

    def test_price_anomaly_excluded_from_pool(self):
        """与 engine 一致：近 61 根出现超涨跌停跳空的股票当天不进池。"""
        stock = next(s for s in self.snapshot["stocks"] if s["code"] == "600001")
        panel = Panel(stock)
        self.assertTrue(pool_ok(panel, panel.n - 1))
        broken = {**stock, "bars": [dict(b) for b in stock["bars"]]}
        broken["bars"][-5]["close"] = broken["bars"][-6]["close"] * 0.5   # 假的除权跳空
        self.assertFalse(pool_ok(Panel(broken), panel.n - 1))

    def test_refine_reports_filters_rankings_designs(self):
        context = refine.market_context(self.snapshot)
        signals, pool_base = refine.collect_signals(self.snapshot, context)
        self.assertGreater(len(signals), 0)
        report = refine.evaluate(signals, pool_base)
        self.assertTrue(report, "示例行情应至少产生一个模型的信号")
        for model, data in report.items():
            self.assertGreaterEqual(len(data["filters"]), 10)
            self.assertGreaterEqual(len(data["rankings"]), 5)
            self.assertTrue(data["designs"])
            for item in data["filters"]:
                self.assertIn("same_day", item)
                self.assertGreater(item["kept"], 0)


if __name__ == "__main__":
    unittest.main()
