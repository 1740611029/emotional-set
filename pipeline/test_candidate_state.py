"""候选状态导出测试：窗口过滤、已确认排除、方向交替与三层状态。

候选只用于展示，不参与准确率；本测试用受控状态替换因子计算，
专注验证导出逻辑本身。
"""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

import candidate_state
from swing_signal_engine import DEFAULT_PARAMS, LAYER_KEYS


def base_frame(periods: int = 100) -> pd.DataFrame:
    close = np.full(periods, 100.0)
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=periods),
        "open": close.copy(), "high": close + 2, "low": close - 2,
        "close": close, "volume": np.full(periods, 1000.0),
    })


def state_with(bottom_positions, top_positions, periods: int = 100) -> dict:
    masks = {}
    for kind, positions in (("bottom", bottom_positions), ("top", top_positions)):
        series = pd.Series(False, index=pd.RangeIndex(periods))
        series.iloc[list(positions)] = True
        masks[kind] = series
    return {
        "params": DEFAULT_PARAMS,
        "masks": masks,
        "context": {"rsi": pd.Series(50.0, index=pd.RangeIndex(periods)),
                    "ma5": pd.Series(100.0, index=pd.RangeIndex(periods)),
                    "volume_ma5": pd.Series(1000.0, index=pd.RangeIndex(periods))},
        "breadth_delta5": pd.Series(np.nan, index=pd.RangeIndex(periods)),
    }


class CandidateRecordsTest(unittest.TestCase):
    def records(self, frame, state, details=(), layers=None):
        layers = layers or {"price": True, "momentum": True, "volume": False}
        with patch.object(candidate_state, "prepare", return_value=state), \
             patch.object(candidate_state, "swing_details", return_value=list(details)), \
             patch.object(candidate_state, "layer_state", return_value=layers):
            return candidate_state.candidate_records(frame)

    def test_pending_candidate_reports_layer_state(self):
        frame = base_frame()
        records = self.records(frame, state_with([97], []))
        self.assertEqual(len(records), 1)
        record = records[0]
        self.assertEqual(record["date"], frame["date"].iat[97].strftime("%Y-%m-%d"))
        self.assertEqual(record["type"], "bottom")
        self.assertEqual(record["passed"], 2)
        self.assertEqual(record["trading_days_since"], 2)
        self.assertEqual(record["confirmation_window"], DEFAULT_PARAMS["confirm_window"])

    def test_confirmed_candidate_is_not_listed(self):
        frame = base_frame()
        detail = {"date": frame["date"].iat[98].strftime("%Y-%m-%d"), "type": "bottom"}
        records = self.records(frame, state_with([97], []), details=[detail])
        self.assertEqual(records, [])

    def test_candidate_outside_window_is_ignored(self):
        frame = base_frame()
        records = self.records(frame, state_with([90], []))
        self.assertEqual(records, [])

    def test_candidate_on_latest_bar_has_no_confirmation_day(self):
        frame = base_frame()
        state = state_with([99], [])
        with patch.object(candidate_state, "prepare", return_value=state), \
             patch.object(candidate_state, "swing_details", return_value=[]):
            records = candidate_state.candidate_records(frame)
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["passed"], 0)
        self.assertEqual(records[0]["layers"], {key: False for key in LAYER_KEYS})

    def test_same_direction_after_confirmed_signal_is_hidden(self):
        """已确认底部之后只允许顶部候选，同向候选不展示。"""
        frame = base_frame()
        detail = {"date": frame["date"].iat[95].strftime("%Y-%m-%d"), "type": "bottom"}
        records = self.records(frame, state_with([97], [98]), details=[detail])
        self.assertTrue(all(record["type"] == "top" for record in records))

    def test_min_swing_gate_blocks_short_leg(self):
        frame = base_frame()
        detail = {"date": frame["date"].iat[96].strftime("%Y-%m-%d"), "type": "bottom"}
        records = self.records(frame, state_with([], [97]), details=[detail])
        self.assertEqual(records, [])


if __name__ == "__main__":
    unittest.main()
