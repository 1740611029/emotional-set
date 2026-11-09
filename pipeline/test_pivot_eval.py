"""拐点口径评价测试：合成信号/拐点样本逐值验证四项指标、小样本标注、分段与敏感性。"""
from __future__ import annotations

import unittest

import pandas as pd

from pivot_eval import (
    CONFIRMATION_CAP, FROZEN_WINDOWS, assign_segment, build_sensitivity,
    embargo_boundary, evaluate_segment, evaluate_signal_set, trading_positions,
    window_evidence,
)


def make_dates(count: int) -> pd.DataFrame:
    return pd.DataFrame({"date": pd.bdate_range("2021-01-04", periods=count)})


def day(frame: pd.DataFrame, index: int) -> str:
    return frame["date"].iat[index].strftime("%Y-%m-%d")


class EvaluateSegmentTest(unittest.TestCase):
    def setUp(self):
        self.frame = make_dates(20)
        self.pos = trading_positions(self.frame)

    def _pivot(self, index, kind, price):
        return {"date": day(self.frame, index), "type": kind, "price": float(price)}

    def _signal(self, index, kind, price):
        return {"date": day(self.frame, index), "type": kind, "price": float(price)}

    def test_capture_lag_and_price_deviation_exact(self):
        pivots = [self._pivot(5, "top", 100), self._pivot(12, "bottom", 50)]
        signals = [self._signal(7, "top", 110), self._signal(12, "bottom", 51),
                   self._signal(3, "bottom", 48)]
        result = evaluate_segment(pivots, signals, self.pos, M=5, N=8, confirmation_cap=5)
        # 两个拐点都被同类信号在 ±5 内捕捉
        self.assertEqual(result["capture"], {**result["capture"], "hits": 2, "total": 2})
        self.assertAlmostEqual(result["capture"]["rate"], 1.0)
        # 滞后：top=+2、bottom=0 → 中位 1.0、P90=1.8
        self.assertAlmostEqual(result["lag"]["median"], 1.0)
        self.assertAlmostEqual(result["lag"]["p90"], 1.8)
        self.assertEqual(result["lag"]["counted"], 2)
        # 点位偏差：0.10、0.02 → 中位 0.06、P90=0.092
        self.assertAlmostEqual(result["price_deviation"]["median"], 0.06)
        self.assertAlmostEqual(result["price_deviation"]["p90"], 0.092)
        # 主达标：两拐点各有同类信号落在 [p-8, p+5]
        self.assertEqual(result["main_hit"]["hits"], 2)
        self.assertEqual(result["pivot_samples"], 2)

    def test_main_hit_can_exceed_capture(self):
        # 信号在拐点前 8 日（捕捉窗 ±5 之外），但落在主达标窗 [p-8, p+5] 内
        pivots = [self._pivot(10, "top", 100)]
        signals = [self._signal(2, "top", 100)]
        result = evaluate_segment(pivots, signals, self.pos, M=5, N=8, confirmation_cap=5)
        self.assertEqual(result["capture"]["hits"], 0)
        self.assertEqual(result["main_hit"]["hits"], 1)
        # 未捕捉 → 滞后与点位偏差均无记录
        self.assertEqual(result["lag"]["counted"], 0)
        self.assertEqual(result["price_deviation"]["counted"], 0)

    def test_lead_beyond_N_is_not_main_hit(self):
        pivots = [self._pivot(10, "top", 100)]
        signals = [self._signal(1, "top", 100)]  # 提前 9 日 > N=8
        result = evaluate_segment(pivots, signals, self.pos, M=5, N=8, confirmation_cap=5)
        self.assertEqual(result["capture"]["hits"], 0)
        self.assertEqual(result["main_hit"]["hits"], 0)

    def test_confirmation_lag_beyond_cap_excluded(self):
        pivots = [self._pivot(10, "top", 100)]
        within = [self._signal(14, "top", 100)]   # 滞后 4 ≤ 5
        beyond = [self._signal(16, "top", 100)]   # 滞后 6 > 5 且距拐点 6 > M=5
        self.assertEqual(evaluate_segment(pivots, within, self.pos, M=5, N=8)["main_hit"]["hits"], 1)
        self.assertEqual(evaluate_segment(pivots, beyond, self.pos, M=5, N=8)["main_hit"]["hits"], 0)

    def test_signal_type_must_match_pivot(self):
        pivots = [self._pivot(10, "top", 100)]
        signals = [self._signal(10, "bottom", 100)]  # 同位但类型不符
        result = evaluate_segment(pivots, signals, self.pos, M=5, N=8, confirmation_cap=5)
        self.assertEqual(result["capture"]["hits"], 0)
        self.assertEqual(result["main_hit"]["hits"], 0)

    def test_empty_pivots_yields_none_rates_small_sample(self):
        result = evaluate_segment([], [self._signal(1, "top", 1)], self.pos, M=5, N=8)
        self.assertIsNone(result["capture"]["rate"])
        self.assertTrue(result["capture"]["small_sample"])
        self.assertEqual(result["capture"]["wilson95"], [None, None])
        self.assertIsNone(result["lag"]["median"])

    def test_small_sample_flag_boundary(self):
        # 29 个拐点 → 小样本；每个拐点配一枚同日同类信号
        pivots = [{"date": day(self.frame, 0), "type": "top", "price": 100.0} for _ in range(29)]
        signals = [self._signal(0, "top", 100)]
        result = evaluate_segment(pivots, signals, self.pos, M=5, N=8)
        self.assertEqual(result["pivot_samples"], 29)
        self.assertTrue(result["capture"]["small_sample"])


