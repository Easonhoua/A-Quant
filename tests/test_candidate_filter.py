"""候选模型筛选（全部 / S1 / S2）与按模型区分的振幅上限。"""

import unittest

from backend.data import demo
from backend.engine import STRATEGY, daily


class ModelFilterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.snapshot = demo()

    def test_only_requested_model_is_returned(self):
        for model in ("S1", "S2"):
            report = daily(self.snapshot, [], 100000, model_filter=model, top_n=99)
            listed = report["candidates"] + report["candidates_overflow"]
            self.assertTrue(all(c["model"] == model for c in listed))
            self.assertEqual(report["model_filter"], model)

    def test_all_is_the_union_of_both_models(self):
        everything = daily(self.snapshot, [], 100000, top_n=99)
        s1 = daily(self.snapshot, [], 100000, top_n=99, model_filter="S1")
        s2 = daily(self.snapshot, [], 100000, top_n=99, model_filter="S2")
        self.assertEqual(everything["candidates_total"], s1["candidates_total"] + s2["candidates_total"])
        self.assertEqual(everything["candidates_by_model"].get("S1", 0), s1["candidates_total"])

    def test_unknown_model_filter_is_rejected(self):
        with self.assertRaises(ValueError):
            daily(self.snapshot, [], 100000, model_filter="S3")

    def test_amplitude_limits_differ_per_model(self):
        limits = STRATEGY["max_day_range_atr"]
        # 突破当天天然是大振幅，S1 的上限必须比 S2 宽，否则 S1 会被全部剔除
        self.assertGreater(limits["S1"], limits["S2"])
        report = daily(self.snapshot, [], 100000, amplitude_filter=True, top_n=99)
        self.assertEqual(report["signal_stats"]["amplitude_limits"], limits)

    def test_amplitude_filter_respects_each_limit(self):
        limits = STRATEGY["max_day_range_atr"]
        report = daily(self.snapshot, [], 100000, amplitude_filter=True, top_n=99)
        for candidate in report["candidates"] + report["candidates_overflow"]:
            self.assertLessEqual(candidate["day_range_atr"], limits[candidate["model"]],
                                 f"{candidate['code']} {candidate['model']} 振幅超过该模型上限")

    def test_top_n_truncates_but_keeps_overflow(self):
        report = daily(self.snapshot, [], 100000, top_n=1)
        self.assertEqual(len(report["candidates"]), min(1, report["candidates_total"]))
        self.assertEqual(len(report["candidates"]) + len(report["candidates_overflow"]),
                         report["candidates_total"])


def _breakout_stock(code, base, vol_mult, n=80):
    """构造一只最后一天放量突破前 20 日高点的股票，成交量倍数可控。"""
    bars = []
    for i in range(n - 1):
        close = base + i * 0.01
        bars.append({"date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}", "open": close,
                     "high": close * 1.004, "low": close * 0.996, "close": close,
                     "volume": 10_000_000, "amount": close * 10_000_000})
    prev = bars[-1]["close"]
    bars.append({"date": "2026-10-30", "open": prev * 1.005, "high": prev * 1.06,
                 "low": prev, "close": prev * 1.05, "volume": vol_mult * 10_000_000,
                 "amount": prev * 1.05 * vol_mult * 10_000_000})
    return {"code": code, "name": "测试" + code[-1], "bars": bars}


class RankingTests(unittest.TestCase):
    def setUp(self):
        # B 成交额最大、量比最小；C 成交额最小；A 量比最大
        stocks = [_breakout_stock("600001", 10, 3.0),
                  _breakout_stock("600002", 12, 1.4),
                  _breakout_stock("600003", 8, 2.0)]
        self.snapshot = {"mode": "live", "as_of": "2026-10-30", "expected_session": "2026-10-30",
                         "calendar_verified": True, "calendar": ["2026-10-29", "2026-10-30"],
                         "complete": True, "errors": [], "stocks": stocks}
        self.kw = dict(capital=100000, strict_filter=False, amplitude_filter=False,
                       market_filter=False, top_n=3)

    def test_default_order_is_liquidity_desc(self):
        report = daily(self.snapshot, [], rank="liquidity", **self.kw)
        liq = [c["liquidity20"] for c in report["candidates"]]
        self.assertEqual(len(liq), 3)
        self.assertEqual(liq, sorted(liq, reverse=True))
        self.assertEqual([c["code"] for c in report["candidates"]], ["600002", "600001", "600003"])

    def test_volume_rank_is_ascending(self):
        report = daily(self.snapshot, [], rank="volume", **self.kw)
        vols = [c["volume_ratio"] for c in report["candidates"]]
        self.assertEqual(vols, sorted(vols))
        self.assertEqual(report["strategy"]["rank"], "volume")

    def test_score_rank_is_descending(self):
        report = daily(self.snapshot, [], rank="score", **self.kw)
        scores = [c["rank_score"] for c in report["candidates"]]
        self.assertEqual(scores, sorted(scores, reverse=True))
        self.assertTrue(all(0 <= s <= c["rank_max"] for s, c in zip(scores, report["candidates"])))

    def test_unknown_rank_is_rejected(self):
        with self.assertRaises(ValueError):
            daily(self.snapshot, [], rank="random", **self.kw)


if __name__ == "__main__":
    unittest.main()
