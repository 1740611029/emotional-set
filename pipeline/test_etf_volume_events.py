"""ETF 放量识别与分层统计纯函数测试（全部离线、确定性合成数据）。"""
from __future__ import annotations

import random
import unittest
from datetime import date, timedelta

import pandas as pd

import etf_volume_events as module

CODES = [f"ETF{index}" for index in range(1, 7)]


def _frame(amounts: dict[str, float], code: str = "ETF1") -> pd.DataFrame:
    days = sorted(amounts)
    return pd.DataFrame({
        "date": days, "open": [1.0] * len(days), "close": [1.0] * len(days),
        "high": [1.0] * len(days), "low": [1.0] * len(days),
        "volume": [1.0] * len(days), "amount": [amounts[day] for day in days],
    })


def _index_frame(closes: list[float], start: str = "2023-01-02") -> pd.DataFrame:
    days = []
    cursor = date.fromisoformat(start)
    while len(days) < len(closes):
        if cursor.weekday() < 5:
            days.append(cursor.isoformat())
        cursor += timedelta(days=1)
    return pd.DataFrame({"date": days, "close": closes})


def _sawtooth(closes: int = 400) -> pd.DataFrame:
    """每 10 日一轮锯齿：i%10==0 起涨、i%10==5 起跌，便于构造胜率差异。"""
    return _index_frame([100.0 + (index % 10) for index in range(closes)])


class PreregisteredTest(unittest.TestCase):
    def test_all_frozen_parameters_are_present(self):
        for key in ("algorithm_version", "放量阈值(元)", "普通放量基线带(元)", "簇合并间隔(交易日)",
                    "状态基准指数", "趋势均线窗口", "回撤观察窗口", "底部区回撤阈值",
                    "上行区回撤阈值", "前瞻期(交易日)", "单层样本下限", "L3样本下限"):
            self.assertIn(key, module.PREREGISTERED)
        self.assertEqual(module.PREREGISTERED["放量阈值(元)"], 1e10)
        self.assertEqual(module.PREREGISTERED["簇合并间隔(交易日)"], 2)
        self.assertEqual(module.PREREGISTERED["前瞻期(交易日)"], [1, 3, 5, 10, 20])


class ClassifyLevelTest(unittest.TestCase):
    def test_levels_follow_hit_count(self):
        self.assertIsNone(module.classify_level(0))
        self.assertEqual(module.classify_level(1), "L1")
        for hits in (2, 4, 5):
            self.assertEqual(module.classify_level(hits), "L2")
        self.assertEqual(module.classify_level(6), "L3")


class DailyRecordTest(unittest.TestCase):
    def test_threshold_and_level_by_hit_count(self):
        frames = {
            "ETF1": _frame({"2026-11-02": 1.1e10, "2026-11-03": 1.0e10}),
            "ETF2": _frame({"2026-11-02": 1.2e10, "2026-11-03": 9.9e9}),
            "ETF3": _frame({"2026-11-02": 2.0e10, "2026-11-03": 5.0e9}),
        }
        records = module.daily_records(frames)
        self.assertEqual([row["date"] for row in records], ["2026-11-02", "2026-11-03"])
        self.assertEqual(records[0]["level"], "L2")
        self.assertEqual(records[0]["etf_codes"], ["ETF1", "ETF2", "ETF3"])
        self.assertAlmostEqual(records[0]["total_amount"], 4.3e10)
        self.assertEqual(records[1]["level"], "L1")
        self.assertEqual(records[1]["etf_codes"], ["ETF1"])

    def test_normal_volume_band_excludes_threshold_days(self):
        frames = {"ETF1": _frame({
            "2026-11-02": 7.0e9,   # 带内 → 普通放量
            "2026-11-03": 1.5e10,  # 达标 → 不算普通放量
            "2026-11-04": 4.0e9,   # 带下 → 不算
        })}
        self.assertEqual(module.normal_volume_days(frames), ["2026-11-02"])


class MergeClusterTest(unittest.TestCase):
    CALENDAR = [f"2026-11-{day:02d}" for day in range(2, 13)]

    def test_gap_boundary_merges_and_splits(self):
        merged = module.merge_clusters(self.CALENDAR, ["2026-11-02", "2026-11-04"], gap=2)
        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0]["days"], 2)
        split = module.merge_clusters(self.CALENDAR, ["2026-11-02", "2026-11-05"], gap=2)
        self.assertEqual(len(split), 2)
        self.assertEqual(split[1]["start_date"], "2026-11-05")

    def test_event_day_is_first_volume_day_not_peak_day(self):
        merged = module.merge_clusters(self.CALENDAR, ["2026-11-02", "2026-11-03", "2026-11-04"], gap=2)
        self.assertEqual(merged[0]["start_date"], "2026-11-02")
        self.assertEqual(merged[0]["end_date"], "2026-11-04")

    def test_identify_uses_first_day_even_when_later_days_are_larger(self):
        frames = {
            "ETF1": _frame({"2026-11-02": 1.1e10, "2026-11-03": 5.0e10}),
            "ETF2": _frame({"2026-11-02": 1.1e10, "2026-11-03": 5.0e10}),
        }
        index_frame = _index_frame([100.0] * 300)
        result = module.identify(frames, index_frame)
        self.assertEqual(result["events"][0]["start_date"], "2026-11-02")
        self.assertEqual(result["events"][0]["end_date"], "2026-11-03")
        self.assertAlmostEqual(result["events"][0]["peak_amount"], 1.0e11)

    def test_level_of_cluster_stays_l3_when_first_day_is_l3(self):
        frames = {code: _frame({"2026-11-02": 1.1e10}) for code in CODES}
        for extra in CODES[:2]:
            frames[extra] = _frame({"2026-11-02": 1.1e10, "2026-11-03": 1.1e10})
        result = module.identify(frames, _index_frame([100.0] * 300))
        self.assertEqual(result["events"][0]["level"], "L3")


