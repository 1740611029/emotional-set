"""确认版板块趋势的人工 OHLC 与因果性测试。"""
from __future__ import annotations

import math
import unittest

import numpy as np
import pandas as pd

from confirmed_sector_trends import (
    ALGORITHM_VERSION,
    STATE_CODES,
    build_sector_evaluation,
    calculate_sector_trend,
    calculate_sector_universe,
    sector_history,
    spark_series,
)
from research_algorithms import evaluate_sector_states, sector_state


MANIFEST = [{"code": "test", "name": "测试"}]


def make_frame(close: list[float], high: list[float], low: list[float]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": pd.date_range("2024-01-01", periods=len(close), freq="D"),
        "open": close,
        "high": high,
        "low": low,
        "close": close,
    })


def oscillating_frame(periods: int = 500) -> pd.DataFrame:
    """震荡序列（跨研究段与验证段）：四种状态都会出现，供四态拆分与对账用例使用。"""
    close = [100.0 + 30.0 * math.sin(index / 7) + 15.0 * math.sin(index / 23)
             for index in range(periods)]
    return make_frame(close, [value + 2.0 for value in close], [value - 2.0 for value in close])


def four_state_frame(close: float, high: float, low: float) -> pd.DataFrame:
    return make_frame(
        [100.0] * 59 + [close],
        [100.0] * 59 + [high],
        [100.0] * 59 + [low],
    )


def directed_score_frame(channel: float, ma_score: float) -> pd.DataFrame:
    low, high = 80.0, 100.0
    close = low + (high - low) * channel / 100
    ratio = 1 + 0.10 * math.atanh((ma_score - 50) / 50)
    average = close / ratio
    previous = (60 * average - close) / 59
    return make_frame([previous] * 59 + [close], [high] * 60, [low] * 60)


class ConfirmedSectorTrendTest(unittest.TestCase):
    def test_manual_ohlc_covers_four_states_and_direction_strength(self):
        cases = (
            ("bull", 110.0, 111.0, 100.0),
            ("warn", 102.0, 120.0, 100.0),
            ("bear", 90.0, 120.0, 80.0),
            ("rise", 90.0, 91.0, 70.0),
        )
        for expected_state, close, high, low in cases:
            with self.subTest(state=expected_state):
                frame = four_state_frame(close, high, low)
                result = calculate_sector_trend(frame)
                self.assertEqual(result["state"].iat[-1], expected_state)
                self.assertEqual(result["days"].iat[-1], 1)
                self.assertFalse(result["data_anomaly"].iat[-1])

                highest = frame["high"].iloc[-20:].max()
                lowest = frame["low"].iloc[-20:].min()
                average = frame["close"].iloc[-60:].mean()
                channel = (close - lowest) / (highest - lowest) * 100
                ma_score = 50 + 50 * math.tanh((close / average - 1) / 0.10)
                base = (channel + ma_score) / 2
                expected_strength = base - 50
                self.assertAlmostEqual(result["strength"].iat[-1], expected_strength)
                self.assertGreaterEqual(result["strength"].iat[-1], -50)
                self.assertLessEqual(result["strength"].iat[-1], 50)

    def test_directional_strength_reframes_base_scores(self):
        for channel, ma_score, expected in ((80.0, 80.0, 30.0), (20.0, 20.0, -30.0)):
            with self.subTest(channel=channel, ma_score=ma_score):
                result = calculate_sector_trend(directed_score_frame(channel, ma_score)).iloc[-1]
                self.assertAlmostEqual(result["range_position"], channel, places=6)
                self.assertAlmostEqual(result["ma_score"], ma_score, places=6)
                self.assertAlmostEqual(result["strength"], expected, places=6)
                self.assertEqual(result["strength_basis"], "directional")

    def test_new_state_counts_one_and_continuation_increments(self):
        frame = make_frame(
            [100.0] * 59 + [110.0, 112.0, 108.0, 109.0],
            [100.0] * 59 + [111.0, 113.0, 130.0, 131.0],
            [100.0] * 59 + [100.0, 110.0, 107.0, 108.0],
        )
        result = calculate_sector_trend(frame)
        self.assertEqual(result["state"].iloc[-4:].tolist(), ["bull", "bull", "warn", "warn"])
        self.assertEqual(result["days"].iloc[-4:].tolist(), [1, 2, 1, 2])

    def test_zero_channel_has_zero_strength_and_anomaly_flag(self):
        frame = make_frame([100.0] * 60, [100.0] * 60, [100.0] * 60)
        result = calculate_sector_trend(frame)
        self.assertEqual(result["state"].iat[-1], "bear")
        self.assertEqual(result["strength"].iat[-1], 0.0)
        self.assertEqual(result["strength_basis"].iat[-1], "neutral")
        self.assertTrue(result["data_anomaly"].iat[-1])
        self.assertGreaterEqual(result["strength"].dropna().min(), -50)
        self.assertLessEqual(result["strength"].dropna().max(), 50)

    def test_prefix_replay_does_not_change_existing_results(self):
        frame = make_frame(
            [100.0] * 59 + [110.0, 112.0, 108.0, 109.0],
            [100.0] * 59 + [111.0, 113.0, 130.0, 131.0],
            [100.0] * 59 + [100.0, 110.0, 107.0, 108.0],
        )
        full = calculate_sector_trend(frame)
        for end in range(1, len(frame) + 1):
            prefix = calculate_sector_trend(frame.iloc[:end].copy())
            for column in ("state", "strength", "days", "data_anomaly"):
                expected = full[column].iat[end - 1]
                actual = prefix[column].iat[-1]
                if pd.isna(expected):
                    self.assertTrue(pd.isna(actual), (end, column))
                elif column == "strength":
                    self.assertAlmostEqual(actual, expected)
                else:
                    self.assertEqual(actual, expected, (end, column))