class SegmentationTest(unittest.TestCase):
    def test_assign_segment_labels(self):
        train_start = pd.Timestamp("2021-01-01")
        train_end = pd.Timestamp("2021-12-31")
        last_valid = pd.Timestamp("2022-06-30")
        cut = pd.Timestamp("2021-12-01")  # embargo 隔离带起点
        kwargs = dict(train_start=train_start, train_end=train_end,
                      last_valid_date=last_valid, embargo_end=cut)
        self.assertEqual(assign_segment("2020-12-31", **kwargs), "outside")
        self.assertEqual(assign_segment("2021-06-15", **kwargs), "train")
        self.assertEqual(assign_segment("2021-12-20", **kwargs), "excluded")
        self.assertEqual(assign_segment("2022-03-01", **kwargs), "validation")
        self.assertEqual(assign_segment("2022-12-01", **kwargs), "outside")

    def test_embargo_boundary_walks_back_trading_days(self):
        frame = make_dates(20)
        positions = trading_positions(frame)
        train_end = frame["date"].iat[15]
        cut = embargo_boundary(train_end, positions, frame, embargo_days=5)
        self.assertEqual(cut, frame["date"].iat[10])  # 15 往回退 5 个交易日

    def test_evaluate_signal_set_segments_do_not_overlap(self):
        frame = make_dates(40)
        positions = trading_positions(frame)
        train_start, train_end = frame["date"].iat[0], frame["date"].iat[24]
        pivots = [{"date": day(frame, i), "type": "top", "price": 100.0} for i in (5, 12, 30)]
        signals = [{"date": day(frame, i), "type": "top", "price": 101.0} for i in (6, 31)]
        result = evaluate_signal_set(pivots, signals, frame,
                                     train_start=train_start, train_end=train_end,
                                     embargo_days=2)
        self.assertEqual(result["windows"], {"M": 5, "N": 8, "confirmation_cap": 5})
        # 拐点 5、12 属 train（截止 embargo 前），30 属 validation
        self.assertEqual(result["segments"]["train"]["pivot_samples"], 2)
        self.assertEqual(result["segments"]["validation"]["pivot_samples"], 1)


class WindowEvidenceTest(unittest.TestCase):
    def test_rates_monotonic_in_window_size(self):
        frame = make_dates(20)
        positions = trading_positions(frame)
        pivots = [{"date": day(frame, 10), "type": "top", "price": 100.0},
                  {"date": day(frame, 3), "type": "bottom", "price": 50.0}]
        signals = [{"date": day(frame, 13), "type": "top", "price": 100.0}]
        ev = window_evidence(pivots, signals, positions)
        capture = [ev["capture_by_M"][str(m)]["rate"] for m in (3, 5, 8)]
        main = [ev["main_hit_by_N"][str(n)]["rate"] for n in (5, 8, 10)]
        self.assertTrue(all(a <= b for a, b in zip(capture, capture[1:])), capture)
        self.assertTrue(all(a <= b for a, b in zip(main, main[1:])), main)

    def test_frozen_windows_are_within_candidate_grids(self):
        self.assertIn(FROZEN_WINDOWS["M"], (3, 5, 8))
        self.assertIn(FROZEN_WINDOWS["N"], (5, 8, 10))
        self.assertEqual(FROZEN_WINDOWS["confirmation_cap"], CONFIRMATION_CAP)


class SensitivityTest(unittest.TestCase):
    def test_build_sensitivity_reports_each_amplitude_tier(self):
        frame = make_dates(30)
        frames = {"C": frame}
        train_start, train_end = frame["date"].iat[0], frame["date"].iat[24]
        pivots_loose = [{"date": day(frame, 10), "type": "top", "price": 100.0}]
        pivots_tight = [{"date": day(frame, 10), "type": "top", "price": 100.0},
                        {"date": day(frame, 3), "type": "bottom", "price": 50.0}]
        benchmark_by_amplitude = {0.03: {"C": pivots_tight}, 0.05: {"C": pivots_loose}}
        signals_by_code = {"C": [{"date": day(frame, 11), "type": "top", "price": 100.0}]}
        tiers = build_sensitivity(frames, benchmark_by_amplitude, signals_by_code,
                                  train_start=train_start, train_end=train_end, embargo_days=1)
        self.assertEqual(set(tiers), {"3%", "5%"})
        for amplitude in ("3%", "5%"):
            self.assertIn("train", tiers[amplitude])
            self.assertIn("capture", tiers[amplitude]["train"])
            self.assertIn("main_hit", tiers[amplitude]["train"])
        # 3% 基准多一个 bottom 拐点，训练段分母更大
        self.assertEqual(tiers["3%"]["train"]["capture"]["total"], 2)
        self.assertEqual(tiers["5%"]["train"]["capture"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
