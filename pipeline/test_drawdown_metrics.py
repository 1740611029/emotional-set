"""回撤指标测试：计算口径、聚合空值，以及与主胜率的隔离。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import confirmed_index_signals as signals_module
from confirmed_index_signals import (
    drawdown_fields, evaluate_signals, legacy_accuracy, summarize_events,
)


def frame_from(close, high=None, low=None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    return pd.DataFrame({
        "date": pd.bdate_range("2025-01-02", periods=len(close)),
        "open": close, "high": close + 1 if high is None else np.asarray(high, dtype=float),
        "low": close - 1 if low is None else np.asarray(low, dtype=float),
        "close": close, "volume": 1000.0,
    })


class DrawdownFieldsTest(unittest.TestCase):
    def test_bottom_uses_low_and_top_uses_high(self):
        frame = frame_from([100, 99, 98, 97], high=[101, 100, 99, 98], low=[99, 95, 96, 96])
        bottom = drawdown_fields(frame, 0, 100.0, "bottom", None)
        self.assertAlmostEqual(bottom["max_adverse_excursion"], -0.05, places=9)
        top_frame = frame_from([100, 101, 102, 103], high=[101, 106, 103, 104], low=[99, 100, 101, 102])
        top = drawdown_fields(top_frame, 0, 100.0, "top", None)
        self.assertAlmostEqual(top["max_adverse_excursion"], -0.06, places=9)

    def test_twenty_day_window_is_bounded(self):
        close = [100] + [90] * 25
        frame = frame_from(close, high=[value + 1 for value in close], low=[value - 1 for value in close])
        result = drawdown_fields(frame, 0, 100.0, "bottom", None)
        self.assertAlmostEqual(result["max_adverse_excursion_20"], -0.11, places=9)
        self.assertAlmostEqual(result["max_adverse_excursion"], -0.11, places=9)
        self.assertAlmostEqual(result["forward_return_20"], -0.10, places=9)

    def test_forward_return_missing_when_data_ends(self):
        frame = frame_from([100, 101, 102])
        result = drawdown_fields(frame, 0, 100.0, "bottom", None)
        self.assertIsNone(result["forward_return_20"])
        self.assertIsNone(result["forward_return_5"])


class SummaryTest(unittest.TestCase):
    def test_no_resolved_samples_yield_null_drawdowns(self):
        summary = summarize_events([{"type": "bottom", "status": "pending"}])["bottom"]
        self.assertIsNone(summary["avg_max_adverse_excursion"])
        self.assertIsNone(summary["worst_max_adverse_excursion"])
        self.assertIsNone(summary["avg_forward_return_20"])
        self.assertIsNone(summary["return_drawdown_ratio"])

    def test_aggregates_use_resolved_events_only(self):
        rows = [
            {"type": "bottom", "status": "success", "max_adverse_excursion_20": -0.04, "forward_return_20": 0.08},
            {"type": "bottom", "status": "failure", "max_adverse_excursion_20": -0.10, "forward_return_20": -0.02},
            {"type": "bottom", "status": "pending", "max_adverse_excursion_20": -0.50, "forward_return_20": 0.50},
        ]
        summary = summarize_events(rows)["bottom"]
        self.assertAlmostEqual(summary["avg_max_adverse_excursion"], -0.07)
        self.assertAlmostEqual(summary["worst_max_adverse_excursion"], -0.10)
        self.assertAlmostEqual(summary["avg_forward_return_20"], 0.03)
        self.assertAlmostEqual(summary["return_drawdown_ratio"], 0.03 / 0.07)


class IsolationTest(unittest.TestCase):
    def test_drawdown_window_does_not_change_win_rate(self):
        close = np.concatenate([np.linspace(100, 110, 12), [90.0], np.linspace(95, 130, 67)])
        frame = frame_from(close)
        signals = pd.Series("", index=frame.index, dtype="string")
        signals.iat[5] = "bottom"
        signals.iat[40] = "top"
        baseline = evaluate_signals(frame, signals)
        original = signals_module.DRAWDOWN_WINDOW
        try:
            signals_module.DRAWDOWN_WINDOW = 5
            changed = evaluate_signals(frame, signals)
        finally:
            signals_module.DRAWDOWN_WINDOW = original
        for before, after in zip(baseline["segments"], changed["segments"]):
            for kind in ("bottom", "top"):
                self.assertEqual(before["summary"][kind]["rate"], after["summary"][kind]["rate"])
                self.assertEqual(before["summary"][kind]["resolved"], after["summary"][kind]["resolved"])
        self.assertNotEqual(
            baseline["segments"][1]["events"][0]["max_adverse_excursion_20"],
            changed["segments"][1]["events"][0]["max_adverse_excursion_20"],
        )

    def test_legacy_accuracy_carries_drawdown(self):
        frame = frame_from(np.linspace(100, 120, 40))
        signals = pd.Series("", index=frame.index, dtype="string")
        signals.iat[5] = "bottom"
        accuracy = legacy_accuracy(evaluate_signals(frame, signals))
        for key in ("avg_max_adverse_excursion", "worst_max_adverse_excursion",
                    "avg_forward_return_20", "return_drawdown_ratio"):
            self.assertIn(key, accuracy["bottom"])
            self.assertIn(key, accuracy["top"])


if __name__ == "__main__":
    unittest.main()