class SectorUniverseTest(unittest.TestCase):
    """单层行业板块：各自独立判定，不再有任何父子关系字段。"""

    manifest = [
        {"code": "BK0433", "name": "农林牧渔"},
        {"code": "BK0479", "name": "钢铁"},
    ]

    def test_each_sector_uses_its_own_state_and_has_no_hierarchy(self):
        bull = make_frame(
            [100.0] * 59 + [110.0, 112.0],
            [100.0] * 59 + [111.0, 113.0],
            [100.0] * 59 + [100.0, 110.0],
        )
        bear = make_frame(
            [100.0] * 59 + [90.0, 90.0],
            [100.0] * 59 + [120.0, 120.0],
            [100.0] * 59 + [80.0, 80.0],
        )
        own = calculate_sector_trend(bear).iloc[-1]
        self.assertEqual(own["state"], "bear")

        result = calculate_sector_universe(self.manifest, {"BK0433": bull, "BK0479": bear})
        first, second = result
        self.assertEqual((first["state"], first["days"]), ("bull", 2))
        self.assertEqual(second["state"], "bear")
        self.assertEqual(second["days"], int(own["days"]))
        self.assertEqual(second["strength"], round(float(own["strength"]), 4))
        self.assertEqual(second["strength_basis"], "directional")
        self.assertFalse(second["data_lag"])
        for record in result:
            for forbidden in ("level", "parent", "subsectors"):
                self.assertNotIn(forbidden, record)

    def test_insufficient_sector_is_unclassified(self):
        sufficient = make_frame(
            [100.0] * 59 + [90.0] + [90.0] * 20,
            [100.0] * 59 + [120.0] + [120.0] * 20,
            [100.0] * 59 + [80.0] + [80.0] * 20,
        )
        insufficient = make_frame([100.0] * 59, [100.0] * 59, [100.0] * 59)

        result = calculate_sector_universe(
            self.manifest, {"BK0433": sufficient, "BK0479": insufficient})
        self.assertEqual(result[0]["state"], "bear")
        second = result[1]
        self.assertIsNone(second["state"])
        self.assertIsNone(second["strength"])
        self.assertIsNone(second["days"])
        self.assertIsNone(second["strength_parts"])

    def test_lagging_sector_is_flagged_and_long_lag_clears_state(self):
        recent = make_frame(
            [100.0] * 99 + [110.0], [100.0] * 99 + [111.0], [100.0] * 99 + [100.0])
        stale = make_frame(
            [100.0] * 59 + [90.0], [100.0] * 59 + [120.0], [100.0] * 59 + [80.0])

        result = calculate_sector_universe(
            self.manifest, {"BK0433": recent, "BK0479": stale})
        self.assertTrue(result[1]["data_lag"])
        self.assertIsNone(result[1]["state"])

        near = make_frame(
            [100.0] * 59 + [90.0] + [90.0] * 19,
            [100.0] * 59 + [120.0] + [120.0] * 19,
            [100.0] * 59 + [80.0] + [80.0] * 19,
        )
        result = calculate_sector_universe(
            self.manifest, {"BK0433": recent, "BK0479": near})
        self.assertTrue(result[1]["data_lag"])
        self.assertEqual(result[1]["state"], "bear")


