"""稳健性诊断的聚类、未决账龄和因果状态测试。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd
from pandas.testing import assert_frame_equal

from multifactor_robustness import cluster_diagnostics, market_regimes, pending_diagnostics


def synthetic_frame(periods: int = 120) -> pd.DataFrame:
    close = 100 + np.sin(np.arange(periods) / 5) * 8 + np.arange(periods) * 0.02
    return pd.DataFrame({
        "date": pd.bdate_range("2023-01-02", periods=periods),
        "open": close - 0.2, "close": close, "high": close + 1, "low": close - 1,
        "volume": 1000 + (np.arange(periods) % 7) * 50, "amount": close * 1000,
    })


class RobustnessDiagnosticsTest(unittest.TestCase):
    def test_same_day_indices_are_not_counted_as_independent_dates(self):
        events = [
            {"code": "a", "type": "bottom", "date": "2024-01-02", "year": 2024, "status": "success"},
            {"code": "b", "type": "bottom", "date": "2024-01-02", "year": 2024, "status": "success"},
            {"code": "a", "type": "bottom", "date": "2024-01-03", "year": 2024, "status": "failure"},
        ]
        row = next(row for row in cluster_diagnostics(events) if row["year"] is None and row["type"] == "bottom")
        self.assertEqual(row["total"], 3)
        self.assertEqual(row["unique_signal_dates"], 2)
        self.assertEqual(row["resolved_signal_dates"], 2)
        self.assertEqual(row["max_same_day_indices"], 2)
        self.assertAlmostEqual(row["multi_index_event_share"], 2 / 3)
        self.assertAlmostEqual(row["date_equal_weighted_success_rate"], 0.5)

    def test_pending_age_uses_trading_days(self):
        frame = synthetic_frame(100)
        events = [{
            "code": "x", "type": "bottom", "date": frame["date"].iat[30].strftime("%Y-%m-%d"),
            "year": 2023, "status": "pending",
        }]
        row = pending_diagnostics(events, {"x": frame})[0]
        self.assertEqual(row["max_pending_trading_days"], 69)
        self.assertEqual(row["pending_older_than_60_trading_days"], 1)
        self.assertEqual(row["pending_older_than_20_trading_days"], 1)

    def test_market_regimes_do_not_use_future_rows(self):
        frame = synthetic_frame()
        full = market_regimes(frame)
        cutoff = 100
        truncated = market_regimes(frame.iloc[:cutoff].copy())
        assert_frame_equal(full.iloc[:cutoff], truncated)


if __name__ == "__main__":
    unittest.main()
