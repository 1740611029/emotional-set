"""服务层测试：快照契约、盘中合成、实时源归一化、接口状态。"""
from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd

from . import app as app_module
from . import etf_volume as etf_volume_module
from . import paths
from . import snapshot as snapshot_module
from .app import SECTOR_STORE, STORE, health, index_history, sector_history, snapshot
from .etf_volume import EtfVolumeStore
from .institutions import InstitutionStore, slice_payload, summary, validate_params
from .realtime import CN, _build_quote, _normalize_volume
from .scheduler import (
    EVENING_SLOTS, MAX_CLOSE_ATTEMPTS, MarketScheduler, SCHEDULER_VERSION, _stale_detail,
)
from .snapshot import (
    PayloadStore, SnapshotStore, _sector_block, breadth_frame, build_close_snapshot, build_live_index,
    _merged_frame,
)
from fastapi import HTTPException
from fastapi.testclient import TestClient


def _payload() -> dict:
    return json.loads(paths.INDEX_SIGNALS.read_text(encoding="utf-8"))


def _sector_payload() -> dict:
    return json.loads(paths.SECTOR_TRENDS.read_text(encoding="utf-8"))


def _frame(periods: int = 80) -> pd.DataFrame:
    close = [100.0 + (index % 5) for index in range(periods)]
    return pd.DataFrame({
        "date": pd.bdate_range("2026-01-05", periods=periods),
        "open": [value - 0.5 for value in close],
        "high": [value + 1 for value in close],
        "low": [value - 1 for value in close],
        "close": close,
        "volume": [1_000_000.0] * periods,
        "amount": [1_000_000_000.0] * periods,
    })


QUOTE = {
    "code": "sh000001", "name": "上证指数",
    "open": 101.0, "high": 103.0, "low": 99.0, "close": 102.0, "volume": 1_200_000.0,
    "ts": "2026-10-30T10:00:00+08:00",
}


class CloseSnapshotTest(unittest.TestCase):
    def test_snapshot_contract_fields(self):
        data = build_close_snapshot(_payload(), _sector_payload())
        for key in ("mode", "as_of", "generated_at", "stale", "data_state", "indices",
                    "sectors", "recent_signals", "candidates", "algorithm_version"):
            self.assertIn(key, data)
        self.assertEqual(data["mode"], "close")
        self.assertFalse(data["provisional"])
        self.assertEqual(len(data["indices"]), 7)
        item = data["indices"][0]
        for key in ("code", "name", "latest", "change_pct", "current_signal",
                    "history", "signals", "accuracy", "candidates", "provisional"):
            self.assertIn(key, item)
        self.assertFalse(item["provisional"])

    def test_accuracy_carries_sample_and_range(self):
        data = build_close_snapshot(_payload(), _sector_payload())
        for item in data["indices"]:
            accuracy = item["accuracy"]
            self.assertIn("start", accuracy)
            self.assertIn("end", accuracy)
            for kind in ("bottom", "top"):
                self.assertIn("triggers", accuracy[kind])
                self.assertIn("successes", accuracy[kind])
                self.assertIn("rate", accuracy[kind])


class MergedFrameTest(unittest.TestCase):
    def test_appends_when_last_date_is_older(self):
        frame = _frame()
        merged = _merged_frame(frame, QUOTE)
        self.assertEqual(len(merged), len(frame) + 1)
        self.assertEqual(merged["date"].is_monotonic_increasing, True)
        self.assertEqual(merged["date"].is_unique, True)
        self.assertAlmostEqual(float(merged["close"].iat[-1]), QUOTE["close"])

    def test_replaces_when_today_already_exists(self):
        frame = _frame()
        today = pd.Timestamp.now(tz="Asia/Shanghai").normalize().tz_localize(None)
        frame.loc[frame.index[-1], "date"] = today
        merged = _merged_frame(frame, QUOTE)
        self.assertEqual(len(merged), len(frame))
        self.assertAlmostEqual(float(merged["close"].iat[-1]), QUOTE["close"])

    def test_input_frame_is_not_mutated(self):
        frame = _frame()
        before = frame.copy(deep=True)
        _merged_frame(frame, QUOTE)
        pd.testing.assert_frame_equal(frame, before)


class LiveIndexTest(unittest.TestCase):
    def test_live_index_is_provisional_and_has_candidates(self):
        frame = _frame()
        item = build_live_index(frame, QUOTE, "上证指数")
        self.assertTrue(item["provisional"])
        self.assertIn("candidates", item)
        self.assertEqual(item["history"][-1]["date"],
                         pd.Timestamp.now(tz="Asia/Shanghai").strftime("%Y-%m-%d"))
        self.assertAlmostEqual(item["latest"], QUOTE["close"])

    def test_live_index_does_not_touch_disk(self):
        before = paths.INDEX_SIGNALS.stat().st_mtime
        build_live_index(_frame(), QUOTE, "上证指数")
        self.assertEqual(paths.INDEX_SIGNALS.stat().st_mtime, before)

    def test_live_accuracy_ignores_provisional_bar(self):
        from confirmed_index_signals import build_index_result  # noqa: PLC0415

        frame = _frame()
        item = build_live_index(frame, QUOTE, "上证指数")
        self.assertEqual(item["accuracy"], build_index_result(frame)["accuracy"])


class RealtimeNormalizeTest(unittest.TestCase):
    def test_volume_scaled_when_history_is_hundred_times_larger(self):
        self.assertAlmostEqual(_normalize_volume(1_000.0, [100_000.0] * 20), 100_000.0)

    def test_volume_kept_when_already_comparable(self):
        self.assertAlmostEqual(_normalize_volume(100_000.0, [100_000.0] * 20), 100_000.0)

    def test_invalid_ohlc_is_rejected(self):
        self.assertIsNone(_build_quote("sh000001", "上证指数", 100.0, 100.0, 99.0, 98.0, 1000.0, None))
        self.assertIsNone(_build_quote("sh000001", "上证指数", 0.0, 100.0, 101.0, 99.0, 1000.0, None))

    def test_valid_quote_is_returned(self):
        quote = _build_quote("sh000001", "上证指数", 102.0, 100.0, 103.0, 99.0, 1000.0, None)
        self.assertIsNotNone(quote)
        self.assertEqual(quote["code"], "sh000001")


class ApiStateTest(unittest.TestCase):
    def tearDown(self):
        STORE.set(None)

    def test_preparing_state_when_store_empty(self):
        STORE.set(None)
        response = snapshot()
        self.assertEqual(response.status_code, 202)
        body = json.loads(response.body)
        self.assertEqual(body["data_state"], "preparing")
        self.assertEqual(body["indices"], [])

    def test_ready_state_after_store_filled(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload()))
        data = snapshot()
        self.assertEqual(data["data_state"], "ready")
        self.assertEqual(len(data["indices"]), 7)

    def test_health_reports_mode_and_source(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload()))
        status = health()
        self.assertTrue(status["alive"])
        self.assertEqual(status["mode"], "close")
        self.assertIn("data_source", status)

    def test_history_rejects_unknown_code(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload()))
        with self.assertRaises(Exception):
            index_history("sh999999")

    def test_history_returns_series(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload()))
        data = index_history("sh000001")
        self.assertEqual(data["code"], "sh000001")
        self.assertGreater(len(data["history"]), 1000)
        dates = [row["date"] for row in data["history"]]
        self.assertEqual(dates, sorted(dates))


