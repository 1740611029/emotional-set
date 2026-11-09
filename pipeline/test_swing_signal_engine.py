"""波段引擎测试：三层确认、交替约束、最小波段与分级。"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import swing_signal_engine as engine
from swing_signal_engine import (
    DEFAULT_PARAMS, _confirm, _swing_gate, layer_state, swing_details, swing_signals,
)


def frame_from(close, volume=None, high=None, low=None, open_=None) -> pd.DataFrame:
    close = np.asarray(close, dtype=float)
    periods = len(close)
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=periods),
        "open": close if open_ is None else np.asarray(open_, dtype=float),
        "high": close + 1 if high is None else np.asarray(high, dtype=float),
        "low": close - 1 if low is None else np.asarray(low, dtype=float),
        "close": close,
        "volume": np.full(periods, 1000.0) if volume is None else np.asarray(volume, dtype=float),
    })


def context_for(frame: pd.DataFrame) -> dict:
    close = frame["close"]
    return {
        "rsi": pd.Series(np.linspace(40, 60, len(frame)), index=frame.index),
        "ma5": close.rolling(5).mean(),
        "volume_ma5": frame["volume"].rolling(5).mean(),
    }


class LayerStateTest(unittest.TestCase):
    def test_bottom_price_layer_requires_structure_break(self):
        frame = frame_from([100, 100, 101.0])
        context = context_for(frame)
        delta = pd.Series(np.nan, index=frame.index)
        failed = layer_state(frame, context, delta, 2, "bottom", 1)
        self.assertFalse(failed["price"])
        frame.loc[2, "close"] = 102.0
        frame.loc[2, "high"] = 103.0
        passed = layer_state(frame, context, delta, 2, "bottom", 1)
        self.assertTrue(passed["price"])

    def test_top_momentum_requires_falling_ma5(self):
        frame = frame_from([100, 101, 102, 103, 104, 99.0], open_=[100, 101, 102, 103, 104, 100.0])
        context = {
            "rsi": pd.Series([50, 51, 52, 53, 54, 50.0]),
            "ma5": frame["close"].rolling(5).mean(),
            "volume_ma5": frame["volume"].rolling(5).mean(),
        }
        delta = pd.Series(np.nan, index=frame.index)
        state = layer_state(frame, context, delta, 5, "top", 4)
        self.assertTrue(state["momentum"])
        rising = frame_from([100, 101, 102, 103, 104, 105.0], open_=[100, 101, 102, 103, 104, 106.0])
        rising_context = {
            "rsi": pd.Series([50, 51, 52, 53, 54, 50.0]),
            "ma5": rising["close"].rolling(5).mean(),
            "volume_ma5": rising["volume"].rolling(5).mean(),
        }
        blocked = layer_state(rising, rising_context, delta, 5, "top", 4)
        self.assertFalse(blocked["momentum"])

    def test_volume_layer_accepts_breadth_improvement(self):
        frame = frame_from([100, 100, 101.0], volume=[1000, 1000, 1000])
        context = context_for(frame)
        delta = pd.Series([0.0, 0.0, 0.08], index=frame.index)
        state = layer_state(frame, context, delta, 2, "bottom", 1)
        self.assertTrue(state["volume"])


class ConfirmationTest(unittest.TestCase):
    def test_confirmation_window_expires_after_three_days(self):
        frame = frame_from([100, 100, 100, 100, 100, 100])
        context = context_for(frame)
        delta = pd.Series(np.nan, index=frame.index)
        confirmed, log = _confirm(frame, context, delta, 1, "bottom", DEFAULT_PARAMS)
        self.assertIsNone(confirmed)
        self.assertEqual(len(log), 3)
        self.assertTrue(all(row["passed"] < 3 for row in log))


class AlternationTest(unittest.TestCase):
    def setUp(self):
        self.frame = frame_from(np.linspace(100, 120, 60))

    def _state(self, bottom_positions, top_positions):
        masks = {}
        for kind, positions in (("bottom", bottom_positions), ("top", top_positions)):
            series = pd.Series(False, index=self.frame.index)
            series.iloc[list(positions)] = True
            masks[kind] = series
        return {
            "frame": self.frame, "params": DEFAULT_PARAMS, "masks": masks,
            "context": context_for(self.frame),
            "breadth_delta5": pd.Series(np.nan, index=self.frame.index),
            "factors": pd.DataFrame({
                "score_bottom": 0.6, "score_top": 0.6,
                "core_hits_bottom": 2, "core_hits_top": 2,
                "veto_bottom": False, "veto_top": False,
                "breadth20": np.nan, "breadth_delta5": np.nan,
                **{f"{name}_{kind}": 0.5 for kind in ("bottom", "top")
                   for name in ("position", "divergence", "volume", "volatility", "trend", "breadth")},
            }, index=self.frame.index),
        }

    def test_same_direction_is_not_repeated(self):
        state = self._state([10, 30, 50], [])
        with patch.object(engine, "prepare", return_value=state), \
             patch.object(engine, "_confirm", side_effect=lambda frame, ctx, delta, pos, kind, cfg: (pos + 1, [])), \
             patch.object(engine, "_swing_gate", return_value=True):
            records = swing_details(self.frame)
        self.assertEqual([record["type"] for record in records], ["bottom"])

    def test_opposite_direction_restores_alternation(self):
        state = self._state([10, 50], [30])
        with patch.object(engine, "prepare", return_value=state), \
             patch.object(engine, "_confirm", side_effect=lambda frame, ctx, delta, pos, kind, cfg: (pos + 1, [])), \
             patch.object(engine, "_swing_gate", return_value=True):
            records = swing_details(self.frame)
        self.assertEqual([record["type"] for record in records], ["bottom", "top", "bottom"])

    def test_signals_are_placed_on_confirmation_day(self):
        state = self._state([10], [])
        with patch.object(engine, "prepare", return_value=state), \
             patch.object(engine, "_confirm", side_effect=lambda frame, ctx, delta, pos, kind, cfg: (pos + 2, [])), \
             patch.object(engine, "_swing_gate", return_value=True):
            records = swing_details(self.frame)
            signals = swing_signals(self.frame)
        self.assertEqual(records[0]["date"], self.frame["date"].iat[12].strftime("%Y-%m-%d"))
        self.assertEqual(signals.iat[12], "bottom")


class SwingGateTest(unittest.TestCase):
    def test_short_or_small_swing_is_rejected(self):
        frame = frame_from([100, 100, 101, 101, 101, 101, 101, 101, 101, 105])
        self.assertFalse(_swing_gate(frame, 3, 0, DEFAULT_PARAMS))
        self.assertTrue(_swing_gate(frame, 9, 0, DEFAULT_PARAMS))
        flat = frame_from([100] * 20)
        self.assertFalse(_swing_gate(flat, 15, 0, DEFAULT_PARAMS))


class GradeTest(unittest.TestCase):
    def test_grade_a_requires_three_core_hits(self):
        records = [
            {"score": 0.6, "core_hits": 3, "confirm_offset": 1},
            {"score": 0.9, "core_hits": 2, "confirm_offset": 1},
            {"score": 0.7, "core_hits": 3, "confirm_offset": 1},
            {"score": 0.5, "core_hits": 3, "confirm_offset": 1},
        ]
        engine._assign_grades(records, DEFAULT_PARAMS)
        self.assertEqual([record["grade"] for record in records], ["B", "B", "A", "B"])


class DetailRecordTest(unittest.TestCase):
    def test_missing_field_raises(self):
        from confirmed_index_signals import detail_records

        frame = frame_from([100, 101, 102])
        complete = {
            "date": "2026-01-07", "type": "bottom", "price": 102.0,
            "candidate_date": "2026-01-05", "grade": "B", "score": 0.6,
            "core_hits": 2, "factors": {}, "breadth": {}, "confirmation": [],
        }
        self.assertEqual(detail_records(frame, [complete])[0]["date"], "2026-01-07")
        broken = {key: value for key, value in complete.items() if key != "grade"}
        with self.assertRaises(ValueError):
            detail_records(frame, [broken])


if __name__ == "__main__":
    unittest.main()
