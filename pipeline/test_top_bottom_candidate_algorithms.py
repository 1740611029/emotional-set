from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from swing_signal_engine import prepare
from top_bottom_candidate_algorithms import (
    KINDS, candidate_points, candidate_specs, confirm_candidates, spec_id,
)


def history(rows: int = 900) -> pd.DataFrame:
    generator = np.random.default_rng(20261117)
    returns = generator.normal(0.0002, 0.012, rows)
    close = 3000 * np.exp(np.cumsum(returns))
    open_ = close * (1 + generator.normal(0, 0.003, rows))
    spread = np.abs(generator.normal(0.008, 0.003, rows))
    high = np.maximum(open_, close) * (1 + spread)
    low = np.minimum(open_, close) * (1 - spread)
    volume = generator.integers(100_000_000, 500_000_000, rows).astype(float)
    return pd.DataFrame({
        "date": pd.bdate_range("2018-01-02", periods=rows),
        "open": open_, "close": close, "high": high, "low": low, "volume": volume,
    })


class CandidateAlgorithmTest(unittest.TestCase):
    def test_registry_is_large_and_unique(self):
        specs = candidate_specs()
        identifiers = [spec_id(spec) for spec in specs]
        self.assertEqual(len(identifiers), len(set(identifiers)))
        self.assertGreaterEqual(len({spec["family"] for spec in specs}), 50)
        self.assertGreaterEqual(len(specs), 100)

    def test_every_candidate_is_prefix_causal(self):
        frame = history()
        cutoff = 750
        prefix = frame.iloc[:cutoff].reset_index(drop=True)
        for spec in candidate_specs():
            with self.subTest(algorithm=spec_id(spec)):
                full = candidate_points(frame, spec)
                short = candidate_points(prefix, spec)
                for kind in KINDS:
                    expected = [item for item in full[kind] if item[0] < cutoff]
                    self.assertEqual(expected, short[kind])

    def test_three_layer_logs_and_confirmation_dates(self):
        frame = history()
        state = prepare(frame, None, {"confirm_window": 3})
        checked = 0
        for spec in candidate_specs()[:25]:
            points = candidate_points(frame, spec)
            for kind in KINDS:
                records = confirm_candidates(
                    frame, state, points[kind], kind,
                    {"volume_ratio": 0.8, "breadth_improve": 0.03},
                )
                for record in records:
                    self.assertEqual(record["type"], kind)
                    self.assertLess(record["detection_date"], record["date"])
                    self.assertTrue(record["confirmation"])
                    self.assertLessEqual(len(record["confirmation"]), 3)
                    final = record["confirmation"][-1]
                    self.assertEqual(final["passed_count"], 3)
                    self.assertTrue(final["price"] and final["momentum"] and final["volume"])
                    checked += 1
        self.assertGreater(checked, 0)


if __name__ == "__main__":
    unittest.main()