class EtfVolumeEventsTest(unittest.TestCase):
    """ETF 放量事件：只是展示标记，产物存在/缺失/损坏都不能影响快照其余字段。"""

    EVENTS = [
        {"date": "2024-09-30", "level": "L3",
         "etf_codes": ["510300", "510050"], "total_amount": 12_000_000_000.0},
        {"date": "2026-11-03", "level": "L2",
         "etf_codes": ["510300"], "total_amount": 1_050_000_000.0},
    ]

    def tearDown(self):
        STORE.set(None)

    def _store(self, target: Path) -> PayloadStore:
        """把事件产物指到临时文件：用例不依赖生产产物是否存在。"""
        patcher = mock.patch.object(paths, "ETF_VOLUME_EVENTS", target)
        patcher.start()
        self.addCleanup(patcher.stop)
        return PayloadStore()

    def test_payload_is_projected_to_four_fields_and_sorted(self):
        rows = [
            {"date": "2026-11-03", "level": "L3", "etf_codes": ["510300"],
             "total_amount": 1_050_000_000.0, "extra": "多余字段必须丢弃"},
            {"date": "2024-09-30", "level": "L1", "etf_codes": ["510050"],
             "total_amount": 210_000_000.0},
        ]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "etf-volume-events.json"
            target.write_text(json.dumps({"days": rows}, ensure_ascii=False), encoding="utf-8")
            events = self._store(target).etf_volume_events()
        self.assertEqual([row["date"] for row in events], ["2024-09-30", "2026-11-03"])
        for row in events:
            self.assertEqual(set(row), {"date", "level", "etf_codes", "total_amount"})
            self.assertIn(row["level"], {"L1", "L2", "L3"})
            self.assertGreater(row["total_amount"], 0)
        self.assertEqual(events[0]["etf_codes"], ["510050"])

    def test_missing_payload_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            events = self._store(Path(temp) / "missing.json").etf_volume_events()
        self.assertEqual(events, [])

    def test_broken_payload_degrades_to_empty(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "etf-volume-events.json"
            target.write_text("{not json", encoding="utf-8")
            with contextlib.redirect_stdout(io.StringIO()):
                events = self._store(target).etf_volume_events()
        self.assertEqual(events, [])

    def test_version_tracks_event_product(self):
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "etf-volume-events.json"
            store = self._store(target)
            self.assertEqual(store.version()[2], 0.0)
            target.write_text(json.dumps({"days": []}), encoding="utf-8")
            self.assertEqual(store.version()[2], target.stat().st_mtime)

    def test_snapshot_carries_events_without_touching_other_fields(self):
        with_events = build_close_snapshot(_payload(), _sector_payload(), etf_volume_events=self.EVENTS)
        without = build_close_snapshot(_payload(), _sector_payload())
        self.assertEqual(with_events["etf_volume_events"], self.EVENTS)
        self.assertEqual(without["etf_volume_events"], [])
        for key in without:
            if key in ("etf_volume_events", "generated_at"):
                continue
            self.assertEqual(with_events[key], without[key], f"{key} 被放量事件影响")

    def test_live_snapshot_carries_events(self):
        from .snapshot import build_live_snapshot  # noqa: PLC0415

        history = mock.Mock()
        history.get.return_value = _frame()
        with mock.patch.object(snapshot_module, "breadth_frame", return_value=None):
            data = build_live_snapshot({"sh000001": QUOTE}, history, _sector_payload(),
                                       etf_volume_events=self.EVENTS)
        self.assertEqual(data["etf_volume_events"], self.EVENTS)

    def test_snapshot_and_history_share_same_events(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload(), etf_volume_events=self.EVENTS))
        body = snapshot()
        data = index_history("sh000001")
        self.assertEqual(data["etf_volume_events"], body["etf_volume_events"])
        self.assertEqual([row["date"] for row in data["etf_volume_events"]],
                         ["2024-09-30", "2026-11-03"])

    def test_history_keeps_all_other_fields_unchanged(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload()))
        before = index_history("sh000300")
        self.assertEqual(before["etf_volume_events"], [])
        STORE.set(build_close_snapshot(_payload(), _sector_payload(), etf_volume_events=self.EVENTS))
        after = index_history("sh000300")
        self.assertEqual(set(after), set(before))
        for key in before:
            if key == "etf_volume_events":
                continue
            self.assertEqual(after[key], before[key], f"{key} 被放量事件影响")

    def test_preparing_branch_returns_empty_events(self):
        STORE.set(None)
        response = snapshot()
        self.assertEqual(response.status_code, 202)
        self.assertEqual(json.loads(response.body)["etf_volume_events"], [])

    def test_scheduler_rebuild_injects_events(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(scheduler.payloads, "etf_volume_events",
                               return_value=self.EVENTS):
            scheduler._rebuild_close()
        self.assertEqual(scheduler.store.get()["etf_volume_events"], self.EVENTS)


class AsOfConsistencyTest(unittest.TestCase):
    """收盘与盘中 as_of 由同一规则导出；短板标的随日期一起返回（D5）。"""

    def _tailed_payload(self) -> dict:
        payload = _payload()
        laggard = payload["indices"][0]["code"]
        tails = {item["code"]: payload["as_of"] for item in payload["indices"]}
        tails[laggard] = "2020-01-01"  # 尾日落后
        payload["index_tails"] = tails
        payload["as_of_rule"] = "min(index_tails)"
        payload["lagging_indexes"] = [laggard]
        return payload

    def test_close_snapshot_exposes_tails_and_rule(self):
        payload = self._tailed_payload()
        data = build_close_snapshot(payload, _sector_payload())
        self.assertEqual(data["as_of_rule"], "min(index_tails)")
        self.assertEqual(len(data["index_tails"]), 7)
        self.assertIn(payload["lagging_indexes"][0], data["lagging_indexes"])

    def test_live_snapshot_uses_same_rule_as_close(self):
        from .snapshot import build_live_snapshot  # noqa: PLC0415

        payload = self._tailed_payload()
        history = mock.Mock()
        history.get.return_value = _frame()
        with mock.patch.object(snapshot_module, "breadth_frame", return_value=None):
            close_data = build_close_snapshot(payload, _sector_payload())
            live_data = build_live_snapshot({"sh000001": QUOTE}, history, _sector_payload(),
                                            indices_payload=payload)
        # 同一批原始日线：收盘/盘中 as_of 必须一致，不差一个交易日
        self.assertEqual(live_data["as_of"], close_data["as_of"])
        self.assertEqual(live_data["as_of_rule"], close_data["as_of_rule"])
        self.assertEqual(live_data["lagging_indexes"], close_data["lagging_indexes"])

    def test_live_snapshot_fallback_marks_rule(self):
        """收盘产物缺失时退回盘中兜底口径并显式标注，不冒充 min(index_tails)。"""
        from .snapshot import build_live_snapshot  # noqa: PLC0415

        history = mock.Mock()
        history.get.return_value = _frame()
        with mock.patch.object(snapshot_module, "breadth_frame", return_value=None):
            data = build_live_snapshot({"sh000001": QUOTE}, history, _sector_payload())
        self.assertNotEqual(data["as_of_rule"], "min(index_tails)")
        self.assertIn("兜底", data["as_of_rule"])


class SectorSnapshotTest(unittest.TestCase):
    def test_snapshot_sectors_drop_long_series(self):
        data = build_close_snapshot(_payload(), _sector_payload())
        block = data["sectors"]
        self.assertNotIn("dates", block)
        self.assertIn("algorithm_version", block)
        item = next(iter(block["groups"].values()))[0]
        self.assertNotIn("series", item)
        for key in ("state", "strength", "strength_parts", "strength_basis", "days",
                    "date", "data_lag", "data_anomaly", "spark"):
            self.assertIn(key, item)

    def test_snapshot_sector_entries_are_flat(self):
        """单层板块：条目不带层级、父级与二级列表，也不带二级评估。"""
        block = build_close_snapshot(_payload(), _sector_payload())["sectors"]
        self.assertNotIn("subsector_evaluation", block)
        self.assertGreater(len([i for rows in block["groups"].values() for i in rows]), 0)
        for rows in block["groups"].values():
            for item in rows:
                for key in ("level", "parent", "subsectors"):
                    self.assertNotIn(key, item, f"{item.get('code')} 仍带 {key}")

    def test_snapshot_carries_four_state_evaluation(self):
        """四态评估随快照返回：两段 × 四态 × 10/20，不再有聚合 horizons。"""
        block = build_close_snapshot(_payload(), _sector_payload())["sectors"]
        evaluation = block["evaluation"]
        self.assertIsNotNone(evaluation)
        self.assertIn("方向胜率不代表交易收益", evaluation["note"])
        self.assertEqual([s["segment"] for s in evaluation["segments"]],
                         ["research", "exploratory_validation"])
        for segment in evaluation["segments"]:
            self.assertNotIn("horizons", segment)
            self.assertEqual(set(segment["states"]), {"bull", "warn", "bear", "rise"})
            for cell in segment["states"].values():
                self.assertEqual(set(cell), {"10", "20"})
                for horizon in cell.values():
                    if horizon is None:
                        continue
                    for key in ("rate", "samples", "successes", "pending", "wilson95",
                                "small_sample"):
                        self.assertIn(key, horizon)

    def test_missing_payload_returns_placeholder(self):
        block = _sector_block(None)
        self.assertTrue(block["is_mock"])
        self.assertEqual(block["groups"], {})
        self.assertNotIn("subsector_evaluation", block)
        self.assertIsNone(block["evaluation"])

    def test_absent_evaluation_is_not_fabricated(self):
        """评估尚未生成时返回 null，前端据此显示「评估尚未生成」，不得给示例数值。"""
        payload = _sector_payload()
        payload["evaluation"] = None
        block = _sector_block(payload)
        self.assertIsNone(block["evaluation"])
        self.assertGreater(len([i for rows in block["groups"].values() for i in rows]), 0)


class SectorFreshnessTest(unittest.TestCase):
    """板块滞后字段：前端据此提示"数据截至 X，落后 N 个交易日"。"""

    def test_block_reports_expected_and_lag(self):
        payload = {"as_of": "2026-10-30", "is_mock": False, "groups": {},
                   "algorithm_version": "x", "evaluation": None}
        with mock.patch.object(snapshot_module, "last_completed_trading_day",
                               return_value=("2026-11-02", "calendar")), \
                mock.patch.object(snapshot_module, "lag_trading_days", return_value=1):
            block = _sector_block(payload)
        self.assertEqual(block["expected_as_of"], "2026-11-02")
        self.assertEqual(block["expected_basis"], "calendar")
        self.assertEqual(block["lag_days"], 1)

    def test_up_to_date_block_reports_zero_lag(self):
        payload = {"as_of": "2026-11-02", "is_mock": False, "groups": {},
                   "algorithm_version": "x", "evaluation": None}
        with mock.patch.object(snapshot_module, "last_completed_trading_day",
                               return_value=("2026-11-02", "calendar")), \
                mock.patch.object(snapshot_module, "lag_trading_days", return_value=0):
            block = _sector_block(payload)
        self.assertEqual(block["lag_days"], 0)

    def test_placeholder_keeps_freshness_fields(self):
        with mock.patch.object(snapshot_module, "last_completed_trading_day",
                               return_value=("2026-11-02", "workday")):
            block = _sector_block(None)
        self.assertTrue(block["is_mock"])
        self.assertEqual(block["expected_as_of"], "2026-11-02")
        self.assertEqual(block["expected_basis"], "workday")
        self.assertIsNone(block["lag_days"])


class BreadthSourceTest(unittest.TestCase):
    """盘中宽度必须与收盘同源：读同花顺行业指数目录，不足 20 个只降级为 None。"""

    def _make_directory(self, temp: str, count: int) -> Path:
        directory = Path(temp)
        for index in range(count):
            (directory / f"8811{index:02d}.csv").write_text(
                "date,open,close,high,low,volume,amount\n2026-11-03,1,1,1,1,1,1\n", encoding="utf-8")
        return directory

    def _patched(self, directory: Path, table=None):
        patches = [
            mock.patch.object(paths, "SECTOR_THS_RAW", directory),
            mock.patch.object(snapshot_module, "_BREADTH_CACHE", {"mtime": None, "frame": None}),
        ]
        if table is not None:
            patches.append(mock.patch.object(snapshot_module, "breadth_table", table))
        return patches

    def test_directory_matches_production_sector_raw(self):
        """防路径漂移：盘中宽度与 build_dashboard.load_breadth 读同一个目录。"""
        from fetch_market import THS_SECTOR_RAW  # noqa: PLC0415  # 只在测试里引入，不影响服务启动链路

        self.assertEqual(paths.SECTOR_THS_RAW, THS_SECTOR_RAW)

    def test_insufficient_sectors_degrade_to_none(self):
        with tempfile.TemporaryDirectory() as temp:
            patches = self._patched(self._make_directory(temp, 19))
            with patches[0], patches[1]:
                self.assertIsNone(breadth_frame())

    def test_enough_sectors_build_frame_from_em_directory(self):
        seen: dict = {}

        def fake_table(data):
            seen.update(data)
            return pd.DataFrame({"breadth20": [0.5]})

        with tempfile.TemporaryDirectory() as temp:
            patches = self._patched(self._make_directory(temp, 21), fake_table)
            with patches[0], patches[1], patches[2]:
                frame = breadth_frame()
        self.assertIsNotNone(frame)
        self.assertEqual(len(seen), 21)
        self.assertEqual(sorted(seen)[:2], ["881100", "881101"])


class CloseAttemptBudgetTest(unittest.IsolatedAsyncioTestCase):
    """收盘重算尝试次数按日重置：长期运行的服务不能因累计失败而永不刷新。"""

    async def test_attempts_reset_on_new_day(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        scheduler._close_attempts = MAX_CLOSE_ATTEMPTS
        scheduler._close_date = "2026-11-02"
        scheduler._close_retry_at = datetime(2026, 11, 3, 10, 0, tzinfo=CN)
        with mock.patch.object(scheduler, "_rebuild_close"):
            await scheduler._tick_close(datetime(2026, 11, 3, 10, 0, tzinfo=CN))
        self.assertEqual(scheduler._close_attempts, 0)
        self.assertIsNone(scheduler._close_retry_at)
        self.assertEqual(scheduler._close_day, "2026-11-03")

    async def test_refresh_still_runs_after_reset(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        scheduler._close_attempts = MAX_CLOSE_ATTEMPTS
        scheduler._close_date = "2026-11-02"
        with mock.patch.object(scheduler, "_rebuild_close"), \
                mock.patch("bootstrap.refresh_daily", side_effect=RuntimeError("boom")) as refresh:
            await scheduler._tick_close(datetime(2026, 11, 3, 17, 30, tzinfo=CN))
        refresh.assert_called_once()
        self.assertEqual(scheduler._close_attempts, 1)
        self.assertIsNotNone(scheduler._close_retry_at)


STALE_GAP = {
    "expected": "2026-11-03", "basis": "calendar",
    "local": {"indices": "2026-11-03", "sectors": "2026-10-30", "institutions": "2026-11-03",
              "etf": "2026-11-03"},
    "lag": {"indices": 0, "sectors": 2, "institutions": 0, "etf": 0}, "stale": ["sectors"],
}
FRESH_GAP = {
    "expected": "2026-11-03", "basis": "calendar",
    "local": {"indices": "2026-11-03", "sectors": "2026-11-03", "institutions": "2026-11-03",
              "etf": "2026-11-03"},
    "lag": {"indices": 0, "sectors": 0, "institutions": 0, "etf": 0}, "stale": [],
}
ETF_LAGGED_GAP = {
    "expected": "2026-11-03", "basis": "calendar",
    "local": {"indices": "2026-11-03", "sectors": "2026-11-03", "institutions": "2026-11-03",
              "etf": "2026-10-31"},
    "lag": {"indices": 0, "sectors": 0, "institutions": 0, "etf": 1}, "stale": ["etf"],
}
ALL_SLOTS = {slot for slot, _ in EVENING_SLOTS}


class CloseCompletionGateTest(unittest.IsolatedAsyncioTestCase):
    """收盘刷新只有真补到最近交易日才算当天完成，否则留给晚间补齐时刻。"""

    async def run_close(self, gap: dict, result: dict) -> MarketScheduler:
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(scheduler, "_rebuild_close"), \
                mock.patch("bootstrap.refresh_daily", return_value=result), \
                mock.patch("bootstrap.data_gap", return_value=gap), \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("io_utils.protected_write"):
            await scheduler._tick_close(datetime(2026, 11, 3, 17, 30, tzinfo=CN))
        return scheduler

    async def test_stale_source_does_not_mark_day_done(self):
        scheduler = await self.run_close(STALE_GAP, {"indices": [], "sectors": 0, "institutions": {}})
        self.assertIsNone(scheduler._close_date)
        self.assertEqual(scheduler._close_attempts, 1)

    async def test_fresh_source_marks_day_done(self):
        scheduler = await self.run_close(FRESH_GAP, {"indices": ["sh000001"], "sectors": 3,
                                                    "institutions": {}})
        self.assertEqual(scheduler._close_date, "2026-11-03")


class EveningBackfillTest(unittest.IsolatedAsyncioTestCase):
    """同花顺行业指数日线通常在收盘当晚即可取得：17:30/20:00/22:00 按需各补试一次。"""

    async def test_no_backfill_before_first_slot(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.data_gap") as gap, \
                mock.patch("bootstrap.ensure_fresh_data") as fresh:
            await scheduler._tick_evening(datetime(2026, 11, 3, 17, 0, tzinfo=CN))
        gap.assert_not_called()
        fresh.assert_not_called()
        self.assertEqual(scheduler._evening_done, set())

    async def test_stale_data_triggers_backfill_and_rebuild(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(scheduler, "_rebuild_close"), \
                mock.patch("bootstrap.data_gap", return_value=STALE_GAP), \
                mock.patch("bootstrap.ensure_fresh_data",
                           return_value={"refreshed": {"sectors": 155}, "after": FRESH_GAP}) as fresh, \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("io_utils.protected_write") as write:
            await scheduler._tick_evening(datetime(2026, 11, 3, 17, 35, tzinfo=CN))
        fresh.assert_called_once()
        self.assertEqual(write.call_count, 2)
        self.assertEqual(scheduler._close_date, "2026-11-03")
        self.assertEqual(scheduler._evening_done, ALL_SLOTS)
        self.assertEqual(scheduler.status()["backfill"]["evening_slots_done"], sorted(ALL_SLOTS))

    async def test_only_latest_pending_slot_runs(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(scheduler, "_rebuild_close"), \
                mock.patch("bootstrap.data_gap", return_value=STALE_GAP), \
                mock.patch("bootstrap.ensure_fresh_data",
                           return_value={"refreshed": {"sectors": 0}, "after": STALE_GAP}) as fresh, \
                mock.patch("io_utils.protected_write") as write:
            await scheduler._tick_evening(datetime(2026, 11, 3, 21, 0, tzinfo=CN))
        fresh.assert_called_once()   # 积压的两个时刻只跑一轮，不形成重试风暴
        write.assert_not_called()
        self.assertEqual(scheduler._evening_done, {"17:30", "20:00"})
        self.assertIsNone(scheduler._close_date)   # 仍未补到，不把当天标记完成

    async def test_up_to_date_data_skips_fetch(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch("bootstrap.ensure_fresh_data") as fresh:
            await scheduler._tick_evening(datetime(2026, 11, 3, 20, 5, tzinfo=CN))
        fresh.assert_not_called()
        self.assertEqual(scheduler._close_date, "2026-11-03")
        self.assertEqual(scheduler._evening_done, ALL_SLOTS)


class HealthStatusTest(unittest.TestCase):
    """健康检查暴露调度版本、在用主源、冷却快照与逐线新鲜度（D6）。"""

    def make_scheduler(self) -> MarketScheduler:
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        return scheduler

    def base_patches(self, scheduler: MarketScheduler, gap: dict):
        return [
            mock.patch("bootstrap.data_gap", return_value=gap),
            mock.patch.object(scheduler.payloads, "indices", return_value=None),
        ]

    def test_status_exposes_version_and_source(self):
        scheduler = self.make_scheduler()
        with mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch.object(scheduler.payloads, "indices", return_value=None):
            status = scheduler.status()
        self.assertIn("scheduler_version", status)
        self.assertEqual(status["scheduler_version"], SCHEDULER_VERSION)
        self.assertIn("primary_source", status)
        self.assertIn("sectors", status["primary_source"])
        self.assertIn("etf", status["primary_source"])
        self.assertIn("indices", status["primary_source"])

    def test_status_reports_rate_limiter_cooldown(self):
        from rate_limiter import LIMITER
        scheduler = self.make_scheduler()
        LIMITER.reset()
        self.addCleanup(LIMITER.reset)
        with mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch.object(scheduler.payloads, "indices", return_value=None), \
                mock.patch.object(LIMITER, "mark_limited", wraps=LIMITER.mark_limited) as limited:
            limited("eastmoney")
            status = scheduler.status()
        self.assertGreater(status["rate_limiter"]["eastmoney"]["cooldown_remaining"], 0)
        # 冷却跳过不得报成「数据已覆盖」：冷却信息单独可见，不污染 covered 判定
        self.assertTrue(status["freshness"]["lines"]["indices"]["covered"])

    def test_status_lists_lines_individually(self):
        scheduler = self.make_scheduler()
        with mock.patch("bootstrap.data_gap", return_value=ETF_LAGGED_GAP), \
                mock.patch.object(scheduler.payloads, "indices", return_value=None):
            status = scheduler.status()
        lines = status["freshness"]["lines"]
        self.assertFalse(lines["etf"]["covered"])
        self.assertTrue(lines["indices"]["covered"])
        self.assertTrue(lines["sectors"]["covered"])
        self.assertTrue(lines["institutions"]["covered"])
        self.assertEqual(status["freshness"]["stale"], ["etf"])

    def test_status_separates_live_snapshot_and_daily_freshness(self):
        scheduler = self.make_scheduler()
        with mock.patch("bootstrap.data_gap", return_value=ETF_LAGGED_GAP), \
                mock.patch.object(scheduler.payloads, "indices", return_value=None):
            status = scheduler.status()
        # 无快照且数据落后：两个标记都必须如实为 False，不以单一字段混淆
        self.assertFalse(status["live_snapshot_available"])
        self.assertFalse(status["daily_up_to_date"])

    def test_status_declares_schedule_times(self):
        scheduler = self.make_scheduler()
        with mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch.object(scheduler.payloads, "indices", return_value=None):
            status = scheduler.status()
        self.assertEqual(status["backfill"]["close_time_declared"], "17:00")
        self.assertIn("20:00", status["backfill"]["evening_slots_declared"])

    async def test_slots_reset_on_new_day(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        scheduler._evening_day = "2026-11-03"
        scheduler._evening_done = set(ALL_SLOTS)
        with mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch("bootstrap.ensure_fresh_data") as fresh:
            await scheduler._tick_evening(datetime(2026, 11, 4, 17, 35, tzinfo=CN))
        fresh.assert_not_called()
        self.assertEqual(scheduler._evening_day, "2026-11-04")
        self.assertEqual(scheduler._close_date, "2026-11-04")


class StartupBuildTest(unittest.TestCase):
    """启动只用本地数据出首屏：原始数据缺失才全量拉取，增量补齐交给收盘/晚间/手动脚本。"""

    def test_startup_order_is_raw_then_close_then_snapshot(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        calls = []

        def note(name):
            def inner(*_args, **_kwargs):
                calls.append(name)
            return inner

        with mock.patch("bootstrap.ensure_raw_data", side_effect=note("raw")), \
                mock.patch("bootstrap.ensure_fresh_data") as fresh, \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("io_utils.protected_write"), \
                mock.patch.object(scheduler, "_ensure_close_data", side_effect=note("ensure")), \
                mock.patch.object(scheduler, "_rebuild_close", side_effect=note("snapshot")):
            with contextlib.redirect_stdout(io.StringIO()):
                scheduler._bootstrap()
        self.assertEqual(calls, ["raw", "ensure", "snapshot"])
        # 启动不做增量补齐：源接口慢/未发布/空响应会长时间阻塞首屏
        fresh.assert_not_called()

    def test_raw_data_failure_still_builds_first_screen(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.ensure_raw_data", side_effect=RuntimeError("无网络")), \
                mock.patch.object(scheduler, "_ensure_close_data") as ensure, \
                mock.patch.object(scheduler, "_rebuild_close") as rebuild:
            with contextlib.redirect_stdout(io.StringIO()):
                scheduler._bootstrap()
        ensure.assert_called_once()    # 拉取失败也照常启动，先用本地数据出首屏
        rebuild.assert_called_once()
        self.assertIn("原始数据准备失败", scheduler.last_error)

    def test_first_screen_failure_is_reported_and_not_swallowed(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.ensure_raw_data"), \
                mock.patch.object(scheduler, "_ensure_close_data",
                                  side_effect=RuntimeError("产物校验失败")), \
                mock.patch.object(scheduler, "_rebuild_close") as rebuild:
            with contextlib.redirect_stdout(io.StringIO()):
                scheduler._bootstrap()
        rebuild.assert_not_called()
        self.assertIn("首屏数据构建失败", scheduler.last_error)

    def test_backfill_blocking_failure_keeps_old_products_and_returns_false(self):
        """增量补齐失败不抛出也不落盘：先用现有数据继续服务，下一个补齐时刻再试。"""
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.ensure_fresh_data", side_effect=RuntimeError("网络断开")), \
                mock.patch("io_utils.protected_write") as write:
            with contextlib.redirect_stdout(io.StringIO()):
                filled = scheduler._backfill_blocking()
        self.assertFalse(filled)
        write.assert_not_called()
        self.assertIn("数据补齐失败", scheduler.last_error)


def _write_kwargs(write: mock.MagicMock) -> dict:
    """按产物名收集 protected_write 的关键字参数，用于断言落盘格式。"""
    return {call.args[0]: dict(call.kwargs) for call in write.call_args_list}


BUILD_FORMAT = {"index-signals.json": {}, "sector-trends.json": {"compact_arrays": True},
                "etf-volume-events.json": {"compact_arrays": True}}


class ProductWriteFormatTest(unittest.IsolatedAsyncioTestCase):
    """服务自己落盘时必须沿用 build_dashboard 的格式：板块产物标量数组压行，
    指数产物保持缩进，否则同一份数据经不同路径写回会出现十几倍体积差。"""

    def test_backfill_blocking_writes_in_build_format(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.ensure_fresh_data",
                        return_value={"refreshed": {"sectors": 155,
                                                    "etf": {"status": "ok",
                                                            "updated": ["510300"],
                                                            "skipped": 5, "failed": []}},
                                      "after": FRESH_GAP}), \
                mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("build_dashboard.build_etf_volume_events", return_value={}), \
                mock.patch("io_utils.protected_write") as write:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(scheduler._backfill_blocking())
        self.assertEqual(_write_kwargs(write), BUILD_FORMAT)

    def test_etf_only_update_rebuilds_events_only(self):
        """仅 ETF 有更新：不重算指数/板块信号，只重建放量事件产物（D4）。"""
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.ensure_fresh_data",
                        return_value={"refreshed": {"indices": [], "sectors": 0,
                                                    "etf": {"status": "ok",
                                                            "updated": ["510300"],
                                                            "skipped": 5, "failed": []}},
                                      "after": FRESH_GAP}), \
                mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch("build_dashboard.build_indices") as build_indices, \
                mock.patch("build_dashboard.build_sectors") as build_sectors, \
                mock.patch("build_dashboard.build_etf_volume_events", return_value={}), \
                mock.patch("io_utils.protected_write") as write:
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertTrue(scheduler._backfill_blocking())
        build_indices.assert_not_called()
        build_sectors.assert_not_called()
        self.assertEqual(list(_write_kwargs(write)), ["etf-volume-events.json"])

    async def test_etf_only_lag_still_triggers_backfill(self):
        """仅 ETF 线落后也触发补齐与事件重建，不因「其余已新鲜」而跳过（D4）。"""
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(scheduler, "_rebuild_close"), \
                mock.patch("bootstrap.refresh_daily",
                           return_value={"indices": [], "sectors": 0, "institutions": {},
                                         "etf": {"status": "ok", "updated": ["510300"],
                                                 "skipped": 5, "failed": []}}), \
                mock.patch("bootstrap.data_gap", return_value=ETF_LAGGED_GAP), \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("build_dashboard.build_etf_volume_events", return_value={}), \
                mock.patch("io_utils.protected_write") as write:
            await scheduler._tick_close(datetime(2026, 11, 3, 17, 30, tzinfo=CN))
        self.assertIn("etf-volume-events.json", _write_kwargs(write))

    def test_ensure_close_data_writes_in_build_format(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(scheduler.payloads, "indices", return_value=None), \
                mock.patch.object(scheduler.payloads, "sectors", return_value=None), \
                mock.patch.object(scheduler.payloads, "load", return_value=None), \
                mock.patch.object(scheduler.payloads, "invalidate"), \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("build_dashboard.build_etf_volume_events", return_value={}), \
                mock.patch("io_utils.protected_write") as write:
            with contextlib.redirect_stdout(io.StringIO()):
                scheduler._ensure_close_data()
        self.assertEqual(_write_kwargs(write), BUILD_FORMAT)

    async def test_tick_close_writes_in_build_format(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(scheduler, "_rebuild_close"), \
                mock.patch("bootstrap.refresh_daily",
                           return_value={"indices": ["sh000001"], "sectors": 3, "institutions": {},
                                         "etf": {"status": "ok", "updated": ["510300"],
                                                 "skipped": 5, "failed": []}}), \
                mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("io_utils.protected_write") as write:
            await scheduler._tick_close(datetime(2026, 11, 3, 17, 30, tzinfo=CN))
        self.assertEqual(_write_kwargs(write), BUILD_FORMAT)


class StaleDetailTest(unittest.TestCase):
    def test_detail_reports_local_expected_and_lag(self):
        text = _stale_detail(STALE_GAP)
        self.assertIn("sectors 停在 2026-10-30", text)
        self.assertIn("应为 2026-11-03", text)
        self.assertIn("差 2 个交易日", text)

    def test_missing_line_is_reported_as_unknown(self):
        gap = {**STALE_GAP,
               "local": {**STALE_GAP["local"], "sectors": None},
               "lag": {**STALE_GAP["lag"], "sectors": None}}
        text = _stale_detail(gap)
        self.assertIn("停在 —", text)
        self.assertIn("未知", text)


class SectorHistoryApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = _sector_payload()
        cls.sectors = [item for rows in cls.payload["groups"].values() for item in rows]
        cls.first = cls.sectors[0]
        cls.other = next(item for item in cls.sectors if item["code"] != cls.first["code"])

    def setUp(self):
        STORE.set(build_close_snapshot(_payload(), self.payload))
        SECTOR_STORE.invalidate()

    def tearDown(self):
        STORE.set(None)
        SECTOR_STORE.invalidate()

    def test_default_range_and_alignment(self):
        data = sector_history(self.first["code"])
        self.assertEqual(data["requested_days"], 120)
        self.assertEqual(len(data["series"]), 120)
        self.assertEqual(data["series"][-1]["date"], self.first["date"])
        self.assertEqual(data["series"][-1]["state"], self.first["state"])
        self.assertFalse(data["insufficient"])

    def test_supported_ranges(self):
        for days in (60, 120, 250):
            with self.subTest(days=days):
                data = sector_history(self.first["code"], days)
                self.assertEqual(len(data["series"]), days)
                self.assertEqual(data["coverage"]["points"], days)
                dates = [row["date"] for row in data["series"]]
                self.assertEqual(dates, sorted(dates))

    def test_invalid_range_is_rejected(self):
        for days in (0, 30, 999):
            with self.subTest(days=days), self.assertRaises(HTTPException) as ctx:
                sector_history(self.first["code"], days)
            self.assertEqual(ctx.exception.status_code, 400)

    def test_unknown_code_returns_404(self):
        with self.assertRaises(HTTPException) as ctx:
            sector_history("000000")
        self.assertEqual(ctx.exception.status_code, 404)

    def test_any_sector_is_located_by_code_alone(self):
        """定位只依 code：响应无层级字段，且拿到的是该板块自身的序列。"""
        data = sector_history(self.other["code"])
        self.assertEqual(data["code"], self.other["code"])
        self.assertEqual(data["name"], self.other["name"])
        self.assertNotIn("level", data)
        self.assertNotIn("parent", data)
        self.assertEqual(data["series"][-1]["date"], self.other["date"])
        first = sector_history(self.first["code"])
        self.assertNotEqual([row["close"] for row in data["series"]],
                            [row["close"] for row in first["series"]])

    def test_all_sectors_are_queryable(self):
        """分组里的每个板块都能单独拉到历史（90 个板块全量）。"""
        self.assertEqual(len(self.sectors), 90)
        for item in self.sectors:
            with self.subTest(code=item["code"]):
                data = sector_history(item["code"], 60)
                self.assertEqual(data["name"], item["name"])
                self.assertEqual(data["series"][-1]["date"], item["date"])

    def test_stale_flag_follows_snapshot(self):
        STORE.set(build_close_snapshot(_payload(), self.payload, stale=True,
                                       stale_since="2026-10-30T15:00:00+08:00",
                                       stale_reason="实时源超时"))
        data = sector_history(self.first["code"], 60)
        self.assertTrue(data["stale"])
        self.assertEqual(data["stale_since"], "2026-10-30T15:00:00+08:00")


class InstitutionEndpointTest(unittest.TestCase):
    """机构多空单端点：参数校验、切片对齐、摘要不含序列、缺失产物降级。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(paths.INSTITUTION_POSITIONS.read_text(encoding="utf-8"))

    def test_default_slice_covers_sixty_days_and_stays_aligned(self):
        data = slice_payload(self.payload, institution="all", variety="all")
        self.assertEqual(len(data["dates"]), 60)
        for gid, block in data["series"].items():
            for key, values in block["all"].items():
                self.assertEqual(len(values), len(data["dates"]), f"{gid}.{key}")
        self.assertEqual(len(data["benchmark"]["close"]), len(data["dates"]))
        self.assertEqual(data["dates"], sorted(data["dates"]))

    def test_market_lines_slice_aligns_with_dates(self):
        data = slice_payload(self.payload, institution="all", variety="all")
        self.assertEqual([line["code"] for line in data["market_lines"]],
                         ["sh000001", "sh000300"])
        for line in data["market_lines"]:
            self.assertEqual(len(line["close"]), len(data["dates"]), line["code"])
        self.assertEqual(data["market_lines"][1]["close"], data["benchmark"]["close"])

    def test_market_lines_do_not_change_statistics(self):
        """上证指数仅为展示叠加：去掉该字段后 benchmark/stats/summary 必须逐值一致。"""
        stripped = {key: value for key, value in self.payload.items() if key != "market_lines"}
        self.assertEqual(slice_payload(stripped, institution="all", variety="all")["market_lines"], [])
        with_lines = slice_payload(self.payload, institution="all", variety="all")
        without = slice_payload(stripped, institution="all", variety="all")
        for key in ("as_of", "benchmark", "dates", "series", "stats", "summary", "quality"):
            self.assertEqual(with_lines[key], without[key], f"{key} 被展示序列影响")

    def test_institution_filter_returns_only_requested_group(self):
        data = slice_payload(self.payload, institution="citic", variety="IF", limit=20)
        self.assertEqual(list(data["series"].keys()), ["citic"])
        self.assertEqual(list(data["stats"].keys()), ["citic"])
        self.assertEqual([meta["id"] for meta in data["institutions"]], ["citic"])
        self.assertEqual(len(data["dates"]), 20)

    def test_date_range_and_empty_window(self):
        dates = self.payload["dates"]
        data = slice_payload(self.payload, institution="guotai", variety="all",
                             start=dates[5], end=dates[9])
        self.assertEqual(data["dates"], dates[5:10])
        empty = slice_payload(self.payload, institution="guotai", variety="all",
                              start="1990-01-01", end="1990-12-31")
        self.assertEqual(empty["dates"], [])
        self.assertEqual(empty["series"]["guotai"]["all"]["net"], [])

    def test_summary_has_no_series(self):
        block = summary(self.payload)
        self.assertNotIn("series", block)
        self.assertNotIn("dates", block)
        self.assertEqual([row["id"] for row in block["institutions"]],
                         ["guotai", "citic", "others"])
        self.assertIn("rate", block["stats"]["guotai"])

    def test_param_validation(self):
        cases = [
            (("bad", "all", None, None, 60), "institution"),
            (("all", "ZZ", None, None, 60), "variety"),
            (("all", "all", "2026/01/05", None, 60), "from"),
            (("all", "all", "2026-02-01", "2026-01-01", 60), "from 不得晚于"),
            (("all", "all", None, None, 0), "limit"),
        ]
        for args, expected in cases:
            with self.subTest(args=args):
                error = validate_params(*args)
                self.assertIsNotNone(error)
                self.assertIn(expected, error)
        self.assertIsNone(validate_params("all", "all", None, None, 60))

    def test_endpoint_preparing_when_payload_missing(self):
        client = TestClient(app_module.app)
        with tempfile.TemporaryDirectory() as temp:
            store = InstitutionStore(Path(temp) / "missing.json")
            with mock.patch.object(app_module, "INSTITUTION_STORE", store):
                response = client.get("/api/institutions/positions")
        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json()["data_state"], "preparing")

    def test_endpoint_rejects_bad_params(self):
        client = TestClient(app_module.app)
        response = client.get("/api/institutions/positions", params={"institution": "bad"})
        self.assertEqual(response.status_code, 400)
        self.assertIn("institution", response.json()["detail"])

    def test_endpoint_returns_slice(self):
        client = TestClient(app_module.app)
        with mock.patch.object(app_module, "INSTITUTION_STORE",
                               InstitutionStore(paths.INSTITUTION_POSITIONS)):
            response = client.get("/api/institutions/positions",
                                  params={"institution": "guotai", "variety": "IH", "limit": 10})
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["dates"]), 10)
        self.assertEqual(list(data["series"].keys()), ["guotai"])
        for key in ("as_of", "generated_at", "stale", "institutions", "varieties",
                    "summary", "series", "disclosure"):
            self.assertIn(key, data, f"响应缺少契约字段 {key}")
        self.assertEqual([row["id"] for row in data["summary"]["institutions"]], ["guotai"])
        self.assertIn("aggregation_note", data["disclosure"])

    def test_store_caches_by_mtime(self):
        from . import institutions as institutions_module
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "positions.json"
            target.write_text(json.dumps({"dates": ["2026-10-30"]}), encoding="utf-8")
            store = InstitutionStore(target)
            real_loads = json.loads
            calls = []

            def counting_loads(*args, **kwargs):
                calls.append(1)
                return real_loads(*args, **kwargs)

            with mock.patch.object(institutions_module.json, "loads", side_effect=counting_loads):
                first = store.load()
                second = store.load()
        self.assertEqual(first, second)
        self.assertEqual(len(calls), 1, "mtime 未变时第二次读取 MUST 命中缓存")

    def test_stale_branch_marks_stale(self):
        payload = json.loads(json.dumps(self.payload))
        payload["as_of"] = "2020-01-01"
        data = slice_payload(payload, institution="all", variety="all")
        self.assertTrue(data["stale"])
        self.assertEqual(data["data_state"], "degraded")
        self.assertEqual(data["stale_since"], "2020-01-01")
        self.assertIn("尚未更新", data["stale_reason"])

    def test_validate_params_rejects_non_string_date(self):
        error = validate_params("all", "all", 123, None, 60)
        self.assertIsNotNone(error)
        self.assertIn("from", error)

    def test_summary_uses_institution_as_of_when_stale(self):
        payload = json.loads(json.dumps(self.payload))
        payload["as_of"] = payload["dates"][-2]
        data = slice_payload(payload, institution="all", variety="all")
        self.assertTrue(data["stale"])
        self.assertEqual(data["summary"]["date"], payload["as_of"])
        self.assertIsNotNone(data["summary"]["institutions"][0]["net"])

    def test_snapshot_carries_summary_without_series(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload()))
        with mock.patch.object(app_module, "INSTITUTION_STORE", InstitutionStore(paths.INSTITUTION_POSITIONS)):
            data = app_module.snapshot()
        self.assertIn("institutions", data)
        self.assertNotIn("series", data["institutions"])
        self.assertEqual(len(data["institutions"]["institutions"]), 3)


ETF_CODES = ["588000", "159915", "510300", "510050", "512100", "510500"]


def _etf_csv_text(dates: list[str], base: float) -> str:
    lines = ["date,open,close,high,low,volume,amount"]
    for step, day in enumerate(dates):
        amount = base + step * 1_000_000.0 + step * step
        lines.append(f"{day},1.0,1.0,1.0,1.0,1000,{amount}")
    return "\n".join(lines) + "\n"


class EtfVolumeSeriesTest(unittest.TestCase):
    """ETF 成交额序列：合成 CSV 夹具全部落在临时目录，严禁读写生产 data/。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        raw = root / "data" / "raw"
        self.etf_dir = raw / "etf-daily"
        self.index_dir = raw / "indices-ths"
        self.etf_dir.mkdir(parents=True)
        self.index_dir.mkdir(parents=True)
        self.manifest = raw / "etf-daily-manifest.json"
        self.events_file = root / "etf-volume-events.json"
        patches = [
            mock.patch.object(paths, "ETF_DAILY_RAW", self.etf_dir),
            mock.patch.object(paths, "ETF_DAILY_MANIFEST", self.manifest),
            mock.patch.object(paths, "INDEX_THS_RAW", self.index_dir),
            mock.patch.object(paths, "ETF_VOLUME_EVENTS", self.events_file),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)

    def _write_fixtures(self, latest: str = "2026-11-03",
                        drop: tuple[str, str] | None = None) -> None:
        """6 只日线近期块共享同一段日期（起点由最晚上市的 588000 决定），指数覆盖全区间；
        drop 模拟齐备区间内某只单日缺数。日期轴用显式列表，不依赖 bdate_range 的星期分布。"""
        starts = {"510050": "2005-02-23", "159915": "2011-12-09", "510300": "2012-05-28",
                  "510500": "2013-03-15", "512100": "2016-11-04", "588000": "2020-11-16"}
        block = [day for day in ("2026-10-20", "2026-10-21", "2026-10-22", "2026-10-23",
                                 "2026-10-26", "2026-10-27", "2026-10-28", "2026-10-29",
                                 "2026-10-30", "2026-11-02", "2026-11-03") if day <= latest]
        for code in ETF_CODES:
            lines = ["date,open,close,high,low,volume,amount",
                     f"{starts[code]},1.0,1.0,1.0,1.0,1000,{code[-1]}000000.0"]
            lines += [f"{day},1.0,1.0,1.0,1.0,1000,{int(code) * 1000 + step * 7 + 0.5}"
                      for step, day in enumerate(block)]
            if drop and drop[0] == code:
                lines = [line for line in lines if not line.startswith(drop[1] + ",")]
            (self.etf_dir / f"{code}.csv").write_text("\n".join(lines) + "\n",
                                                       encoding="utf-8")
        closes = "\n".join(["date,open,close,high,low,volume,amount"] +
                           [f"{day},1,{3000 + step * 2},1,1,1" for step, day in enumerate(block)])
        (self.index_dir / "sh000001.csv").write_text(closes + "\n", encoding="utf-8")
        universe = [{"code": code, "name": f"ETF{code}", "index": f"指数{code}"}
                    for code in ETF_CODES]
        self.manifest.write_text(json.dumps({"universe": universe}, ensure_ascii=False),
                                 encoding="utf-8")

    def _series(self) -> dict:
        return EtfVolumeStore().series()

    def test_ready_fixture_shape(self):
        self._write_fixtures()
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            data = self._series()
        self.assertEqual(data["data_state"], "ready")
        self.assertEqual(len(data["etfs"]), 6)
        self.assertEqual(data["coverage"]["start"], "2026-10-20")

    def test_axis_starts_at_intersection_first_day(self):
        """齐备区间起点 = 6 份日线交集首日（上市最晚的 588000 决定）。"""
        self._write_fixtures()
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            data = self._series()
        self.assertEqual(data["dates"][0], "2026-10-20")
        self.assertEqual(data["dates"], sorted(data["dates"]))
        self.assertEqual(data["as_of"], "2026-11-03")
        self.assertEqual(data["coverage"]["trading_days"], len(data["dates"]))
        for etf in data["etfs"]:
            self.assertEqual(len(etf["amount"]), len(data["dates"]))

    def test_missing_day_dropped_whole_not_zero_filled(self):
        """区间内任一只缺数：该日整日剔除，不补 0、不用 5 只之和冒充。"""
        self._write_fixtures(drop=("510300", "2026-10-22"))
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            data = self._series()
        self.assertNotIn("2026-10-22", data["dates"])
        self.assertIn("2026-10-21", data["dates"])
        self.assertIn("2026-10-23", data["dates"])
        self.assertEqual(len(data["total_amount"]), len(data["dates"]))
        self.assertTrue(all(value is not None for value in data["total_amount"]))

    def test_total_equals_sum_of_six_each_day(self):
        self._write_fixtures()
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            data = self._series()
        for index in range(len(data["dates"])):
            expected = round(sum(etf["amount"][index] for etf in data["etfs"]), 2)
            self.assertAlmostEqual(data["total_amount"][index], expected, places=2)

    def test_index_gap_keeps_null_without_touching_etf_values(self):
        """指数缺日只为 null（不插值不填充），ETF 数值与日期轴不受影响。"""
        self._write_fixtures()
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            before = self._series()
        path = self.index_dir / "sh000001.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text("\n".join(line for line in lines
                                  if not line.startswith("2026-10-21,")) + "\n",
                        encoding="utf-8")
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            after = self._series()
        self.assertEqual(after["dates"], before["dates"])
        self.assertEqual(after["total_amount"], before["total_amount"])
        position = after["dates"].index("2026-10-21")
        self.assertIsNone(after["index_close"][position])
        self.assertIsNotNone(after["index_close"][position + 1])
        self.assertEqual(len(after["index_close"]), len(after["dates"]))

    def test_l3_dates_filtered_to_ready_range(self):
        self._write_fixtures()
        self.events_file.write_text(json.dumps({"days": [
            {"date": "2026-10-21", "level": "L3", "etf_codes": ETF_CODES,
             "total_amount": 7_000_000_000.0},
            {"date": "2026-10-22", "level": "L2", "etf_codes": ["510300"],
             "total_amount": 1_000_000_000.0},
            {"date": "2019-01-02", "level": "L3", "etf_codes": ETF_CODES,
             "total_amount": 6_000_000_000.0},
        ]}, ensure_ascii=False), encoding="utf-8")
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            data = self._series()
        self.assertEqual(data["l3_dates"], ["2026-10-21"])

    def test_events_missing_or_broken_keeps_series_fields(self):
        """事件产物缺失/损坏：l3_dates 为空，序列字段照常返回。"""
        self._write_fixtures()
        for broken in (None, "{not json"):
            with self.subTest(broken=broken):
                if broken is None:
                    self.events_file.unlink(missing_ok=True)
                else:
                    self.events_file.write_text(broken, encoding="utf-8")
                with contextlib.redirect_stdout(io.StringIO()), \
                        mock.patch("trading_calendar.last_completed_trading_day",
                                   return_value=("2026-11-03", "calendar")):
                    data = self._series()
                self.assertEqual(data["data_state"], "ready")
                self.assertEqual(data["l3_dates"], [])
                self.assertGreater(len(data["dates"]), 10)
                self.assertEqual(len(data["total_amount"]), len(data["dates"]))

    def test_missing_or_corrupt_files_degrade_to_preparing(self):
        """文件缺失与内容损坏都降级为 preparing，绝不抛出异常。"""
        cases = {
            "manifest-missing": lambda: self.manifest.unlink(),
            "manifest-broken": lambda: self.manifest.write_text("{bad", encoding="utf-8"),
            "etf-csv-missing": lambda: (self.etf_dir / "510300.csv").unlink(),
            "etf-csv-corrupt": lambda: (self.etf_dir / "510300.csv").write_text(
                "date,open\n2026-10-20,1\n", encoding="utf-8"),
            "index-csv-missing": lambda: (self.index_dir / "sh000001.csv").unlink(),
        }
        for name, break_fixture in cases.items():
            with self.subTest(case=name):
                self._write_fixtures()
                break_fixture()
                with contextlib.redirect_stdout(io.StringIO()):
                    data = self._series()
                self.assertEqual(data["data_state"], "preparing")
                self.assertIn("ETF", data["stale_reason"])
                self.assertEqual(data["dates"], [])
                self.assertEqual(data["total_amount"], [])
                self.assertEqual(data["l3_dates"], [])
                self.assertIsNone(data["as_of"])

    def test_cache_keyed_by_max_mtime(self):
        """输入文件 mtime 未变时命中缓存；任一日线更新后重新组装。"""
        self._write_fixtures()
        store = EtfVolumeStore()
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            first = store.series()
            second = store.series()
            self.assertIs(first, second)
            stamped = (self.etf_dir / "588000.csv").stat().st_mtime + 5
            os.utime(self.etf_dir / "588000.csv", (stamped, stamped))
            third = store.series()
        self.assertIsNot(third, first)
        self.assertEqual(third["dates"], first["dates"])

    def test_stale_when_behind_last_completed_trading_day(self):
        self._write_fixtures(latest="2026-10-30")
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            data = self._series()
        self.assertTrue(data["stale"])
        self.assertIn("2026-10-30", data["stale_reason"])
        self.assertEqual(data["as_of"], "2026-10-30")
        self.assertGreater(len(data["dates"]), 0)

    def test_store_does_not_write_anything(self):
        self._write_fixtures()
        before = {path.name: (path.stat().st_mtime, path.read_bytes())
                  for path in sorted(self.etf_dir.glob("*.csv"))}
        before["sh000001.csv"] = ((self.index_dir / "sh000001.csv").stat().st_mtime,
                                  (self.index_dir / "sh000001.csv").read_bytes())
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            self._series()
        after = {path.name: (path.stat().st_mtime, path.read_bytes())
                 for path in sorted(self.etf_dir.glob("*.csv"))}
        after["sh000001.csv"] = ((self.index_dir / "sh000001.csv").stat().st_mtime,
                                 (self.index_dir / "sh000001.csv").read_bytes())
        self.assertEqual(before, after)


class EtfVolumeEndpointTest(unittest.TestCase):
    """GET /api/etf/volume-series：列式契约、缓存一致与只读保证。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        raw = root / "data" / "raw"
        self.etf_dir = raw / "etf-daily"
        self.index_dir = raw / "indices-ths"
        self.etf_dir.mkdir(parents=True)
        self.index_dir.mkdir(parents=True)
        self.manifest = raw / "etf-daily-manifest.json"
        patches = [
            mock.patch.object(paths, "ETF_DAILY_RAW", self.etf_dir),
            mock.patch.object(paths, "ETF_DAILY_MANIFEST", self.manifest),
            mock.patch.object(paths, "INDEX_THS_RAW", self.index_dir),
            mock.patch.object(paths, "ETF_VOLUME_EVENTS", root / "events.json"),
        ]
        for patcher in patches:
            patcher.start()
            self.addCleanup(patcher.stop)
        dates = pd.bdate_range("2020-11-16", "2026-11-03").strftime("%Y-%m-%d").tolist()
        for code in ETF_CODES:
            (self.etf_dir / f"{code}.csv").write_text(
                _etf_csv_text(dates, float(code) * 1_000_000.0), encoding="utf-8")
        (self.index_dir / "sh000001.csv").write_text(
            "\n".join(["date,open,close,high,low,volume,amount"] +
                      [f"{day},1,{3000 + step},1,1,1,1" for step, day in enumerate(dates)]),
            encoding="utf-8")
        universe = [{"code": code, "name": f"ETF{code}", "index": f"指数{code}"}
                    for code in ETF_CODES]
        self.manifest.write_text(json.dumps({"universe": universe}, ensure_ascii=False),
                                 encoding="utf-8")
        self.store = EtfVolumeStore()
        self.client = TestClient(app_module.app)
        patcher = mock.patch.object(app_module, "ETF_VOLUME_STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_response_fields_and_array_alignment(self):
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            response = self.client.get("/api/etf/volume-series")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for key in ("data_state", "as_of", "generated_at", "stale", "stale_reason",
                    "coverage", "dates", "etfs", "total_amount", "index_close", "l3_dates"):
            self.assertIn(key, data)
        self.assertEqual(data["data_state"], "ready")
        self.assertEqual(len(data["etfs"]), 6)
        for etf in data["etfs"]:
            self.assertEqual(set(etf), {"code", "name", "index", "amount"})
            self.assertEqual(len(etf["amount"]), len(data["dates"]))
        for key in ("total_amount", "index_close"):
            self.assertEqual(len(data[key]), len(data["dates"]), key)

    def test_repeated_requests_are_byte_identical(self):
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            first = self.client.get("/api/etf/volume-series").content
            second = self.client.get("/api/etf/volume-series").content
        self.assertEqual(first, second)

    def test_requests_do_not_touch_data_files(self):
        watched = sorted(self.etf_dir.glob("*.csv")) + [self.manifest,
                                                        self.index_dir / "sh000001.csv"]
        before = {path: (path.stat().st_mtime_ns, path.read_bytes()) for path in watched}
        productions = [paths.INDEX_SIGNALS, paths.SECTOR_TRENDS, paths.ETF_VOLUME_EVENTS,
                       paths.INSTITUTION_POSITIONS]
        prod_before = {path: path.stat().st_mtime_ns for path in productions
                       if path.exists()}
        with mock.patch("trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar")):
            self.client.get("/api/etf/volume-series")
            self.client.get("/api/etf/volume-series")
        for path in watched:
            self.assertEqual((path.stat().st_mtime_ns, path.read_bytes()), before[path])
        for path, mtime in prod_before.items():
            self.assertEqual(path.stat().st_mtime_ns, mtime, f"{path} 被端点改写")

    def test_preparing_response_when_manifest_missing(self):
        self.manifest.unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.client.get("/api/etf/volume-series")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["data_state"], "preparing")
        self.assertEqual(data["dates"], [])
        self.assertIsNotNone(data["stale_reason"])


if __name__ == "__main__":
    unittest.main()
