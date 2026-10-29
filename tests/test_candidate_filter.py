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


if __name__ == "__main__":
    unittest.main()
