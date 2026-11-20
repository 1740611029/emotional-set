from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from external_wave_candidates import (
    dema, ehlers_itrend, external_candidate_points, hma, wma, zlema,
)
from external_wave_candidates import EXTERNAL_FAMILIES


class ExternalWaveCandidatesTest(unittest.TestCase):
    def test_wma_uses_recent_weight(self):
        values = pd.Series([1.0, 2.0, 4.0])
        self.assertAlmostEqual(wma(values, 3).iat[2], (1 + 4 + 12) / 6)

    def test_constant_series_keeps_hma_constant_after_warmup(self):
        values = pd.Series(np.full(30, 10.0))
        result = hma(values, 16).iloc[15:]
        self.assertTrue(np.allclose(result.dropna(), 10.0))

    def test_dema_reduces_lag_on_linear_series(self):
        values = pd.Series(np.arange(1.0, 41.0))
        result = dema(values, 10)
        ema = values.ewm(span=10, adjust=False).mean()
        self.assertGreater(result.iloc[-1], ema.iloc[-1])

    def test_zlema_uses_lag_adjusted_price(self):
        values = pd.Series([10.0, 11.0, 12.0, 13.0, 14.0])
        result = zlema(values, 5)
        adjusted = values + (values - values.shift(2))
        expected = adjusted.ewm(span=5, adjust=False).mean()
        self.assertTrue(np.allclose(result.dropna(), expected.dropna()))

    def test_ehlers_itrend_is_prefix_stable(self):
        values = pd.Series([10, 11, 12, 11.5, 12.5, 13, 12.8, 13.5, 14, 15, 14.8, 15.2], dtype=float)
        full_trend, full_trigger = ehlers_itrend(values, 4)
        part_trend, part_trigger = ehlers_itrend(values.iloc[:9], 4)
        self.assertTrue(np.allclose(full_trend.iloc[:9], part_trend, equal_nan=True))
        self.assertTrue(np.allclose(full_trigger.iloc[:9], part_trigger, equal_nan=True))

    def test_ehlers_trigger_uses_old_two_bar_trend(self):
        values = pd.Series(np.arange(1.0, 20.0))
        trend, trigger = ehlers_itrend(values, 6)
        for i in range(8, len(values)):
            self.assertAlmostEqual(trigger.iat[i], 2 * trend.iat[i] - trend.iat[i - 2])

    def test_external_points_are_prefix_stable(self):
        frame = pd.DataFrame({
            "date": pd.bdate_range("2024-01-01", periods=80),
            "open": np.linspace(100, 130, 80),
            "high": np.linspace(101, 131, 80),
            "low": np.linspace(99, 129, 80),
            "close": np.linspace(100, 130, 80),
            "volume": np.full(80, 1000.0),
        })
        spec = {"family": "HMA方向反转", "params": {"period": 16}}
        full = external_candidate_points(frame, spec)
        part = external_candidate_points(frame.iloc[:60].copy(), spec)
        self.assertEqual({k: [x for x in full[k] if x[0] < 60] for k in full}, part)

    def test_external_family_names_are_unique(self):
        self.assertEqual(len(EXTERNAL_FAMILIES), 5)


if __name__ == "__main__":
    unittest.main()
