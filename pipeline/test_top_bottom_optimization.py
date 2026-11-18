from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from run_top_bottom_optimization import (
    MIN_TRAIN_RESOLVED, baseline_enricher, choose_pairs, evaluate_legacy, select_options,
)
from swing_signal_engine import prepare
from top_bottom_candidate_algorithms import confirm_candidates, money_flow


def ranking_row(resolved: int, family: str) -> dict:
    return {
        "family": family, "train_resolved": resolved, "train_active_years": 3,
        "train_wilson_low": 0.5, "train_rate": 0.8, "train_excess": 0.1,
    }


def rising_history(rows: int = 2200) -> pd.DataFrame:
    close = np.linspace(2000, 5000, rows)
    return pd.DataFrame({
        "date": pd.bdate_range("2017-01-02", periods=rows),
        "open": close * 0.999, "close": close,
        "high": close * 1.005, "low": close * 0.995,
        "volume": np.full(rows, 100_000_000.0),
    })


class OptimizationTest(unittest.TestCase):
    def test_selection_never_falls_back_to_small_samples(self):
        rows = [ranking_row(MIN_TRAIN_RESOLVED - 1, "小样本"), ranking_row(0, "零样本")]
        self.assertEqual(select_options(rows), [])
        eligible = ranking_row(MIN_TRAIN_RESOLVED, "达标")
        self.assertEqual(select_options(rows + [eligible]), [eligible])

    def test_random_baseline_includes_early_success_near_segment_end(self):
        frame = rising_history()
        enrich = baseline_enricher(frame)
        row = {
            "code": "synthetic", "kind": "bottom",
            "train_resolved": 5, "train_rate": 1.0,
            "validation_resolved": 3, "validation_rate": 1.0,
        }
        enrich(row)
        self.assertEqual(row["train_random_samples"], 5)
        self.assertEqual(row["validation_random_samples"], 3)
        self.assertEqual(row["train_random_baseline"], 1.0)
        self.assertEqual(row["validation_random_baseline"], 1.0)

    def test_mfi_is_100_when_only_positive_flow_exists(self):
        close = np.arange(1.0, 50.0)
        frame = pd.DataFrame({
            "open": close - 0.2, "close": close,
            "high": close + 0.3, "low": close - 0.3,
            "volume": np.full(len(close), 1000.0),
        })
        self.assertEqual(money_flow(frame, 14).iloc[-1], 100.0)

    def test_unconfirmed_candidates_keep_complete_layer_logs(self):
        close = np.full(200, 100.0)
        frame = pd.DataFrame({
            "date": pd.bdate_range("2020-01-02", periods=200),
            "open": close, "close": close, "high": close, "low": close,
            "volume": np.full(200, 1000.0),
        })
        state = prepare(frame, None, {"confirm_window": 3})
        audit = []
        records = confirm_candidates(
            frame, state, [(100, 100)], "bottom",
            {"volume_ratio": 1.2, "breadth_improve": 0.05}, audit=audit,
        )
        self.assertEqual(records, [])
        self.assertEqual(audit[0]["reason"], "three_layer_unconfirmed")
        self.assertEqual(len(audit[0]["confirmation"]), 3)
        self.assertTrue(all("price" in day and "momentum" in day and "volume" in day for day in audit[0]["confirmation"]))

    def test_legacy_evaluation_uses_opposite_events_from_actual_pair(self):
        frame = rising_history(12)
        bottom = {"type": "bottom", "date": frame["date"].iat[1].strftime("%Y-%m-%d")}
        top = {"type": "top", "date": frame["date"].iat[3].strftime("%Y-%m-%d")}
        bull = pd.Series(False, index=frame.index)
        bull.iloc[5] = True
        bear = pd.Series(False, index=frame.index)
        with (
            patch("run_top_bottom_optimization.ma_reversal_masks", return_value=(bull, bear)),
            patch("run_top_bottom_optimization.segment_bounds", return_value={"train": (0, 11)}),
        ):
            paired = evaluate_legacy(frame, {"bottom": [bottom], "top": [top]})
            unpaired = evaluate_legacy(frame, {"bottom": [bottom], "top": []})
        self.assertEqual(paired["events"]["bottom"]["train"][0]["status"], "failure")
        self.assertEqual(unpaired["events"]["bottom"]["train"][0]["status"], "success")

    def test_pair_choice_combines_algorithms_and_ignores_validation_ranking(self):
        selections = {"synthetic": {
            "bottom": {"options": [{"algorithm": name, "confirm": "{}"} for name in ("bottomA", "bottomB")]},
            "top": {"options": [{"algorithm": "topC", "confirm": "{}"}]},
        }}

        def fake_events(frame, state, specs, row):
            return ({"bottom": [{"algorithm": row["algorithm"]}], "top": [{"algorithm": row["algorithm"]}]}, {})

        def summary(rate, resolved, wilson_low):
            return {"rate": rate, "resolved": resolved, "wilson_low": wilson_low}

        def fake_evaluation(frame, events):
            bottom = events["bottom"][0]["algorithm"]
            train_rate = 0.95 if bottom == "bottomA" else 0.60
            validation_rate = 0.10 if bottom == "bottomA" else 1.0
            return {
                "events": {},
                "summary": {
                    "bottom": {
                        "train": summary(train_rate, 20, train_rate - 0.1),
                        "validation": summary(validation_rate, 20, validation_rate),
                    },
                    "top": {
                        "train": summary(0.90, 22, 0.80),
                        "validation": summary(0.50, 8, 0.20),
                    },
                },
            }

        with (
            patch("run_top_bottom_optimization.events_for_row", side_effect=fake_events),
            patch("run_top_bottom_optimization.evaluate_legacy", side_effect=fake_evaluation),
        ):
            result = choose_pairs("synthetic", selections, {"synthetic": pd.DataFrame()}, {"synthetic": {}}, [])
        self.assertEqual(result["chosen"]["bottom_algorithm"], "bottomA")
        self.assertEqual(result["chosen"]["top_algorithm"], "topC")
        self.assertEqual(len(result["options"]), 2)

    def test_pair_choice_rejects_either_direction_below_sample_threshold(self):
        selections = {"synthetic": {
            "bottom": {"options": [{"algorithm": "bottomA", "confirm": "{}"}]},
            "top": {"options": [{"algorithm": "topC", "confirm": "{}"}]},
        }}
        evaluation = {
            "events": {},
            "summary": {
                "bottom": {"train": {"rate": 1.0, "resolved": 20, "wilson_low": 0.8}},
                "top": {"train": {"rate": 1.0, "resolved": 19, "wilson_low": 0.8}},
            },
        }
        with (
            patch("run_top_bottom_optimization.events_for_row", return_value=({"bottom": [], "top": []}, {})),
            patch("run_top_bottom_optimization.evaluate_legacy", return_value=evaluation),
        ):
            result = choose_pairs("synthetic", selections, {"synthetic": pd.DataFrame()}, {"synthetic": {}}, [])
        self.assertIsNone(result["chosen"])
        self.assertEqual(result["options"], [])


if __name__ == "__main__":
    unittest.main()