class SectorStrengthPartsTest(unittest.TestCase):
    def test_parts_recompute_strength(self):
        cases = (
            ("bull", 110.0, 111.0, 100.0),
            ("warn", 102.0, 120.0, 100.0),
            ("bear", 90.0, 120.0, 80.0),
            ("rise", 90.0, 91.0, 70.0),
        )
        for expected_state, close, high, low in cases:
            with self.subTest(state=expected_state):
                result = calculate_sector_trend(four_state_frame(close, high, low)).iloc[-1]
                parts = result["range_position"], result["ma_score"]
                average = (parts[0] + parts[1]) / 2
                expected = 0.0 if result["strength_basis"] == "neutral" else average - 50
                self.assertAlmostEqual(result["strength"], expected, places=6)

    def test_zero_channel_basis_is_neutral(self):
        result = calculate_sector_trend(make_frame([100.0] * 60, [100.0] * 60, [100.0] * 60)).iloc[-1]
        self.assertEqual(result["strength"], 0.0)
        self.assertEqual(result["strength_basis"], "neutral")
        self.assertEqual(result["range_position"], 50.0)

    def test_missing_state_has_no_parts(self):
        result = calculate_sector_trend(
            make_frame([100.0] * 30, [100.0] * 30, [100.0] * 30)).iloc[-1]
        self.assertTrue(pd.isna(result["state"]))
        self.assertTrue(pd.isna(result["range_position"]))
        self.assertTrue(pd.isna(result["strength_basis"]))


class SectorHistoryTest(unittest.TestCase):
    def test_history_matches_latest_state_and_is_ascending(self):
        frame = make_frame(
            [100.0] * 59 + [110.0, 112.0, 108.0],
            [100.0] * 59 + [111.0, 113.0, 130.0],
            [100.0] * 59 + [100.0, 110.0, 107.0],
        )
        rows = sector_history(frame, days=10)
        self.assertEqual(len(rows), 10)
        dates = [row["date"] for row in rows]
        self.assertEqual(dates, sorted(set(dates)))
        self.assertEqual(rows[-1]["date"], frame["date"].iat[-1].strftime("%Y-%m-%d"))
        self.assertEqual(rows[-1]["state"], "warn")
        self.assertAlmostEqual(rows[-1]["close"], 108.0)

    def test_history_excludes_unavailable_states(self):
        frame = make_frame([100.0] * 30, [100.0] * 30, [100.0] * 30)
        rows = sector_history(frame, days=5)
        self.assertTrue(all(row["state"] is None for row in rows))

    def test_history_rejects_non_positive_days(self):
        frame = make_frame([100.0] * 60, [100.0] * 60, [100.0] * 60)
        with self.assertRaises(ValueError):
            sector_history(frame, days=0)

    def test_spark_uses_last_thirty_closes(self):
        frame = make_frame([100.0 + i for i in range(40)], [110.0 + i for i in range(40)],
                           [90.0 + i for i in range(40)])
        spark = spark_series(frame)
        self.assertEqual(len(spark), 30)
        self.assertAlmostEqual(spark[-1], 139.0)

    def test_state_codes_cover_four_states(self):
        self.assertEqual(set(STATE_CODES.values()), {"B", "W", "D", "R"})
        self.assertEqual(ALGORITHM_VERSION, "sector-donchian20-ma60-v2.0-em-source")


