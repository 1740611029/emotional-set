"""第二轮多因子研究的因果性与前向验证边界测试。"""
from __future__ import annotations

import math
import statistics
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from research_algorithms import ema, rsi
from multifactor_research import (
    apply_embargo, evaluate_window, money_flow_index, multifactor_indicators,
    multifactor_signals, training_score,
)
from multifactor_followup import CANDIDATES
from run_indicator_verification import (
    assert_reference_match, independent_indicator_values, validate_external_record,
)


def synthetic_frame(periods: int = 180) -> pd.DataFrame:
    close = 100 + np.sin(np.arange(periods) / 5) * 8 + np.arange(periods) * 0.02
    return pd.DataFrame({
        "date": pd.bdate_range("2020-01-01", periods=periods),
        "open": close - 0.2, "close": close, "high": close + 1, "low": close - 1,
        "volume": 1000 + (np.arange(periods) % 7) * 50, "amount": close * 1000,
    })


class ConfirmedIndicatorTest(unittest.TestCase):
    def test_ema_and_rsi_match_hand_calculated_recursion(self):
        close = pd.Series([10.0, 12.0, 11.0, 14.0])
        self.assertEqual(ema(close, 3).tolist(), [10.0, 11.0, 11.0, 12.5])
        values = rsi(close, 2)
        self.assertTrue(pd.isna(values.iat[0]))
        np.testing.assert_allclose(values.iloc[1:], [100, 200 / 3, 800 / 9])

    def test_rsi14_matches_scalar_smoothing_without_seed_change(self):
        close = [10, 12, 11, 14, 9, 8, 12, 10, 10, 13, 11, 9, 15, 13, 14, 10]
        gains, losses = 2.0, 0.0
        expected = [100.0]
        for previous, current in zip(close[1:-1], close[2:]):
            delta = current - previous
            gains = (13 * gains + max(delta, 0)) / 14
            losses = (13 * losses + max(-delta, 0)) / 14
            expected.append(100 * gains / (gains + losses))
        np.testing.assert_allclose(rsi(pd.Series(close)).iloc[1:], expected)

    def test_ma_boll_zscore_and_close_extremes_match_manual_values(self):
        frame = synthetic_frame(80)
        frame["close"] = np.arange(1, 81, dtype=float)
        values = multifactor_indicators(frame).iloc[-1]
        for key, expected in {"ma5": 78, "ma10": 75.5, "ma20": 70.5,
                              "ma30": 65.5, "ma60": 50.5}.items():
            self.assertAlmostEqual(values[key], expected)
        deviation = math.sqrt(399 / 12)
        self.assertAlmostEqual(values["std20"], deviation)
        self.assertAlmostEqual(values["zscore20"], 9.5 / deviation)
        self.assertAlmostEqual(values["percent_b"], 0.5 + 9.5 / (4 * deviation))
        self.assertAlmostEqual(values["drawdown20"], 0)
        self.assertAlmostEqual(values["runup20"], 80 / 61 - 1)

    def test_volatility_baseline_includes_today(self):
        frame = synthetic_frame(100)
        prices = list(range(1, 101))
        frame["close"] = prices
        returns = [current / previous - 1 for previous, current in zip(prices, prices[1:])]
        realized = [statistics.pstdev(returns[end - 20:end]) for end in range(20, 100)]
        values = multifactor_indicators(frame)
        self.assertTrue(values["volatility_baseline"].iloc[:79].isna().all())
        self.assertTrue(pd.notna(values["volatility_baseline"].iat[79]))
        self.assertAlmostEqual(values["realized20"].iat[-1], realized[-1], places=12)
        self.assertAlmostEqual(values["volatility_baseline"].iat[-1],
                               statistics.median(realized[-60:]), places=12)
        self.assertNotAlmostEqual(values["volatility_baseline"].iat[-1],
                                  statistics.median(realized[-61:-1]), places=12)

    def test_obv_and_mfi_ties_and_volume_scaling(self):
        frame = synthetic_frame(5)
        for column in ("high", "low", "close"):
            frame[column] = [10.0, 12.0, 11.0, 11.0, 13.0]
        frame["volume"] = [1.0, 2.0, 3.0, 5.0, 7.0]
        values = multifactor_indicators(frame)
        self.assertEqual(values["obv"].tolist(), [0, 2, -1, -1, 6])
        self.assertEqual(values["obv_change3"].iloc[3:].tolist(), [-1, 4])
        mfi = money_flow_index(frame, 3)
        self.assertTrue(mfi.iloc[:2].isna().all())
        np.testing.assert_allclose(mfi.iloc[2:], [100 * 24 / 57, 100 * 24 / 57, 100 * 91 / 124])
        scaled = frame.copy()
        scaled["volume"] *= 100
        np.testing.assert_allclose(mfi, money_flow_index(scaled, 3), equal_nan=True)

    def test_mfi_one_sided_flow_and_zero_flow(self):
        frame = synthetic_frame(20)
        for column in ("high", "low", "close"):
            frame[column] = np.arange(1, 21, dtype=float)
        self.assertEqual(money_flow_index(frame).iat[-1], 100)
        for column in ("high", "low", "close"):
            frame[column] = np.arange(20, 0, -1, dtype=float)
        self.assertEqual(money_flow_index(frame).iat[-1], 0)
        frame["volume"] = 0.0
        self.assertTrue(money_flow_index(frame).isna().all())

    def test_indicator_prefixes_are_unchanged_by_future_rows(self):
        frame = synthetic_frame()
        full = multifactor_indicators(frame)
        for cutoff in (5, 20, 59, 60, 79, 80, 119, 179):
            with self.subTest(cutoff=cutoff):
                assert_frame_equal(full.iloc[:cutoff], multifactor_indicators(frame.iloc[:cutoff]))

    def test_independent_scalar_reference_covers_every_final_indicator(self):
        frame = synthetic_frame()
        values = multifactor_indicators(frame)
        for position in (0, 3, 12, 13, 19, 20, 59, 60, 79, 80, 179):
            with self.subTest(position=position):
                local = {key: float(value) if pd.notna(value) else None
                         for key, value in values.iloc[position].items()}
                reference = independent_indicator_values(frame, position)
                assert_reference_match(local, reference)
                altered = dict(local)
                altered["obv"] = altered["obv"] + 1
                with self.assertRaisesRegex(AssertionError, "obv"):
                    assert_reference_match(altered, reference)

    def test_external_obv_record_is_rejected_by_signed_volume_identity(self):
        result = validate_external_record()
        self.assertEqual(result["mfi14_actual_matches"], 2)
        self.assertTrue(result["ma20_actual_match"])
        self.assertEqual(result["obv_external_reference_rejected_days"],
                         ["2026-10-26", "2026-10-27", "2026-10-28"])
        self.assertTrue(result["obv_local_signed_volume_match"])

    def test_short_or_flat_history_does_not_emit_confirmed_signals(self):
        short = synthetic_frame(59)
        flat = synthetic_frame(180)
        for column in ("open", "high", "low", "close"):
            flat[column] = 100.0
        for frame in (short, flat):
            result = multifactor_signals(frame, 4, participation_mode="and")
            self.assertTrue(result.eq("").all())
        values = multifactor_indicators(flat)
        for key in ("rsi14", "percent_b", "zscore20", "mfi14"):
            self.assertTrue(values[key].isna().all())


