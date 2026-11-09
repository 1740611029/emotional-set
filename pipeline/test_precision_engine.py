"""无未来函数截断自检、拐点基准集因果性、三层确认日志完整性（任务 2.1 / 2.2 / 2.3）。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

import precision_config as cfg
import precision_engine as engine
from pivot_benchmark import annotate_pivots

# 测试专用：放开六因子闸门以产生足量信号，专测因果性；不改变冻结参数集。
OPEN_GATE = {
    "score_threshold": 0.0, "core_hits_min": 0, "core_extreme": 0.0,
    "direction_min": 0.0, "min_available_directions": 0, "confirm_window": 3,
}


def flat_breadth(frame: pd.DataFrame) -> pd.DataFrame:
    """前缀稳定的合成宽度表：同一日期在截断前后取值完全一致。"""
    n = len(frame)
    wave = 0.5 + 0.1 * np.sin(np.arange(n, dtype=float) / 20.0)
    return pd.DataFrame({
        "breadth20": wave, "breadth60": wave,
        "sector_count": np.full(n, 31.0),
        "breadth_delta5": np.full(n, 0.01),
    }, index=pd.DatetimeIndex(frame["date"]))


def params() -> dict:
    return {
        "algorithm_version": cfg.ALGORITHM_VERSION,
        "pivot_amplitude": cfg.FROZEN_PIVOT["amplitude"],
        "pivot_min_gap": cfg.FROZEN_PIVOT["min_gap"],
        "six_factor_gate": OPEN_GATE,
    }


class TestCausality(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frame = cfg.read_index_csv("sh000001")
        cls.cut = 1000
        cls.cut_date = cls.frame["date"].iat[cls.cut - 1].strftime("%Y-%m-%d")
        cls.full, cls.full_dropped = engine.precision_signals(
            cls.frame, params=params(), breadth=flat_breadth(cls.frame))
        cls.truncated = cls.frame.iloc[:cls.cut].reset_index(drop=True)

    def test_truncation_does_not_change_history(self):
        """T 日之后行情置空后，T 日及之前信号逐条完全一致（任务 2.3 核心用例）。"""
        part, _ = engine.precision_signals(
            self.truncated, params=params(), breadth=flat_breadth(self.truncated))
        expected = [record for record in self.full if record["date"] <= self.cut_date]
        self.assertGreater(len(expected), 0, "测试需产生足量历史信号才有意义")
        self.assertEqual(expected, part)

    def test_shuffled_future_does_not_change_history(self):
        """T 日之后行情整体打乱，T 日及之前信号仍逐条一致。"""
        shuffled = self.frame.copy()
        for column in ("open", "close", "high", "low"):
            values = shuffled[column].to_numpy(dtype=float).copy()
            values[self.cut:] = values[self.cut:] * 1.7
            shuffled[column] = values
        part, _ = engine.precision_signals(
            shuffled, params=params(), breadth=flat_breadth(shuffled))
        expected = [record for record in self.full if record["date"] <= self.cut_date]
        self.assertEqual(expected, part)

    def test_pivot_benchmark_prefix_stable_after_truncation(self):
        """将某日之后行情置空，该日及之前的拐点标注完全不变（任务 2.1）。"""
        amplitude, gap = cfg.FROZEN_PIVOT["amplitude"], cfg.FROZEN_PIVOT["min_gap"]
        full = annotate_pivots(self.frame, amplitude=amplitude, min_gap=gap)
        part = annotate_pivots(self.truncated, amplitude=amplitude, min_gap=gap)
        prefix = [record for record in full
                  if record["confirm_date"] <= self.cut_date]
        self.assertGreater(len(prefix), 0)
        self.assertEqual(prefix, part)

    def test_confirmation_never_before_natural_confirm_date(self):
        for record in self.full:
            self.assertGreaterEqual(record["signal_confirm_offset_from_natural"], 0)
            self.assertGreaterEqual(record["signal_confirm_offset_from_pivot"], 0)


class TestThreeLayerLog(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        frame = cfg.read_index_csv("sh000001")
        cls.signals, cls.dropped = engine.precision_signals(
            frame, params=params(), breadth=flat_breadth(frame))

    def test_every_candidate_has_a_record(self):
        self.assertGreater(len(self.dropped), 0)
        for record in self.dropped:
            self.assertIn(record["reason"], {"six_factor_gate_failed", "three_layer_unconfirmed"})
            self.assertIn("natural_confirm_date", record)
            self.assertIn("gate", record)
            self.assertIn("confirmation", record)

    def test_dropped_candidates_keep_full_layer_log(self):
        confirmed_logs = [record["confirmation"] for record in self.dropped
                          if record["reason"] == "three_layer_unconfirmed"]
        confirmed_logs += [record["confirmation"] for record in self.signals]
        self.assertGreater(len(confirmed_logs), 0)
        for log in confirmed_logs:
            self.assertGreater(len(log), 0)
            for entry in log:
                self.assertIn("date", entry)
                self.assertIn("offset", entry)
                for layer in ("price", "momentum", "volume"):
                    self.assertIn("passed", entry[layer], layer)
                self.assertIn("candidate_reference", entry["price"])
                self.assertIn("threshold", entry["price"])
                self.assertIn("rsi14", entry["momentum"])
                self.assertIn("rule", entry["momentum"])
                self.assertIn("volume_ratio_threshold", entry["volume"])
                self.assertIn("breadth_threshold", entry["volume"])

    def test_gate_records_values_and_thresholds(self):
        gate_failed = [record["gate"] for record in self.dropped
                       if record["reason"] == "six_factor_gate_failed"]
        self.assertGreater(len(gate_failed), 0)
        gate = gate_failed[0]
        self.assertIn("direction_scores", gate)
        self.assertIn("thresholds", gate)
        self.assertIn("score_threshold", gate["thresholds"])
        self.assertIn("core_hits_min", gate["thresholds"])
        self.assertIn("factor_evaluation_date", gate)

    def test_signals_carry_pivot_anchor_fields(self):
        for record in self.signals:
            for key in ("pivot_date", "pivot_price", "natural_confirm_date",
                        "natural_confirm_offset", "pivot_amplitude", "pivot_min_gap",
                        "signal_confirm_offset_from_pivot",
                        "signal_confirm_offset_from_natural", "confirmation"):
                self.assertIn(key, record)


if __name__ == "__main__":
    unittest.main()