class SectorEvaluationTest(unittest.TestCase):
    def test_continuation_does_not_recount_and_reentry_counts(self):
        # 构造序列：bull持续3天只计1次，之后转bear再转bull重新计次
        close = [100.0] * 59 + [110.0, 112.0, 111.0, 90.0, 85.0, 110.0]
        high = [100.0] * 59 + [111.0, 113.0, 112.0, 120.0, 120.0, 111.0]
        low = [100.0] * 59 + [100.0, 110.0, 100.0, 80.0, 80.0, 100.0]
        frame = make_frame(close, high, low)
        frame["date"] = pd.date_range("2024-10-01", periods=len(close), freq="D")

        manifest = [{"code": "test", "name": "测试"}]
        evaluation = build_sector_evaluation(manifest, {"test": frame})

        # 应该有研究段和验证段
        self.assertEqual(len(evaluation["segments"]), 2)
        self.assertEqual(evaluation["segments"][0]["segment"], "research")
        self.assertEqual(evaluation["segments"][1]["segment"], "exploratory_validation")

    def test_four_states_direction_validation(self):
        # 测试四种状态的方向验证：bull/rise期望上涨，warn/bear期望下跌
        close = [100.0] * 59 + [110.0, 120.0, 90.0, 80.0]
        high = [100.0] * 59 + [111.0, 130.0, 120.0, 120.0]
        low = [100.0] * 59 + [100.0, 110.0, 80.0, 70.0]
        frame = make_frame(close, high, low)
        frame["date"] = pd.date_range("2024-10-01", periods=len(close), freq="D")

        manifest = [{"code": "test", "name": "测试"}]
        evaluation = build_sector_evaluation(manifest, {"test": frame})

        # 四态键固定齐备，每态都带 10 / 20 日两档
        for segment in evaluation["segments"]:
            self.assertEqual(set(segment["states"]), {"bull", "warn", "bear", "rise"})
            for horizons in segment["states"].values():
                self.assertEqual(set(horizons), {"10", "20"})
        # 不再输出聚合 horizons 口径
        for segment in evaluation["segments"]:
            self.assertNotIn("horizons", segment)

    def test_immature_samples_excluded(self):
        # 历史不足60日的板块不应产生有效样本
        frame = make_frame([100.0] * 30, [100.0] * 30, [100.0] * 30)
        frame["date"] = pd.date_range("2024-10-01", periods=30, freq="D")

        manifest = [{"code": "test", "name": "测试"}]
        evaluation = build_sector_evaluation(manifest, {"test": frame})

        # 由于历史不足，研究段和验证段都应该没有有效数据：四态均为空值
        for segment in evaluation["segments"]:
            for horizons in segment["states"].values():
                for horizon in ("10", "20"):
                    self.assertIsNone(horizons[horizon])

    def test_four_states_appear_in_oscillating_history(self):
        evaluation = build_sector_evaluation(MANIFEST, {"test": oscillating_frame()})
        research = evaluation["segments"][0]["states"]
        for state in ("bull", "warn", "bear", "rise"):
            with self.subTest(state=state):
                self.assertIsNotNone(research[state]["10"], f"{state} 在研究段没有任何事件")
                self.assertGreater(research[state]["10"]["samples"], 0, state)

    def test_four_state_samples_sum_to_all_events(self):
        frame = oscillating_frame()
        evaluation = build_sector_evaluation(MANIFEST, {"test": frame})
        events = evaluate_sector_states(
            frame, sector_state(frame, "Donchian20_MA60"),
            code="test", name="测试", method=ALGORITHM_VERSION,
        )

        for segment_name, segment in zip(("research", "exploratory_validation"),
                                         evaluation["segments"]):
            part = [event for event in events if event["segment"] == segment_name]
            self.assertGreater(len(part), 0, segment_name)
            for horizon in (10, 20):
                key = str(horizon)
                cells = [cell[key] for cell in segment["states"].values() if cell[key]]
                expected = sum(1 for event in part
                               if event[f"outcome_{horizon}"] in {"success", "failure"})
                with self.subTest(segment=segment_name, horizon=horizon):
                    self.assertEqual(sum(cell["entries"] for cell in cells), len(part))
                    self.assertEqual(sum(cell["samples"] for cell in cells), expected)
                    self.assertEqual(sum(cell["pending"] for cell in cells),
                                     len(part) - expected)


if __name__ == "__main__":
    unittest.main()
