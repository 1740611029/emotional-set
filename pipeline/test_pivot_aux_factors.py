"""辅助因子测试：只加分不否决（信号集合逐条不变）+ T 日后数据置乱不影响 T 日前取值。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pivot_aux_factors import (
    AUX_REGISTRY, annotate_signals, build_aux_scores, grade_from_gain,
    score_breadth, score_divergence, score_external_spx,
)


def wavy_frame(n: int = 160) -> pd.DataFrame:
    dates = pd.bdate_range("2021-01-04", periods=n)
    close = 100 + 18 * np.sin(np.arange(n) / 6.0) + 0.03 * np.arange(n)
    return pd.DataFrame({"date": dates, "open": close, "high": close * 1.01,
                         "low": close * 0.99, "close": close, "volume": np.full(n, 1e6) + np.arange(n) * 50})


class RegistryTest(unittest.TestCase):
    def test_six_factors_registered_with_grade_and_source(self):
        self.assertEqual(set(AUX_REGISTRY), {
            "margin_flow", "etf_share", "qvix", "divergence", "breadth", "external_spx"})
        for name, spec in AUX_REGISTRY.items():
            self.assertIn(spec["grade"], ("P0", "P1", "P2"), name)
            self.assertTrue(spec.get("source"), f"{name} 缺出处")
            self.assertTrue(spec.get("category"), f"{name} 缺分类")

    def test_external_spx_is_all_nan_not_filled(self):
        calendar = pd.DatetimeIndex(pd.bdate_range("2021-01-04", periods=10))
        frame = score_external_spx(calendar)
        self.assertTrue(frame["bottom"].isna().all())
        self.assertTrue(frame["top"].isna().all())


class NonInterferenceTest(unittest.TestCase):
    def _scores(self):
        calendar = pd.DatetimeIndex(pd.bdate_range("2021-01-04", periods=20))
        bottom = pd.Series(np.linspace(0, 1, 20), index=calendar)
        return {"qvix": pd.DataFrame({"bottom": bottom, "top": 1 - bottom}),
                "divergence": pd.DataFrame({"bottom": 0.9, "top": 0.1}, index=calendar)}

    def test_annotate_keeps_signal_set_and_only_adds_labels(self):
        signals = [{"date": "2021-01-11", "type": "bottom", "price": 100.0},
                   {"date": "2021-02-01", "type": "top", "price": 120.0}]
        weights = {"qvix": 0.15, "divergence": 0.10}
        annotated = annotate_signals(signals, self._scores(), weights)
        # 数量、日期、类型、价格逐条不变
        self.assertEqual([r["date"] for r in annotated], [s["date"] for s in signals])
        self.assertEqual([r["type"] for r in annotated], [s["type"] for s in signals])
        self.assertEqual([r["price"] for r in annotated], [s["price"] for s in signals])
        self.assertEqual(len(annotated), len(signals))
        for record in annotated:
            self.assertIn("aux_gain", record)
            self.assertIn(record["aux_grade"], ("A", "B", "C"))
            self.assertGreaterEqual(record["aux_gain"], 0.0)

    def test_zero_aux_still_keeps_signal(self):
        # 全部辅助得分为零时信号 MUST 仍成立（只影响分级）
        calendar = pd.DatetimeIndex(pd.bdate_range("2021-01-04", periods=20))
        zero = pd.DataFrame({"bottom": 0.0, "top": 0.0}, index=calendar)
        signals = [{"date": "2021-01-11", "type": "bottom", "price": 1.0}]
        annotated = annotate_signals(signals, {"qvix": zero}, {"qvix": 0.15})
        self.assertEqual(len(annotated), 1)
        self.assertEqual(annotated[0]["aux_gain"], 0.0)
        self.assertEqual(annotated[0]["aux_grade"], "C")

    def test_grade_boundaries(self):
        self.assertEqual(grade_from_gain(0.12), "A")
        self.assertEqual(grade_from_gain(0.06), "B")
        self.assertEqual(grade_from_gain(0.059), "C")


class CausalityTest(unittest.TestCase):
    def _shuffled_after(self, frame: pd.DataFrame, cut_index: int) -> pd.DataFrame:
        out = frame.copy()
        tail = out.iloc[cut_index + 1:].copy()
        rng = np.random.default_rng(7)
        perm = rng.permutation(len(tail))
        tail = tail.iloc[perm].reset_index(drop=True)
        return pd.concat([out.iloc[:cut_index + 1], tail], ignore_index=True)

    def test_divergence_unchanged_before_cut(self):
        frame = wavy_frame()
        cut = 100
        before = score_divergence(frame)
        after = score_divergence(self._shuffled_after(frame, cut))
        # 前 cut+1 行（含切分日）逐行逐列不变（位置对齐：切分点之前未被扰动）
        pd.testing.assert_frame_equal(before.iloc[:cut + 1], after.iloc[:cut + 1])
        self.assertFalse(before["bottom"].isna().all())

    def test_breadth_unchanged_before_cut(self):
        n = 160
        calendar = pd.DatetimeIndex(pd.bdate_range("2021-01-04", periods=n))
        values = np.linspace(0.2, 0.8, n)
        breadth = pd.DataFrame({"breadth20": values}, index=calendar)
        cut = 100
        before = score_breadth(breadth, calendar)
        shuffled_values = values.copy()
        rng = np.random.default_rng(3)
        rng.shuffle(shuffled_values[cut + 1:])
        shuffled = pd.DataFrame({"breadth20": shuffled_values}, index=calendar)
        after = score_breadth(shuffled, calendar)
        pd.testing.assert_frame_equal(before.iloc[:cut + 1], after.iloc[:cut + 1])


class BuildTest(unittest.TestCase):
    def test_build_returns_all_factors_aligned(self):
        frame = wavy_frame(200)
        scores = build_aux_scores(frame, breadth=None)
        self.assertEqual(set(scores), set(AUX_REGISTRY))
        calendar = pd.DatetimeIndex(pd.to_datetime(frame["date"])).sort_values()
        for name, table in scores.items():
            self.assertTrue(table.index.equals(calendar), f"{name} 未对齐日历")
            self.assertEqual(list(table.columns), ["bottom", "top"])
        # 缺失 breadth 时 breadth 因子全 NaN，不补值
        self.assertTrue(scores["breadth"]["bottom"].isna().all())


if __name__ == "__main__":
    unittest.main()
