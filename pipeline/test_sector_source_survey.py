"""换源调研脚本的纯函数测试：对齐、差异统计、状态命中率、汇总口径。

不触网：所有行情用合成数据；东财缓存命中路径单独验证。
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

import sector_source_survey as module


def make_frame(dates: pd.DatetimeIndex, seed: int, drift: float = 0.0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.01, len(dates))
    close = 1000 * np.cumprod(1 + rets)
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) * 1.01
    low = np.minimum(open_, close) * 0.99
    return pd.DataFrame({
        "date": dates, "open": open_, "high": high, "low": low, "close": close,
        "volume": np.arange(1, len(dates) + 1, dtype=float),
        "amount": np.arange(1, len(dates) + 1, dtype=float) * 1000,
    })


def trend_frame(dates: pd.DatetimeIndex, states: list[str | None], strengths: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": dates, "state": pd.Series(states, dtype="object"),
                         "strength": strengths})


class AlignTest(unittest.TestCase):
    def test_keeps_only_common_days_in_same_order(self):
        left = make_frame(pd.bdate_range("2026-01-01", periods=6), 1)
        right = make_frame(pd.bdate_range("2026-01-01", periods=6, freq="B").delete([2]), 2)
        l_out, r_out = module.align(left, right)
        self.assertEqual(len(l_out), 5)
        self.assertTrue(l_out["date"].equals(r_out["date"]))
        self.assertNotIn(pd.Timestamp("2026-01-05"), set(l_out["date"]))


class ReturnTableTest(unittest.TestCase):
    def test_percentage_difference_is_day_by_day(self):
        dates = pd.bdate_range("2026-01-01", periods=3)
        sw = pd.DataFrame({"date": dates, "close": [100.0, 110.0, 99.0]})
        em = pd.DataFrame({"date": dates, "close": [100.0, 100.0, 100.0]})
        table = module.return_table(sw, em)
        self.assertEqual(len(table), 2)          # 首日无涨跌，被丢弃
        self.assertAlmostEqual(table["abs_diff"].iat[0], 10.0, places=6)
        self.assertAlmostEqual(table["abs_diff"].iat[1], 10.0, places=6)


class StateComparisonTest(unittest.TestCase):
    def test_counts_flips_and_per_state_hit_rates(self):
        dates = pd.bdate_range("2026-01-01", periods=5)
        sw = trend_frame(dates, ["bull", "bull", "warn", None, "bear"], [10.0, 9.0, -1.0, np.nan, -20.0])
        em = trend_frame(dates, ["bull", "warn", "warn", None, "bear"], [8.0, 3.0, -1.0, np.nan, -18.0])
        result = module.state_comparison(sw, em, "2026-01-05")
        self.assertEqual(result["days"], 2)      # 评估段内两边都有状态：01-05 与 01-07
        self.assertEqual(result["state_match_rate"], 1.0)

        result = module.state_comparison(sw, em, "2026-01-01")
        self.assertEqual(result["days"], 4)
        self.assertEqual(result["flip_days"], 1)
        self.assertEqual(result["flip_breakdown"], {"bull->warn": 1})
        self.assertEqual(result["per_state"]["bull"]["both_days"], 1)
        self.assertEqual(result["per_state"]["bull"]["either_days"], 2)
        self.assertEqual(result["per_state"]["bear"]["match_rate"], 1.0)

    def test_empty_window_does_not_divide_by_zero(self):
        dates = pd.bdate_range("2026-01-01", periods=3)
        sw = trend_frame(dates, [None, None, None], [np.nan, np.nan, np.nan])
        em = trend_frame(dates, [None, None, None], [np.nan, np.nan, np.nan])
        result = module.state_comparison(sw, em, "2026-01-01")
        self.assertEqual(result["days"], 0)
        self.assertIsNone(result["state_match_rate"])


class CumulativeGapTest(unittest.TestCase):
    def test_normalised_end_gap(self):
        dates = pd.bdate_range("2026-01-01", periods=2)
        sw = pd.DataFrame({"date": dates, "close": [100.0, 120.0]})
        em = pd.DataFrame({"date": dates, "close": [100.0, 100.0]})
        self.assertEqual(module.cumulative_gap(sw, em), 20.0)
        self.assertTrue(np.isnan(module.cumulative_gap(sw.iloc[0:0], em.iloc[0:0])))


class SummarizeTest(unittest.TestCase):
    def sector(self, name: str, *, days: int, window_days: int, flips: int) -> dict:
        return {
            "code": "801000", "name": name, "common_days": window_days,
            "window_days": window_days,
            "daily_return_abs_gap": {"mean": 0.5, "median": 0.4, "p90": 1.2,
                                     "max": 3.0, "gt_1pct_days": 5, "gt_2pct_days": 1},
            "cumulative_gap_pct": 2.0, "days": days, "state_match_rate": round((days - flips) / days, 4),
            "flip_days": flips, "flip_breakdown": {"bull->warn": flips},
            "strength_abs_gap_mean": 4.0, "strength_abs_gap_median": 3.0, "strength_abs_gap_p90": 9.0,
            "per_state": {code: {"sw_days": 1, "em_days": 1, "both_days": 1, "either_days": 2,
                                 "match_rate": 0.5} for code in module.STATE_ORDER},
        }

    def test_rates_use_state_evaluated_days_as_denominator(self):
        results = [self.sector("甲", days=50, window_days=60, flips=10),
                   self.sector("乙", days=30, window_days=40, flips=5),
                   {"code": "801999", "name": "丙", "skipped": "共同交易日仅 3 天"}]
        summary = module.summarize(results, window_start="2026-01-01", window_end="2026-03-01")
        self.assertEqual(summary["sectors_total"], 3)
        self.assertEqual(summary["sectors_compared"], 2)
        self.assertEqual(summary["sector_days"], 100)
        self.assertEqual(summary["state_evaluated_days"], 80)
        self.assertEqual(summary["state_flip_sector_days"], 15)
        self.assertEqual(summary["state_flip_rate"], 0.1875)   # 15 / 80，不是 15 / 100
        self.assertEqual(summary["skipped"][0]["name"], "丙")
        self.assertEqual(summary["bull_vs_nonbull_match_rate"]["bull"]["match_rate"], 0.5)
        self.assertIn("样本量", module.render_markdown(summary))

    def test_raises_when_nothing_comparable(self):
        with self.assertRaises(RuntimeError):
            module.summarize([{"code": "801000", "name": "甲", "skipped": "缺失"}],
                             window_start="2026-01-01", window_end="2026-03-01")

    def test_opposite_direction_flips_are_counted_separately(self):
        row = self.sector("甲", days=100, window_days=100, flips=10)
        row["flip_breakdown"] = {"bull->bear": 3, "bear->bull": 2, "bull->warn": 5}
        summary = module.summarize([row], window_start="2026-01-01", window_end="2026-03-01")
        self.assertEqual(summary["state_flip_sector_days"], 10)
        self.assertEqual(summary["opposite_direction_days"], 5)
        self.assertEqual(summary["opposite_direction_rate"], 0.05)
        self.assertEqual(summary["flip_type_distribution"]["bull->bear"], 3)
        self.assertIn("翻转类型分布", module.render_markdown(summary))


class CompareSectorTest(unittest.TestCase):
    def test_synthetic_pair_produces_full_metrics(self):
        dates = pd.bdate_range("2025-01-01", periods=190)
        sw = make_frame(dates, 7, drift=0.001)
        em = make_frame(dates, 7, drift=0.001)      # 同种子：两源几乎一致
        result = module.compare_sector("801120", "食品饮料", sw, em, "2025-06-01", "2025-09-30")
        self.assertEqual(result["common_days"], 190)
        self.assertEqual(result["state_match_rate"], 1.0)
        self.assertEqual(result["flip_days"], 0)
        self.assertGreater(result["days"], 0)

    def test_short_history_is_skipped(self):
        dates = pd.bdate_range("2025-01-01", periods=40)
        result = module.compare_sector("801120", "食品饮料", make_frame(dates, 1),
                                       make_frame(dates, 2), "2025-02-01", "2025-03-01")
        self.assertIn("skipped", result)


class FetchCacheTest(unittest.TestCase):
    def test_cached_csv_is_used_without_network(self):
        original = module.EM_RAW
        self.addCleanup(setattr, module, "EM_RAW", original)
        with tempfile.TemporaryDirectory() as em_raw:
            module.EM_RAW = Path(em_raw)
            make_frame(pd.bdate_range("2026-01-01", periods=10), 3).to_csv(
                module.EM_RAW / "食品饮料.csv", index=False, encoding="utf-8-sig")
            frame = module.fetch_em("食品饮料", "20260101", "20260131")
            self.assertEqual(len(frame), 10)
            self.assertEqual(list(frame.columns),
                             ["date", "open", "high", "low", "close", "volume", "amount"])


if __name__ == "__main__":
    unittest.main()