class MarketStateTest(unittest.TestCase):
    def test_three_states_are_exclusive_and_complete(self):
        random.seed(20261106)
        seen = set()
        for _ in range(200):
            closes, value = [], 100.0
            for _ in range(300):
                value *= 1 + random.uniform(-0.02, 0.02)
                closes.append(value)
            frame = _index_frame(closes)
            state = module.market_state(frame, frame["date"].iat[-1])
            self.assertIn(state, module.STATES)
            seen.add(state)
        self.assertEqual(seen, set(module.STATES))

    def test_bottom_and_uptrend_thresholds(self):
        falling = _index_frame([200.0 - index * 0.5 for index in range(300)])
        rising = _index_frame([100.0 + index * 0.5 for index in range(300)])
        self.assertEqual(module.market_state(falling, falling["date"].iat[-1]), module.STATE_BOTTOM)
        self.assertEqual(module.market_state(rising, rising["date"].iat[-1]), module.STATE_UPTREND)

    def test_state_uses_only_history_up_to_the_day(self):
        closes = [100.0 + (index % 20) for index in range(300)]
        frame = _index_frame(closes)
        day = frame["date"].iat[260]
        tampered = frame.copy()
        tampered.loc[tampered.index > 260, "close"] = 1.0
        self.assertEqual(module.market_state(frame, day), module.market_state(tampered, day))

    def test_short_history_is_neutral_not_crashing(self):
        frame = _index_frame([100.0] * 30)
        self.assertEqual(module.market_state(frame, frame["date"].iat[-1]), module.STATE_NEUTRAL)


class ForwardStatsTest(unittest.TestCase):
    def _events(self, index_frame: pd.DataFrame, offsets: int, level: str = "L2",
                state: str = module.STATE_UPTREND, count: int = 10) -> list[dict]:
        days = index_frame["date"].tolist()
        picks = [days[index] for index in range(offsets, len(days) - 25, 10)][:count]
        return [{"start_date": day, "level": level, "state": state} for day in picks]

    def test_wilson_interval_bounds(self):
        low, high = module.wilson_interval(8, 10)
        self.assertTrue(0 <= low <= 0.8 <= high <= 1)
        self.assertEqual(module.wilson_interval(0, 0), (None, None))

    def test_small_sample_is_reported_as_undecidable(self):
        index_frame = _sawtooth()
        events = self._events(index_frame, 0, level="L3", count=2)
        stats = module.forward_stats(events, index_frame, baselines={
            "same_state": {module.STATE_UPTREND: []}, "normal_volume": []})
        layer = next(item for item in stats["layers"] if item["key"] == "L3")
        self.assertEqual(layer["horizons"]["5"]["verdict"], module.VERDICT_UNKNOWN)
        self.assertTrue(layer["horizons"]["5"]["small_sample"])

    def test_missing_baseline_never_yields_true(self):
        index_frame = _sawtooth()
        events = self._events(index_frame, 0, count=10)
        stats = module.forward_stats(events, index_frame)
        layer = next(item for item in stats["layers"] if item["key"] == "L2")
        self.assertEqual(layer["horizons"]["5"]["verdict"], module.VERDICT_NO_BASELINE)
        self.assertNotIn(module.VERDICT_TRUE,
                         {cell["verdict"] for cell in layer["horizons"].values()})

    def test_beating_baseline_is_true_and_lagging_is_false(self):
        index_frame = _sawtooth()
        days = index_frame["date"].tolist()
        uptrend_days = [days[index] for index in range(0, len(days) - 25, 10)]  # 锯齿起点，前瞻为正
        fall_days = [days[index] for index in range(5, len(days) - 25, 10)]     # 锯齿顶点，前瞻为负
        baselines = {"same_state": {module.STATE_UPTREND: fall_days}, "normal_volume": []}
        events = [{"start_date": day, "level": "L2", "state": module.STATE_UPTREND}
                  for day in uptrend_days[:12]]
        winners = module.forward_stats(events, index_frame, baselines=baselines)
        layer = next(item for item in winners["layers"] if item["key"] == "L2")
        self.assertEqual(layer["horizons"]["5"]["verdict"], module.VERDICT_TRUE)

        lagging = [{"start_date": day, "level": "L2", "state": module.STATE_UPTREND}
                   for day in fall_days[:12]]
        losers = module.forward_stats(lagging, index_frame, baselines=baselines)
        lagging_layer = next(item for item in losers["layers"] if item["key"] == "L2")
        self.assertEqual(lagging_layer["horizons"]["5"]["verdict"], module.VERDICT_FALSE)

    def test_horizon_range_only_includes_mature_samples(self):
        history = _index_frame([100.0 + index for index in range(30)])
        dates = history["date"].tolist()
        stats = module.layer_stats(history, [dates[0], dates[15], dates[29]], horizons=(5, 20, 40))
        self.assertEqual(stats["n"], 3)
        for horizon, expected in (("5", (2, dates[0], dates[15])),
                                  ("20", (1, dates[0], dates[0])),
                                  ("40", (0, None, None))):
            cell = stats["horizons"][horizon]
            self.assertEqual((cell["samples"], cell["start"], cell["end"]), expected)

    def test_layer_stats_reports_sample_range_and_drawdown(self):
        index_frame = _sawtooth()
        days = index_frame["date"].tolist()
        stats = module.layer_stats(index_frame, days[:20], horizons=(5,))
        self.assertEqual(stats["n"], 20)
        self.assertEqual(stats["start"], days[0])
        self.assertEqual(stats["end"], days[19])
        cell = stats["horizons"]["5"]
        self.assertEqual(cell["samples"], 20)
        self.assertLessEqual(cell["avg_max_drawdown"], 0)
        self.assertEqual(len(cell["wilson95"]), 2)


