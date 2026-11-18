from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from run_top_bottom_closed_loop import DEFAULT_CODES, pair_score, quick_summary
from top_bottom_closed_loop import RISK_LINES, evaluate_closed_loop, prepare_direction


def frame(rows: int = 12) -> pd.DataFrame:
    close = np.full(rows, 100.0)
    return pd.DataFrame({
        "date": pd.bdate_range("2024-01-01", periods=rows),
        "open": close,
        "close": close,
        "high": close,
        "low": close,
        "volume": np.full(rows, 1000.0),
    })


def event(data: pd.DataFrame, position: int, kind: str) -> dict:
    return {
        "type": kind,
        "date": data["date"].iat[position].strftime("%Y-%m-%d"),
        "price": float(data["close"].iat[position]),
    }


def evaluate(data: pd.DataFrame, bottom: list[dict], top: list[dict], risk: float = 0.08) -> dict:
    with patch("top_bottom_closed_loop.segment_bounds", return_value={"train": (0, len(data) - 1)}):
        return evaluate_closed_loop(data, {"bottom": bottom, "top": top}, risk)


class ClosedLoopTest(unittest.TestCase):
    def test_bottom_risk_failure_cannot_be_reversed_by_later_top(self):
        data = frame()
        data.loc[2, ["low", "close"]] = [91.0, 95.0]
        data.loc[5, ["high", "close"]] = [121.0, 120.0]
        result = evaluate(data, [event(data, 1, "bottom")], [event(data, 5, "top")])
        row = result["events"]["bottom"]["train"][0]
        self.assertEqual(row["status"], "risk_failure")
        self.assertEqual(row["outcome_date"], data["date"].iat[2].strftime("%Y-%m-%d"))
        self.assertLess(row["mae"], -0.08)

    def test_bottom_exits_at_next_actual_top_without_fixed_horizon(self):
        data = frame(70)
        data.loc[65, "close"] = 110.0
        result = evaluate(data, [event(data, 1, "bottom")], [event(data, 65, "top")])
        row = result["events"]["bottom"]["train"][0]
        self.assertEqual(row["status"], "closed_success")
        self.assertEqual(row["holding_days"], 64)
        self.assertAlmostEqual(row["exit_return"], 0.10)

    def test_top_uses_mirror_risk_and_bottom_exit(self):
        data = frame()
        data.loc[2, ["high", "close"]] = [109.0, 105.0]
        data.loc[5, ["low", "close"]] = [80.0, 85.0]
        result = evaluate(data, [event(data, 5, "bottom")], [event(data, 1, "top")])
        row = result["events"]["top"]["train"][0]
        self.assertEqual(row["status"], "risk_failure")
        self.assertEqual(row["holding_days"], 1)
        self.assertLess(row["mae"], -0.08)

    def test_top_closes_successfully_at_actual_bottom(self):
        data = frame()
        data.loc[5, ["low", "close"]] = [90.0, 91.0]
        result = evaluate(data, [event(data, 5, "bottom")], [event(data, 1, "top")])
        row = result["events"]["top"]["train"][0]
        self.assertEqual(row["status"], "closed_success")
        self.assertAlmostEqual(row["exit_return"], 0.09)

    def test_unclosed_signal_is_pending_not_failure(self):
        data = frame()
        result = evaluate(data, [event(data, 1, "bottom")], [])
        row = result["events"]["bottom"]["train"][0]
        summary = result["summary"]["bottom"]["train"]
        self.assertEqual(row["status"], "pending")
        self.assertEqual(summary["resolved"], 0)
        self.assertEqual(summary["pending"], 1)
        self.assertIsNone(summary["rate"])

    def test_loss_making_closed_exit_is_success_and_separately_disclosed(self):
        data = frame()
        data.loc[5, ["low", "close"]] = [94.0, 95.0]
        result = evaluate(data, [event(data, 1, "bottom")], [event(data, 5, "top")])
        row = result["events"]["bottom"]["train"][0]
        summary = result["summary"]["bottom"]["train"]
        self.assertEqual(row["status"], "closed_success")
        self.assertEqual(summary["rate"], 1.0)
        self.assertEqual(summary["profitable_exit_rate"], 0.0)
        self.assertEqual(summary["loss_making_closed_exits"], 1)
        self.assertAlmostEqual(summary["avg_closed_exit_return"], -0.05)

    def test_same_day_reverse_signal_and_risk_is_conservative_failure(self):
        data = frame()
        data.loc[5, ["low", "close"]] = [91.0, 110.0]
        result = evaluate(data, [event(data, 1, "bottom")], [event(data, 5, "top")])
        row = result["events"]["bottom"]["train"][0]
        summary = result["summary"]["bottom"]["train"]
        self.assertEqual(row["status"], "risk_failure")
        self.assertTrue(row["ambiguous_same_day"])
        self.assertEqual(summary["ambiguous_same_day"], 1)

    def test_exact_risk_line_does_not_fail(self):
        data = frame()
        data.loc[2, "low"] = 92.0
        result = evaluate(data, [event(data, 1, "bottom")], [event(data, 5, "top")])
        self.assertEqual(result["events"]["bottom"]["train"][0]["status"], "closed_success")

    def test_index_specific_risk_lines_are_not_uniform(self):
        self.assertEqual(RISK_LINES["sz399006"], 0.08)
        self.assertEqual(RISK_LINES["sh000688"], 0.08)
        self.assertEqual(RISK_LINES["sh000001"], 0.045)
        self.assertEqual(RISK_LINES["sh000300"], 0.05)
        self.assertEqual(len(set(RISK_LINES.values())), 6)

    def test_path_metrics_cover_trade_until_outcome(self):
        data = frame()
        data.loc[2, ["low", "high"]] = [96.0, 104.0]
        data.loc[5, ["low", "high", "close"]] = [98.0, 112.0, 110.0]
        result = evaluate(data, [event(data, 1, "bottom")], [event(data, 5, "top")])
        row = result["events"]["bottom"]["train"][0]
        self.assertAlmostEqual(row["mae"], -0.04)
        self.assertAlmostEqual(row["mfe"], 0.12)
        self.assertAlmostEqual(row["mfe_mae_ratio"], 3.0)
        self.assertAlmostEqual(row["outcome_return"], 0.10)

    def test_fast_pair_summary_matches_detailed_evaluation(self):
        data = frame()
        data.loc[5, "close"] = 110.0
        bottom = [event(data, 1, "bottom")]
        top = [event(data, 5, "top")]
        prepared_bottom = prepare_direction(data, bottom, "bottom", 0.08)
        prepared_top = prepare_direction(data, top, "top", 0.08)
        fast = quick_summary(
            prepared_bottom, [row["_position"] for row in prepared_top], 0, len(data) - 1,
        )
        detailed = evaluate(data, bottom, top)["summary"]["bottom"]["train"]
        for key in ("total", "resolved", "successes", "risk_failures", "pending", "rate", "wilson95"):
            self.assertEqual(fast[key], detailed[key])

    def test_pair_threshold_requires_both_directions_and_two_years(self):
        bottom = {"resolved": 20, "active_years": 3, "wilson_low": 0.5, "rate": 0.8}
        top = {"resolved": 20, "active_years": 3, "wilson_low": 0.6, "rate": 0.9}
        self.assertIsNotNone(pair_score(bottom, top))
        self.assertIsNone(pair_score(bottom, {**top, "resolved": 19}))
        self.assertIsNone(pair_score(bottom, {**top, "active_years": 1}))

    def test_second_round_scope_is_only_three_requested_indices(self):
        self.assertEqual(set(DEFAULT_CODES), {"sh000001", "sz399006", "sh000688"})


if __name__ == "__main__":
    unittest.main()
