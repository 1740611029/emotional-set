"""持有期矩阵、MAE 5% 硬约束、双口径、触发次数守恒的单元测试（任务 3.2 / 3.6）。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from precision_config import HOLD_PERIODS, KINDS, amplitude_line
from precision_evaluation import (
    adverse_excursion_20, baseline_cell, build_matrix, date_positions,
    evaluate_event, mae_exceeded, trigger_conservation,
)


def synthetic_frame(closes: list[float] | None = None, *,
                    low_at: float | None = None, high_at: float | None = None,
                    size: int = 40) -> pd.DataFrame:
    """构造 40 个交易日的合成日线；价格基准 100，可指定 20 日内的盘中极值。"""
    close = np.full(size, 100.0) if closes is None else np.array(closes, dtype=float)
    low = np.full(size, low_at if low_at is not None else 100.0)
    high = np.full(size, high_at if high_at is not None else 100.0)
    dates = pd.bdate_range("2024-01-01", periods=size)
    return pd.DataFrame({
        "date": dates, "open": close, "close": close,
        "high": np.maximum.reduce([high, close, low]),
        "low": np.minimum.reduce([low, close, high]),
        "volume": np.full(size, 1000.0), "amount": np.full(size, 100000.0),
    })


class TestMAEHardConstraint(unittest.TestCase):
    """5% 浮亏硬约束：恰好 5% 不判失败，超限在全部持有期判失败，顶部/底部双向覆盖。"""

    def test_bottom_exactly_five_percent_is_not_failure(self):
        frame = synthetic_frame(low_at=95.0)          # 最低 95 / 100 = -5.00%
        mae = adverse_excursion_20(frame, 0, 100.0, "bottom")
        self.assertAlmostEqual(mae, -0.05, places=12)
        self.assertFalse(mae_exceeded(mae))
        event = evaluate_event(frame, 0, 100.0, "bottom", 20, line=0.0)
        self.assertFalse(event["mae_limit_exceeded"])
        self.assertNotEqual(event["reason"], "mae_limit_exceeded")

    def test_top_exactly_five_percent_is_not_failure(self):
        frame = synthetic_frame(high_at=105.0)        # 最高 105 / 100 = +5.00%
        mae = adverse_excursion_20(frame, 0, 100.0, "top")
        self.assertAlmostEqual(mae, -0.05, places=12)
        self.assertFalse(mae_exceeded(mae))
        event = evaluate_event(frame, 0, 100.0, "top", 20, line=0.0)
        self.assertFalse(event["mae_limit_exceeded"])

    def test_bottom_overshoot_fails_every_horizon(self):
        frame = synthetic_frame(low_at=94.67)         # -5.33%
        mae = adverse_excursion_20(frame, 0, 100.0, "bottom")
        self.assertTrue(mae_exceeded(mae))
        for horizon in HOLD_PERIODS:
            event = evaluate_event(frame, 0, 100.0, "bottom", horizon, line=0.0)
            self.assertEqual(event["status"], "failure", f"horizon={horizon}")
            self.assertEqual(event["reason"], "mae_limit_exceeded")
            self.assertFalse(event["direction_success"])
            self.assertFalse(event["effective_amplitude_success"])

    def test_top_overshoot_fails_every_horizon(self):
        frame = synthetic_frame(high_at=105.40)       # +5.40% 不利
        mae = adverse_excursion_20(frame, 0, 100.0, "top")
        self.assertTrue(mae_exceeded(mae))
        for horizon in HOLD_PERIODS:
            event = evaluate_event(frame, 0, 100.0, "top", horizon, line=0.0)
            self.assertEqual(event["status"], "failure", f"horizon={horizon}")
            self.assertEqual(event["reason"], "mae_limit_exceeded")

    def test_intraday_extreme_not_close_price(self):
        """盘中最低 -5.4% 但收盘最多只 -3.1%，硬约束 MUST 用盘中极值。"""
        frame = synthetic_frame(low_at=94.6)
        closes = frame["close"].to_numpy().copy()
        closes[1:21] = 96.9                          # 收盘口径仅 -3.1%
        frame["close"] = closes
        frame["low"] = np.minimum(frame["low"].to_numpy(), closes)
        event = evaluate_event(frame, 0, 100.0, "bottom", 20, line=0.0)
        self.assertTrue(event["mae_limit_exceeded"])


class TestDualCriteria(unittest.TestCase):
    def test_noise_gain_counts_direction_only(self):
        closes = np.full(40, 100.0)
        closes[20] = 100.3                            # +0.3%
        frame = synthetic_frame(closes=list(closes))
        event = evaluate_event(frame, 0, 100.0, "bottom", 20, line=0.02)
        self.assertEqual(event["status"], "success")
        self.assertTrue(event["direction_success"])
        self.assertFalse(event["effective_amplitude_success"])

    def test_effective_amplitude_requires_line(self):
        closes = np.full(40, 100.0)
        closes[20] = 103.0                            # +3% >= 线 2%
        frame = synthetic_frame(closes=list(closes))
        event = evaluate_event(frame, 0, 100.0, "bottom", 20, line=0.02)
        self.assertTrue(event["direction_success"])
        self.assertTrue(event["effective_amplitude_success"])

    def test_pending_when_horizon_unfinished(self):
        frame = synthetic_frame(size=12)
        event = evaluate_event(frame, 0, 100.0, "bottom", 30, line=0.02)
        self.assertEqual(event["status"], "pending")
        self.assertEqual(event["reason"], "holding_period_unfinished")


class TestMatrix(unittest.TestCase):
    def setUp(self):
        self.frame = synthetic_frame(size=900)
        self.dates = [day.strftime("%Y-%m-%d") for day in self.frame["date"]]
        self.signals = [
            {"date": self.dates[100], "type": "bottom", "price": 100.0},
            {"date": self.dates[200], "type": "bottom", "price": 100.0},
            {"date": self.dates[300], "type": "top", "price": 100.0},
            {"date": self.dates[870], "type": "bottom", "price": 100.0},  # 30 日未决
        ]
        self.frozen = {
            "effective_amplitude": {
                "lines": {"test": {str(h): 0.02 for h in HOLD_PERIODS}}
            }
        }
        self.segments = {
            "train": {"effective_start": self.dates[0], "effective_end": self.dates[500],
                      "start": self.dates[0], "end": self.dates[500]},
            "embargo": {"start": self.dates[501], "end": self.dates[530], "trading_days": 30},
            "validation": {"start": self.dates[531], "end": self.dates[-1]},
        }

    def test_matrix_is_complete(self):
        matrix = build_matrix(self.frame, self.signals, segments=self.segments,
                              frozen=self.frozen, code="test")
        required = ("triggers", "resolved", "successes", "failures", "pending",
                    "direction_correct_rate", "effective_amplitude_rate",
                    "direction_correct_wilson95", "statistical_interval",
                    "small_sample", "baseline", "drawdown")
        for segment in ("train", "validation"):
            self.assertIn(segment, matrix)
            for horizon in HOLD_PERIODS:
                for kind in KINDS:
                    cell = matrix[segment]["horizons"][str(horizon)][kind]
                    for key in required:
                        self.assertIn(key, cell, f"{segment}/{horizon}/{kind} 缺 {key}")

    def test_triggers_are_conserved_and_pending_visible(self):
        matrix = build_matrix(self.frame, self.signals, segments=self.segments,
                              frozen=self.frozen, code="test")
        check = trigger_conservation(matrix, self.signals, self.segments)
        self.assertTrue(check["passed"], check["problems"])
        train_bottom = matrix["train"]["horizons"]["5"]["bottom"]
        self.assertEqual(train_bottom["triggers"], 2)
        val_bottom = matrix["validation"]["horizons"]["30"]["bottom"]
        self.assertEqual(val_bottom["triggers"], 1)
        self.assertEqual(val_bottom["pending"], 1)
        self.assertEqual(val_bottom["resolved"], 0)
        self.assertIsNone(val_bottom["direction_correct_rate"])

    def test_28_lines_are_used_per_code_and_horizon(self):
        matrix = build_matrix(self.frame, self.signals, segments=self.segments,
                              frozen=self.frozen, code="test")
        for horizon in HOLD_PERIODS:
            cell = matrix["train"]["horizons"][str(horizon)]["bottom"]
            self.assertAlmostEqual(cell["effective_amplitude_line"], 0.02)
        with self.assertRaises(KeyError):
            amplitude_line(self.frozen["effective_amplitude"], "other", 20)

    def test_baseline_is_reproducible_with_fixed_seed(self):
        available = list(range(100, 400))
        first = baseline_cell(self.frame, available, 10, 20, "bottom", 0.02, 20261109)
        second = baseline_cell(self.frame, available, 10, 20, "bottom", 0.02, 20261109)
        self.assertEqual(first, second)
        self.assertEqual(first["baseline_samples"], 10)
        self.assertIsNotNone(first["baseline_rate"])

    def test_no_resolved_samples_yields_null_not_zero(self):
        matrix = build_matrix(self.frame, self.signals, segments=self.segments,
                              frozen=self.frozen, code="test")
        cell = matrix["validation"]["horizons"]["30"]["bottom"]
        self.assertIsNone(cell["drawdown"]["avg_max_adverse_excursion"])
        self.assertIsNone(cell["drawdown"]["return_drawdown_ratio"])


if __name__ == "__main__":
    unittest.main()
