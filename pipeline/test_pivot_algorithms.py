"""候选拐点算法测试：登记完整性 + 顶底交替 + 截断重跑未来函数自检（T 日及之前逐条一致）。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from pivot_algorithms import (
    ALGORITHMS, donchian_signals, fractal_signals, run_algorithm, zigzag_confirm3_signals,
    zigzag_signals,
)
from research_algorithms import read_index_csv
from fetch_market import INDEX_RAW


def wave_frame(n: int = 180) -> pd.DataFrame:
    dates = pd.bdate_range("2021-01-04", periods=n)
    close = 100 + 20 * np.sin(np.arange(n) / 7.0) + 0.05 * np.arange(n)
    return pd.DataFrame({
        "date": dates, "open": close, "high": close * 1.012,
        "low": close * 0.988, "close": close, "volume": np.full(n, 1e6),
    })


def key(signals: list[dict]) -> list[tuple[str, str]]:
    return [(row["date"], row["type"]) for row in signals]


ALGO_CASES = {
    "zigzag": lambda f: zigzag_signals(f, amplitude=0.05, min_gap=8),
    "donchian": lambda f: donchian_signals(f, window=20, confirm_days=3),
    "fractal": lambda f: fractal_signals(f, k=2),
}


class RegistrationTest(unittest.TestCase):
    def test_all_algorithms_registered_with_required_fields(self):
        for name, spec in ALGORITHMS.items():
            for field in ("func", "definition", "param_grid", "data_deps"):
                self.assertIn(field, spec, f"{name} 缺登记字段 {field}")

    def test_run_algorithm_dispatch_and_unknown(self):
        frame = wave_frame()
        signals = run_algorithm("pivot-zigzag", frame, amplitude=0.05, min_gap=8)
        self.assertEqual(signals, zigzag_signals(frame, amplitude=0.05, min_gap=8))
        with self.assertRaises(KeyError):
            run_algorithm("pivot-unknown", frame)


class StructureTest(unittest.TestCase):
    def test_types_alternate_and_dates_valid(self):
        frame = wave_frame()
        for algo in ALGO_CASES.values():
            signals = algo(frame)
            self.assertGreater(len(signals), 3, "合成波形应产生足够样本")
            types = [row["type"] for row in signals]
            self.assertTrue(all(a != b for a, b in zip(types, types[1:])), types)
            for row in signals:
                self.assertGreaterEqual(row["confirm_offset"], 0)
                self.assertLessEqual(pd.Timestamp(row["pivot_date"]), pd.Timestamp(row["date"]))
                # 信号价取确认日收盘价
                day_close = float(frame.loc[frame["date"] == row["date"], "close"].iat[0])
                self.assertAlmostEqual(row["price"], day_close, places=6)

    def test_monotone_series_has_no_or_minimal_pivots(self):
        dates = pd.bdate_range("2021-01-04", periods=60)
        # 全程仅 3% 温和上行，未达 5% 反转阈值 → zigzag 无任何拐点
        close = np.linspace(100, 103, 60)
        frame = pd.DataFrame({"date": dates, "open": close, "high": close * 1.001,
                              "low": close * 0.999, "close": close, "volume": np.full(60, 1e6)})
        self.assertEqual(zigzag_signals(frame, amplitude=0.05, min_gap=8), [])
        # 单调强上行：起点被确认为底部拐点，其后不再反转 → 恰好 1 个 bottom
        steep = np.linspace(100, 160, 60)
        steep_frame = pd.DataFrame({"date": dates, "open": steep, "high": steep * 1.001,
                                    "low": steep * 0.999, "close": steep, "volume": np.full(60, 1e6)})
        signals = zigzag_signals(steep_frame, amplitude=0.05, min_gap=8)
        self.assertEqual([row["type"] for row in signals], ["bottom"])
        self.assertEqual(signals[0]["pivot_date"], "2021-01-04")


class TruncationCausalityTest(unittest.TestCase):
    """未来函数自检：删除 T 日之后的数据重算，T 日及之前的信号 MUST 逐条一致。"""

    def _assert_truncation(self, algo):
        frame = wave_frame()
        full = algo(frame)
        date_strs = frame["date"].dt.strftime("%Y-%m-%d").tolist()
        for stop in (70, 110, 150, len(frame) - 1):
            truncated = algo(frame.iloc[:stop + 1].reset_index(drop=True))
            cutoff = date_strs[stop]
            expected = [row for row in full if row["date"] <= cutoff]
            self.assertEqual(key(truncated), key(expected),
                             f"截断到 {cutoff} 后 T 日及之前信号应与全量一致")

    def test_zigzag_no_lookahead(self):
        self._assert_truncation(ALGO_CASES["zigzag"])

    def test_donchian_no_lookahead(self):
        self._assert_truncation(ALGO_CASES["donchian"])

    def test_fractal_no_lookahead(self):
        self._assert_truncation(ALGO_CASES["fractal"])


class Confirm3VariantTest(unittest.TestCase):
    def _signals(self, frame):
        signals, dropped = zigzag_confirm3_signals(frame, amplitude=0.05, min_gap=8)
        return signals, dropped

    def test_registered_and_returns_signals_and_drop_log(self):
        self.assertIn("pivot-zigzag+confirm3", ALGORITHMS)
        signals, dropped = self._signals(wave_frame())
        # 变体与纯 zigzag 相比，部分候选应被三层确认作废（除非全部通过）
        self.assertIsInstance(signals, list)
        self.assertIsInstance(dropped, list)
        for row in signals:
            self.assertEqual(row["algorithm"], "pivot-zigzag+confirm3")
        for row in dropped:
            self.assertEqual(row["reason"], "three_layer_unconfirmed")

    def test_signals_dates_after_pivot_and_cause(self):
        # 变体丢弃候选后可能不再严格顶底交替（预期行为），只校验信号日不早于拐点日与确认日
        frame = wave_frame()
        signals, _ = self._signals(frame)
        for row in signals:
            self.assertLessEqual(pd.Timestamp(row["pivot_date"]), pd.Timestamp(row["date"]))
            self.assertGreaterEqual(row["confirm_offset"], 0)

    def test_truncation_no_lookahead(self):
        frame = wave_frame()
        full, _ = self._signals(frame)
        date_strs = frame["date"].dt.strftime("%Y-%m-%d").tolist()
        for stop in (70, 110, 150, len(frame) - 1):
            trunc, _ = self._signals(frame.iloc[:stop + 1].reset_index(drop=True))
            cutoff = date_strs[stop]
            expected = [(row["date"], row["type"]) for row in full if row["date"] <= cutoff]
            self.assertEqual(key(trunc), expected, f"confirm3 截断到 {cutoff} 应逐条一致")


class RealDataSmokeTest(unittest.TestCase):
    def test_real_index_signals_are_causal(self):
        frame = read_index_csv(INDEX_RAW / "sh000001.csv")
        frame["date"] = pd.to_datetime(frame["date"])
        full = zigzag_signals(frame, amplitude=0.05, min_gap=8)
        self.assertGreater(len(full), 5)
        stop = len(frame) - 40
        cutoff = frame["date"].iat[stop].strftime("%Y-%m-%d")
        truncated = zigzag_signals(frame.iloc[:stop + 1].reset_index(drop=True),
                                   amplitude=0.05, min_gap=8)
        expected = [row for row in full if row["date"] <= cutoff]
        self.assertEqual(key(truncated), key(expected))


if __name__ == "__main__":
    unittest.main()
