"""服务层测试：快照契约、盘中合成、实时源归一化、接口状态。"""
from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from unittest import mock

import pandas as pd

from . import app as app_module
from . import paths
from . import snapshot as snapshot_module
from .app import SECTOR_STORE, STORE, health, index_history, sector_history, snapshot
from .institutions import InstitutionStore, slice_payload, summary, validate_params
from .realtime import CN, _build_quote, _normalize_volume
from .scheduler import EVENING_SLOTS, MAX_CLOSE_ATTEMPTS, MarketScheduler, _stale_detail
from .snapshot import (
    SnapshotStore, _sector_block, build_close_snapshot, build_live_index, _merged_frame,
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


class SectorSnapshotTest(unittest.TestCase):
    def test_snapshot_sectors_drop_long_series(self):
        data = build_close_snapshot(_payload(), _sector_payload())
        block = data["sectors"]
        self.assertNotIn("dates", block)
        self.assertIn("subsector_evaluation", block)
        self.assertIn("algorithm_version", block)
        item = next(iter(block["groups"].values()))[0]
        self.assertNotIn("series", item)
        for key in ("state", "strength", "strength_parts", "strength_basis", "days",
                    "date", "data_lag", "data_anomaly", "spark"):
            self.assertIn(key, item)
        self.assertNotIn("series", item["subsectors"][0])
        self.assertIn("code", item["subsectors"][0])

    def test_missing_payload_returns_placeholder(self):
        block = _sector_block(None)
        self.assertTrue(block["is_mock"])
        self.assertEqual(block["groups"], {})
        self.assertIsNone(block["subsector_evaluation"])


class SectorFreshnessTest(unittest.TestCase):
    """板块滞后字段：前端据此提示"数据截至 X，落后 N 个交易日"。"""

    def test_block_reports_expected_and_lag(self):
        payload = {"as_of": "2026-10-30", "is_mock": False, "groups": {},
                   "algorithm_version": "x", "evaluation": None, "subsector_evaluation": None}
        with mock.patch.object(snapshot_module, "last_completed_trading_day",
                               return_value=("2026-11-02", "calendar")), \
                mock.patch.object(snapshot_module, "lag_trading_days", return_value=1):
            block = _sector_block(payload)
        self.assertEqual(block["expected_as_of"], "2026-11-02")
        self.assertEqual(block["expected_basis"], "calendar")
        self.assertEqual(block["lag_days"], 1)

    def test_up_to_date_block_reports_zero_lag(self):
        payload = {"as_of": "2026-11-02", "is_mock": False, "groups": {},
                   "algorithm_version": "x", "evaluation": None, "subsector_evaluation": None}
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
            await scheduler._tick_close(datetime(2026, 11, 3, 15, 30, tzinfo=CN))
        refresh.assert_called_once()
        self.assertEqual(scheduler._close_attempts, 1)
        self.assertIsNotNone(scheduler._close_retry_at)


STALE_GAP = {
    "expected": "2026-11-03", "basis": "calendar",
    "local": {"indices": "2026-11-03", "sectors": "2026-10-30", "institutions": "2026-11-03"},
    "lag": {"indices": 0, "sectors": 2, "institutions": 0}, "stale": ["sectors"],
}
FRESH_GAP = {
    "expected": "2026-11-03", "basis": "calendar",
    "local": {"indices": "2026-11-03", "sectors": "2026-11-03", "institutions": "2026-11-03"},
    "lag": {"indices": 0, "sectors": 0, "institutions": 0}, "stale": [],
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
            await scheduler._tick_close(datetime(2026, 11, 3, 15, 30, tzinfo=CN))
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
    """申万行业日线常在收盘数小时后才发布：17:30/20:00/22:00 各补试一次。"""

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


class StartupBackfillTest(unittest.TestCase):
    """启动先把落后的日线补上，补齐（或失败）之后才出首屏数据。"""

    def test_backfill_runs_before_first_snapshot_build(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        calls = []

        def note(name):
            def inner(*_args, **_kwargs):
                calls.append(name)
            return inner

        with mock.patch("bootstrap.ensure_raw_data", side_effect=note("raw")), \
                mock.patch("bootstrap.ensure_fresh_data",
                           side_effect=lambda *_a, **_k: (calls.append("backfill"),
                                                          {"refreshed": {"sectors": 155},
                                                           "after": FRESH_GAP})[1]), \
                mock.patch("bootstrap.data_gap", return_value=FRESH_GAP), \
                mock.patch("build_dashboard.build_indices", return_value={}), \
                mock.patch("build_dashboard.build_sectors", return_value={}), \
                mock.patch("io_utils.protected_write"), \
                mock.patch.object(scheduler, "_ensure_close_data", side_effect=note("ensure")), \
                mock.patch.object(scheduler, "_rebuild_close", side_effect=note("snapshot")):
            with contextlib.redirect_stdout(io.StringIO()):
                scheduler._bootstrap()
        self.assertEqual(calls, ["raw", "backfill", "ensure", "snapshot"])

    def test_backfill_failure_does_not_block_startup(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.ensure_raw_data"), \
                mock.patch("bootstrap.ensure_fresh_data", side_effect=RuntimeError("网络断开")), \
                mock.patch.object(scheduler, "_ensure_close_data") as ensure, \
                mock.patch.object(scheduler, "_rebuild_close") as rebuild:
            with contextlib.redirect_stdout(io.StringIO()):
                scheduler._bootstrap()
        ensure.assert_called_once()    # 补齐失败也照常启动，下次启动会再试
        rebuild.assert_called_once()
        self.assertIn("数据补齐失败", scheduler.last_error)

    def test_raw_data_failure_still_continues_to_backfill(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch("bootstrap.ensure_raw_data", side_effect=RuntimeError("无网络")), \
                mock.patch("bootstrap.ensure_fresh_data",
                           return_value={"refreshed": {}, "after": FRESH_GAP}) as fresh, \
                mock.patch.object(scheduler, "_ensure_close_data") as ensure, \
                mock.patch.object(scheduler, "_rebuild_close"):
            with contextlib.redirect_stdout(io.StringIO()):
                scheduler._bootstrap()
        fresh.assert_called_once()
        ensure.assert_called_once()
        self.assertIn("原始数据准备失败", scheduler.last_error)


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
        cls.first = next(iter(cls.payload["groups"].values()))[0]
        cls.sub = cls.first["subsectors"][0]

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

    def test_second_level_returns_own_series(self):
        data = sector_history(self.sub["code"])
        self.assertEqual(data["level"], "second")
        self.assertEqual(data["parent"], self.first["name"])
        self.assertEqual(data["series"][-1]["date"], self.sub["date"])
        parent = sector_history(self.first["code"])
        self.assertNotEqual([row["close"] for row in data["series"]],
                            [row["close"] for row in parent["series"]])

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


if __name__ == "__main__":
    unittest.main()
