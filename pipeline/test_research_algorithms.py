"""只读回测的标签边界、去重和板块入场计次测试。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from research_algorithms import (
    deduplicate, evaluate_index_signals, evaluate_sector_states, ma_reversal_masks,
    summarize_index_events, wilson_interval,
)


def frame_for(dates: list[str]) -> pd.DataFrame:
    close = np.arange(100, 100 + len(dates), dtype=float)
    return pd.DataFrame({
        "date": pd.to_datetime(dates), "open": close, "close": close,
        "high": close + 1, "low": close - 1, "volume": 1.0, "amount": 1.0,
    })


class IndexEvaluationTest(unittest.TestCase):
    def evaluate(self, frame, signals, bull=None, bear=None):
        bull = pd.Series(False, index=frame.index) if bull is None else bull
        bear = pd.Series(False, index=frame.index) if bear is None else bear
        with patch("research_algorithms.ma_reversal_masks", return_value=(bull, bear)):
            return evaluate_index_signals(frame, signals, code="test", name="测试", method="test")

    def test_success_starts_on_next_day_and_records_confirmation(self):
        frame = frame_for(list(pd.date_range("2020-11-02", periods=6).strftime("%Y-%m-%d")))
        signals = pd.Series(["", "bottom", "", "", "top", ""], dtype="string")
        bull = pd.Series([True, False, True, False, False, False])
        event = self.evaluate(frame, signals, bull=bull)[0]
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["confirmation_date"], frame["date"].iat[2].strftime("%Y-%m-%d"))
        self.assertEqual(event["confirmation_days"], 1)

    def test_opposite_signal_is_failure(self):
        frame = frame_for(list(pd.date_range("2020-11-02", periods=5).strftime("%Y-%m-%d")))
        signals = pd.Series(["", "bottom", "", "top", ""], dtype="string")
        event = self.evaluate(frame, signals)[0]
        self.assertEqual(event["status"], "failure")
        self.assertEqual(event["confirmation_days"], 2)

    def test_open_sample_is_pending(self):
        frame = frame_for(list(pd.date_range("2020-11-02", periods=4).strftime("%Y-%m-%d")))
        signals = pd.Series(["", "bottom", "", ""], dtype="string")
        self.assertEqual(self.evaluate(frame, signals)[0]["status"], "pending")

    def test_research_cannot_use_validation_period_to_resolve(self):
        frame = frame_for(["2024-12-27", "2024-12-30", "2024-12-31", "2025-01-02"])
        signals = pd.Series(["bottom", "", "", ""], dtype="string")
        bull = pd.Series([False, False, False, True])
        self.assertEqual(self.evaluate(frame, signals, bull=bull)[0]["status"], "pending")

    def test_small_sample_and_wilson_are_reported(self):
        frame = frame_for(list(pd.date_range("2020-11-02", periods=5).strftime("%Y-%m-%d")))
        signals = pd.Series(["", "bottom", "", "top", ""], dtype="string")
        events = self.evaluate(frame, signals)
        summary = summarize_index_events(events, ["method"])[0]
        self.assertTrue(summary["small_sample"])
        self.assertEqual(summary["resolved"], 1)
        self.assertEqual(len(summary["wilson95"]), 2)

    def test_deduplicate_allows_signal_after_twenty_day_cooldown(self):
        mask = pd.Series([True] + [False] * 19 + [True, True] + [False] * 20 + [True])
        kept = np.flatnonzero(deduplicate(mask).to_numpy())
        self.assertEqual(kept.tolist(), [0, 21, 42])

    def test_wilson_has_finite_bounds(self):
        low, high = wilson_interval(5, 10)
        self.assertGreater(low, 0)
        self.assertLess(high, 1)
        self.assertLess(low, 0.5)
        self.assertGreater(high, 0.5)


class SectorEvaluationTest(unittest.TestCase):
    def test_state_entries_and_unmatured_samples(self):
        frame = frame_for(list(pd.date_range("2020-11-02", periods=25).strftime("%Y-%m-%d")))
        state = pd.Series(["bull"] * 12 + ["bear"] * 13, dtype="string")
        events = evaluate_sector_states(frame, state, code="s", name="板块", method="test")
        self.assertEqual(len(events), 2)
        self.assertEqual(events[0]["outcome_10"], "success")
        self.assertEqual(events[0]["outcome_20"], "success")
        self.assertEqual(events[1]["outcome_10"], "failure")
        self.assertEqual(events[1]["outcome_20"], "pending")


class MovingAverageMaskTest(unittest.TestCase):
    def test_final_alignment_and_all_slopes_are_required(self):
        frame = frame_for(list(pd.date_range("2020-11-02", periods=45).strftime("%Y-%m-%d")))
        bull, bear = ma_reversal_masks(frame)
        self.assertTrue(bool(bull.iat[-1]))
        self.assertFalse(bool(bear.iat[-1]))
        reversed_frame = frame.iloc[::-1].reset_index(drop=True)
        bull, bear = ma_reversal_masks(reversed_frame)
        self.assertFalse(bool(bull.iat[-1]))
        self.assertTrue(bool(bear.iat[-1]))


if __name__ == "__main__":
    unittest.main()