class MultifactorResearchTest(unittest.TestCase):
    def test_truncated_history_does_not_repaint_signals(self):
        frame = synthetic_frame()
        full = multifactor_signals(frame, 3)
        cutoff = 120
        truncated = multifactor_signals(frame.iloc[:cutoff].copy(), 3)
        self.assertEqual(full.iloc[:cutoff].tolist(), truncated.tolist())

    def test_only_fixed_research_thresholds_are_allowed(self):
        frame = synthetic_frame()
        with self.assertRaises(ValueError):
            multifactor_signals(frame, 2)
        with self.assertRaises(ValueError):
            multifactor_signals(frame, 5)

    def test_pending_training_samples_do_not_improve_score(self):
        events = []
        for index in range(20):
            events.append({"type": "bottom" if index < 10 else "top", "status": "success"})
        baseline = training_score(events)
        with_pending = training_score(events + [{"type": "bottom", "status": "pending"}] * 100)
        self.assertEqual(baseline, with_pending)

    def test_embargo_excludes_last_twenty_trading_days(self):
        frame = synthetic_frame(80)
        fold_start = pd.Timestamp("2020-04-01")
        events = [{"code": "x", "date": day.strftime("%Y-%m-%d")} for day in frame["date"]]
        kept = apply_embargo(events, {"x": frame}, fold_start)
        start_position = int(np.flatnonzero((frame["date"] >= fold_start).to_numpy())[0])
        self.assertEqual(len(kept), start_position - 20)

    def test_followup_variants_are_causal_and_original_is_unchanged(self):
        frame = synthetic_frame()
        original = multifactor_signals(frame, 4)
        self.assertEqual(
            original.tolist(),
            multifactor_signals(frame, 4, trigger_mode="original", participation_mode="or").tolist(),
        )
        for spec in CANDIDATES.values():
            full = multifactor_signals(frame, **spec)
            truncated = multifactor_signals(frame.iloc[:120].copy(), **spec)
            self.assertEqual(full.iloc[:120].tolist(), truncated.tolist())

    def test_unknown_followup_modes_are_rejected(self):
        frame = synthetic_frame()
        with self.assertRaises(ValueError):
            multifactor_signals(frame, 4, trigger_mode="future")
        with self.assertRaises(ValueError):
            multifactor_signals(frame, 4, participation_mode="majority")

    def test_fold_end_cannot_resolve_using_next_fold(self):
        frame = synthetic_frame(10)
        frame["date"] = pd.bdate_range("2020-11-02", periods=10)
        signals = pd.Series(["bottom"] + [""] * 9, dtype="string")
        label_end = frame["date"].iat[5]
        with patch("multifactor_research.ma_reversal_masks") as masks:
            bull = pd.Series([False] * 6 + [True] + [False] * 3)
            masks.return_value = (bull, pd.Series(False, index=frame.index))
            events = evaluate_window(
                frame, signals, code="x", name="测试", method="mf",
                signal_start=frame["date"].iat[0], signal_end=frame["date"].iat[-1], label_end=label_end,
            )
        self.assertEqual(events[0]["status"], "pending")


if __name__ == "__main__":
    unittest.main()
