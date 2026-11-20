from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from run_wave_return_comparison import DEFAULT_CODES
from wave_return_diagnostics import (
    evaluate_oracle_segment, match_oracle_wave, oracle_metrics, summarize_oracle,
)


def frame() -> pd.DataFrame:
    close = np.array([100.0, 95.0, 92.0, 110.0, 115.0, 120.0, 118.0, 116.0])
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=len(close)),
        "open": close,
        "close": close,
        "high": close + 2,
        "low": close - 2,
        "volume": np.full(len(close), 1000.0),
    })


def event(data: pd.DataFrame, position: int, kind: str) -> dict:
    return {
        "type": kind,
        "date": data["date"].iat[position].strftime("%Y-%m-%d"),
        "price": float(data["close"].iat[position]),
    }


class WaveReturnDiagnosticsTest(unittest.TestCase):
    def test_bottom_actual_ideal_delay_and_remaining_mfe(self):
        data = frame()
        data.loc[1, "low"] = 90.0
        data.loc[4, "high"] = 130.0
        wave = {"kind": "bottom", "start": 1, "end": 5}
        result = oracle_metrics(data, 3, wave)
        self.assertAlmostEqual(result["oracle_exit_return"], 120 / 110 - 1)
        self.assertAlmostEqual(result["ideal_return"], 130 / 90 - 1)
        self.assertAlmostEqual(result["oracle_mfe"], 130 / 110 - 1)
        self.assertEqual(result["oracle_delay_days"], 2)
        self.assertAlmostEqual(result["oracle_lag_price_move"], 110 / 90 - 1)

    def test_top_is_mirror_of_bottom(self):
        data = frame()
        data.loc[1, "high"] = 120.0
        data.loc[3, "close"] = 90.0
        data.loc[5, "low"] = 70.0
        data.loc[5, "close"] = 80.0
        wave = {"kind": "top", "start": 1, "end": 5}
        result = oracle_metrics(data, 3, wave)
        self.assertAlmostEqual(result["oracle_exit_return"], 1 - 80 / 90)
        self.assertAlmostEqual(result["ideal_return"], 1 - 70 / 120)
        self.assertAlmostEqual(result["oracle_mfe"], 1 - 70 / 90)
        self.assertEqual(result["oracle_delay_days"], 2)
        self.assertAlmostEqual(result["oracle_lag_price_move"], 1 - 90 / 120)

    def test_early_signal_has_negative_delay(self):
        data = frame()
        wave = {"kind": "bottom", "start": 2, "end": 5}
        result = oracle_metrics(data, 0, wave)
        self.assertEqual(result["oracle_delay_days"], -2)

    def test_signal_between_waves_matches_upcoming_bottom(self):
        waves = [
            {"kind": "bottom", "start": 1, "end": 3},
            {"kind": "top", "start": 3, "end": 5},
            {"kind": "bottom", "start": 5, "end": 7},
        ]
        self.assertEqual(match_oracle_wave(waves, 4, "bottom")["start"], 5)
        self.assertEqual(match_oracle_wave(waves, 2, "bottom")["start"], 1)

    def test_wave_ending_after_segment_is_pending(self):
        data = frame()
        waves = [{"kind": "bottom", "start": 1, "end": 5}]
        rows = evaluate_oracle_segment(
            data, [event(data, 2, "bottom")], waves, "bottom", "train", 0, 4,
        )
        self.assertEqual(rows[0]["oracle_status"], "pending")
        self.assertEqual(summarize_oracle(rows)["closed"], 0)

    def test_summary_uses_only_closed_waves_for_return_samples(self):
        data = frame()
        waves = [{"kind": "bottom", "start": 1, "end": 5}]
        rows = evaluate_oracle_segment(
            data,
            [event(data, 2, "bottom"), event(data, 4, "bottom")],
            waves, "bottom", "train", 0, 4,
        )
        summary = summarize_oracle(rows)
        self.assertEqual(summary["total"], 2)
        self.assertEqual(summary["closed"], 0)
        self.assertEqual(summary["pending"], 2)
        self.assertIsNone(summary["avg_exit_return"])

    def test_scope_is_chinext_and_star50(self):
        self.assertEqual(set(DEFAULT_CODES), {"sz399006", "sh000688"})


if __name__ == "__main__":
    unittest.main()