class NoLookaheadTest(unittest.TestCase):
    """截断某日之后的全部数据后重跑，该日及之前的事件、级别与状态必须完全不变。"""

    def test_open_cluster_level_never_uses_later_hits_or_prices(self):
        history = _index_frame([100 + index * 0.1 for index in range(270)])
        dates = history["date"].tolist()
        # 同一簇从 L1 逐日升至 L2、L3；在簇内每个交易日截断并篡改后置数据。
        frames = {code: _frame({day: (
            1.1e10 if index >= 260 and position < min(index - 259, 6) else 1e9
        ) for index, day in enumerate(dates)}) for position, code in enumerate(CODES)}
        full = module.identify(frames, history)
        self.assertEqual(full["events"][0]["level"], "L1")
        self.assertEqual(full["days"][-1]["level"], "L3")
        stable = lambda rows: [{key: row[key] for key in ("start_date", "level", "state")}
                               for row in rows]
        for cut in dates[260:]:
            with self.subTest(cut=cut):
                part = module.identify(
                    {code: frame[frame["date"] <= cut] for code, frame in frames.items()},
                    history[history["date"] <= cut],
                )
                self.assertEqual(part["days"], [row for row in full["days"] if row["date"] <= cut])
                self.assertEqual(stable(part["events"]), stable(full["events"]))
                self.assertEqual(part["events"][0]["end_date"], cut)
                changed = {code: frame.copy() for code, frame in frames.items()}
                for frame in changed.values():
                    frame.loc[frame["date"] > cut, "amount"] = 0
                changed_history = history.copy()
                changed_history.loc[changed_history["date"] > cut, "close"] = 1
                tampered = module.identify(changed, changed_history)
                self.assertEqual(stable(tampered["events"]), stable(part["events"]))


    def _dataset(self):
        days = []
        cursor = date(2023, 1, 2)
        while len(days) < 400:
            if cursor.weekday() < 5:
                days.append(cursor.isoformat())
            cursor += timedelta(days=1)
        frames = {}
        for position, code in enumerate(CODES):
            amounts = {}
            for index, day in enumerate(days):
                # 常态成交额远低于阈值，确保只有刻意构造的日子才是放量日
                amounts[day] = (1.0e9 + (index % 7) * 1.0e8) * (1 - position * 0.02)
            for index in range(40, 44):  # 第一簇：6 只全中
                amounts[days[index]] = 1.5e10
            for index in range(50, 52):  # 第二簇：仅前 2 只
                if position < 2:
                    amounts[days[index]] = 1.4e10
            frames[code] = _frame(amounts, code)
        closes = [100.0 + 20 * ((index % 60) / 60) for index in range(400)]
        return frames, _index_frame(closes), days[60]

    def test_events_and_states_before_cut_are_unchanged(self):
        frames, index_frame, cut = self._dataset()
        full = module.identify(frames, index_frame)
        truncated_frames = {code: frame[frame["date"] <= cut] for code, frame in frames.items()}
        truncated_index = index_frame[index_frame["date"] <= cut]
        part = module.identify(truncated_frames, truncated_index)

        cut_days_full = [row for row in full["days"] if row["date"] <= cut]
        self.assertTrue(cut_days_full)
        self.assertEqual(cut_days_full, part["days"])

        cut_events_full = [row for row in full["events"] if row["start_date"] <= cut]
        self.assertEqual(cut_events_full, part["events"])
        self.assertEqual(len(part["events"]), 2)
        for row in part["events"]:
            self.assertEqual(row["state"], module.market_state(truncated_index, row["start_date"]))


if __name__ == "__main__":
    unittest.main()
