"""候选因子调研测试：映射、未来函数自检、相关性、叠加/替换与结论三态。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import factor_survey
from research_algorithms import rsi
from swing_signal_engine import DEFAULT_PARAMS


def synthetic_frame(periods: int = 300) -> pd.DataFrame:
    x = np.arange(periods)
    close = 100 + 10 * np.sin(x / 12) - 0.02 * x
    # 人为制造急跌+放量反弹脉冲，确保能触发底部三层确认
    close[80:85] = close[80] * np.linspace(1.0, 0.87, 5)   # 5天跌13%
    close[85:88] = close[85] * np.linspace(1.0, 1.06, 3)   # 3天反弹6%
    # 人为制造急涨+缩量回调脉冲，确保能触发顶部三层确认
    close[180:185] = close[180] * np.linspace(1.0, 1.13, 5)  # 5天涨13%
    close[185:188] = close[185] * np.linspace(1.0, 0.96, 3)  # 3天回调4%

    volume = 1e6 * (1 + 0.3 * np.cos(x / 7))
    volume[85:88] *= 2.0   # 反弹日放量
    volume[185:188] *= 0.6  # 回调日缩量

    return pd.DataFrame({
        "date": pd.bdate_range("2022-01-03", periods=periods),
        "open": close - 0.3,
        "high": close + 1.5,
        "low": close - 1.5,
        "close": close,
        "volume": volume,
    })


def synthetic_factors(frame: pd.DataFrame) -> pd.DataFrame:
    factors = pd.DataFrame(index=frame.index)
    for kind in ("bottom", "top"):
        factors[f"score_{kind}"] = 0.60
        for dim in ("position", "breadth", "volume"):
            factors[f"{dim}_{kind}"] = 0.90
        for dim in ("divergence", "volatility", "trend"):
            factors[f"{dim}_{kind}"] = 0.50
        factors[f"veto_{kind}"] = False
    return factors


def context_of(frame: pd.DataFrame) -> dict:
    return {
        "rsi": rsi(frame["close"]),
        "ma5": frame["close"].rolling(5).mean(),
        "volume_ma5": frame["volume"].rolling(5).mean(),
        "open": frame["open"],
        "close": frame["close"],
    }


def make_events(count: int, segment: str, successes: int, kind: str = "bottom") -> list[dict]:
    return [
        {"type": kind, "date": pd.Timestamp("2024-06-03"), "segment": segment,
         "status": "success" if index < successes else "failure"}
        for index in range(count)
    ]


class PreregisteredTest(unittest.TestCase):
    def test_thresholds_match_design(self):
        config = factor_survey.PREREGISTERED
        self.assertAlmostEqual(config["correlation_threshold"], 0.60)
        self.assertEqual(config["overlay_weights"], [0.10, 0.15, 0.20])
        self.assertEqual(config["min_validation_samples"], 20)
        self.assertEqual(config["train"], ["2020-11-01", "2023-12-31"])
        self.assertEqual(config["validation"], ["2024-01-01", "2026-10-29"])


class ScoreTest(unittest.TestCase):
    def test_monotonic_mapping_is_bounded_and_ordered(self):
        series = pd.Series(np.linspace(1, 100, 400))
        spec = {"type": "monotonic", "bottom": "high"}
        bottom = factor_survey.score_factor(series, spec, "bottom")
        top = factor_survey.score_factor(series, spec, "top")
        valid = bottom.dropna()
        self.assertTrue(((valid >= 0) & (valid <= 1)).all())
        self.assertTrue(valid.is_monotonic_increasing)
        self.assertTrue(top.dropna().is_monotonic_decreasing)

    def test_extreme_mapping_is_bounded(self):
        series = pd.Series(np.concatenate([np.linspace(1, 5, 200), np.linspace(5, 1, 200)]))
        score = factor_survey.score_factor(series, {"type": "extreme"}, "bottom")
        valid = score.dropna()
        self.assertTrue(((valid >= 0) & (valid <= 1)).all())
        self.assertGreater(valid.max(), 0.5)

    def test_no_future_leak_for_every_candidate(self):
        base = pd.Series(np.sin(np.arange(400) / 9) * 10 + 30)
        cutoff = 300
        for name, spec in factor_survey.FACTOR_SPECS.items():
            full = factor_survey.score_factor(base, spec, "bottom")
            truncated_input = base.copy()
            truncated_input.iloc[cutoff + 1:] = np.nan
            truncated = factor_survey.score_factor(truncated_input, spec, "bottom")
            pd.testing.assert_series_equal(
                full.iloc[: cutoff + 1], truncated.iloc[: cutoff + 1],
                check_names=False, obj=f"{name} 在 T 日之前被未来数据影响",
            )


class CorrelationTest(unittest.TestCase):
    def test_matrix_is_symmetric_finite_with_unit_diagonal(self):
        rng = np.random.default_rng(7)
        data = pd.DataFrame(rng.normal(size=(300, 5)), columns=list("abcde"))
        data.loc[::17, "b"] = np.nan
        matrix = factor_survey.full_correlation(data)
        self.assertTrue(np.allclose(np.diag(matrix.to_numpy()), 1.0))
        self.assertTrue(np.allclose(matrix.to_numpy(), matrix.to_numpy().T, equal_nan=True))
        self.assertFalse(np.isinf(matrix.to_numpy()).any())

    def test_duplicate_dimension_is_detected(self):
        matrix = pd.DataFrame({"candidate": [1.0, 0.8], "position_bottom": [0.8, 1.0]},
                              index=["candidate", "position_bottom"])
        dim, value = factor_survey.max_abs_correlation(matrix, "candidate", ("position_bottom",))
        self.assertEqual(dim, "position_bottom")
        self.assertAlmostEqual(value, 0.8)


class OverlayTest(unittest.TestCase):
    def test_constant_factor_leaves_score_unchanged(self):
        base = pd.Series([0.2, 0.5, 0.8, np.nan])
        constant = pd.Series([0.5, 0.5, 0.5, 0.5])
        result = factor_survey.overlay_score(base, constant, 0.15)
        pd.testing.assert_series_equal(result, base, check_names=False)

    def test_constant_factor_leaves_signals_unchanged(self):
        frame = synthetic_frame()
        factors = synthetic_factors(frame)
        context = context_of(frame)
        delta = pd.Series(0.0, index=frame.index)
        base_scores = factor_survey.baseline_scores(factors)
        base_records = factor_survey.signals_from_scores(frame, factors, context, delta, DEFAULT_PARAMS, base_scores)
        self.assertGreater(len(base_records), 0, "合成序列应至少产生一个信号")

        constant = {kind: pd.Series(0.5, index=frame.index) for kind in ("bottom", "top")}
        tilted = {kind: factor_survey.overlay_score(base_scores[kind], constant[kind], 0.15)
                  for kind in ("bottom", "top")}
        records = factor_survey.signals_from_scores(frame, factors, context, delta, DEFAULT_PARAMS, tilted)
        self.assertEqual([record["position"] for record in records],
                         [record["position"] for record in base_records])

    def test_replace_keeps_six_dimensions(self):
        frame = synthetic_frame()
        factors = synthetic_factors(frame)
        extra = pd.Series(0.9, index=frame.index)
        replaced = factor_survey.replace_score(factors, "bottom", "trend", extra)
        # position/breadth/volume 为 0.9，divergence/volatility 为 0.5，trend 被 0.9 替换，仍按 6 维求均值
        expected = (3 * 0.9 + 2 * 0.5 + 0.9) / 6
        self.assertAlmostEqual(float(replaced.iloc[0]), expected, places=6)


class VerdictTest(unittest.TestCase):
    def build_result(self, resolved: int, successes: int, trial_rate: float) -> dict:
        baseline = make_events(resolved, "validation", successes)
        trials = make_events(resolved, "validation", int(round(trial_rate * resolved)))
        return {
            "correlation": 0.30, "correlation_dim": "trend_bottom",
            "baseline": {"events": baseline},
            "overlays": {str(weight): {"events": trials} for weight in factor_survey.PREREGISTERED["overlay_weights"]},
            "replace": None,
        }

    def test_small_sample_is_downgraded_to_observe(self):
        decision = factor_survey.decide("demo", self.build_result(10, 9, 1.0))
        self.assertEqual(decision["verdict"], "观察")
        self.assertIn("n=10", decision["reason"])

    def test_improvement_marks_adopt_but_flags_noise(self):
        decision = factor_survey.decide("demo", self.build_result(40, 30, 0.85))
        self.assertEqual(decision["verdict"], "采纳")
        self.assertTrue(decision["within_noise"])
        self.assertIn("Wilson", decision["reason"])

    def test_decline_marks_abandon_with_reason(self):
        decision = factor_survey.decide("demo", self.build_result(40, 36, 0.70))
        self.assertEqual(decision["verdict"], "放弃")
        self.assertTrue(decision["reason"])


class EvidenceTest(unittest.TestCase):
    FIELDS = ("category", "system", "proxy", "meaning", "source", "availability", "evidence",
              "hypothesis", "conclusion", "reason")
    VAGUE = ("业内公认", "研究表明", "众所周知")

    def test_every_record_has_traceable_source(self):
        for record in factor_survey.EVIDENCE:
            for field in self.FIELDS:
                self.assertTrue(record.get(field), f"{record.get('system')} 缺少字段 {field}")
            for phrase in self.VAGUE:
                self.assertNotIn(phrase, record["source"])
                self.assertNotIn(phrase, record["evidence"])

    def test_conclusions_are_three_state(self):
        for record in factor_survey.EVIDENCE:
            self.assertIn(record["conclusion"], ("采纳", "观察", "放弃"))
            if record["conclusion"] == "放弃":
                self.assertTrue(record["reason"].strip())


class GradeTest(unittest.TestCase):
    def entry(self, **overrides) -> dict:
        base = {"status": "ok", "frequency": "daily", "start": "2015-02-09",
                "end": "2026-10-30", "missing_rate": 0.0}
        base.update(overrides)
        return base

    def test_long_daily_series_is_p0(self):
        grade, _ = factor_survey.grade_indicator(self.entry())
        self.assertEqual(grade, "P0")

    def test_short_history_is_p1(self):
        grade, reason = factor_survey.grade_indicator(self.entry(start="2024-10-10"))
        self.assertEqual(grade, "P1")
        self.assertIn("不足", reason)

    def test_monthly_is_p1(self):
        grade, _ = factor_survey.grade_indicator(self.entry(frequency="monthly"))
        self.assertEqual(grade, "P1")

    def test_failed_fetch_is_p2(self):
        grade, _ = factor_survey.grade_indicator({"status": "failed"})
        self.assertEqual(grade, "P2")


class YearlyTest(unittest.TestCase):
    def test_yearly_covers_both_segments(self):
        events = make_events(6, "train", 5) + make_events(4, "validation", 3)
        for index, event in enumerate(events):
            event["date"] = pd.Timestamp(f"{2021 + index // 3}-06-03")
        table = factor_survey.yearly_table(events)
        self.assertEqual(sum(row["total"] for row in table), len(events))
        self.assertEqual({row["segment"] for row in table}, {"train", "validation"})


if __name__ == "__main__":
    unittest.main()
