"""服务层测试：收盘快照契约、健康状态、接口契约与降级行为。

2026-11-20：指数总览/板块趋势页与盘中实时快照下线，盘中合成、实时源归一化、
晚间补齐等测试随实现一并移除。
"""

from __future__ import annotations

import contextlib
import io
import json
import os
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest import mock

import pandas as pd

from . import app as app_module
from . import paths
from . import snapshot as snapshot_module
from .app import STORE, health
from .etf_share_flow import EtfShareFlowStore
from .etf_volume import EtfVolumeStore
from .institutions import InstitutionStore, slice_payload, summary, validate_params
from .realtime import CN
from .scheduler import (
    CORE_LINES,
    FRESH_CHECK_MINUTES,
    MAX_CLOSE_ATTEMPTS,
    MarketScheduler,
    SCHEDULER_VERSION,
)
from .snapshot import (
    PayloadStore,
    SnapshotStore,
    _sector_block,
    build_close_snapshot,
)
from fastapi.testclient import TestClient


def _payload() -> dict:
    return json.loads(paths.INDEX_SIGNALS.read_text(encoding="utf-8"))


def _sector_payload() -> dict:
    return json.loads(paths.SECTOR_TRENDS.read_text(encoding="utf-8"))


class CloseSnapshotTest(unittest.TestCase):
    def test_snapshot_contract_fields(self):
        data = build_close_snapshot(_payload(), _sector_payload())
        for key in (
            "mode",
            "as_of",
            "generated_at",
            "stale",
            "data_state",
            "indices",
            "sectors",
            "recent_signals",
            "candidates",
            "algorithm_version",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["mode"], "close")
        self.assertFalse(data["provisional"])
        # 指数数量以落盘产物为准（换源/换全集后不再硬编码个数）
        self.assertEqual(len(data["indices"]), len(_payload()["indices"]))
        item = data["indices"][0]
        for key in (
            "code",
            "name",
            "latest",
            "change_pct",
            "current_signal",
            "history",
            "signals",
            "accuracy",
            "candidates",
            "provisional",
        ):
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


class HealthEndpointTest(unittest.TestCase):
    def tearDown(self):
        STORE.set(None)

    def test_health_reports_mode_and_source(self):
        STORE.set(build_close_snapshot(_payload(), _sector_payload()))
        status = health()
        self.assertTrue(status["alive"])
        self.assertEqual(status["mode"], "close")
        self.assertIn("data_source", status)


class EtfVolumeEventsTest(unittest.TestCase):
    """ETF 放量事件：只是展示标记，产物存在/缺失/损坏都不能影响快照其余字段。"""

    EVENTS = [
        {
            "date": "2024-09-30",
            "level": "L3",
            "etf_codes": ["510300", "510050"],
            "total_amount": 12_000_000_000.0,
        },
        {
            "date": "2026-11-03",
            "level": "L2",
            "etf_codes": ["510300"],
            "total_amount": 1_050_000_000.0,
        },
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
            {
                "date": "2026-11-03",
                "level": "L3",
                "etf_codes": ["510300"],
                "total_amount": 1_050_000_000.0,
                "extra": "多余字段必须丢弃",
            },
            {
                "date": "2024-09-30",
                "level": "L1",
                "etf_codes": ["510050"],
                "total_amount": 210_000_000.0,
            },
        ]
        with tempfile.TemporaryDirectory() as temp:
            target = Path(temp) / "etf-volume-events.json"
            target.write_text(
                json.dumps({"days": rows}, ensure_ascii=False), encoding="utf-8"
            )
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
        with_events = build_close_snapshot(
            _payload(), _sector_payload(), etf_volume_events=self.EVENTS
        )
        without = build_close_snapshot(_payload(), _sector_payload())
        self.assertEqual(with_events["etf_volume_events"], self.EVENTS)
        self.assertEqual(without["etf_volume_events"], [])
        for key in without:
            if key in ("etf_volume_events", "generated_at"):
                continue
            self.assertEqual(with_events[key], without[key], f"{key} 被放量事件影响")

    def test_scheduler_rebuild_injects_events(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with mock.patch.object(
            scheduler.payloads, "etf_volume_events", return_value=self.EVENTS
        ):
            scheduler._rebuild_close()
        self.assertEqual(scheduler.store.get()["etf_volume_events"], self.EVENTS)


class AsOfConsistencyTest(unittest.TestCase):
    """短板标的随日期一起返回（D5）。"""

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
        self.assertEqual(len(data["index_tails"]), len(payload["index_tails"]))
        self.assertIn(payload["lagging_indexes"][0], data["lagging_indexes"])


class SectorSnapshotTest(unittest.TestCase):
    def test_snapshot_sectors_drop_long_series(self):
        data = build_close_snapshot(_payload(), _sector_payload())
        block = data["sectors"]
        self.assertNotIn("dates", block)
        self.assertIn("algorithm_version", block)
        item = next(iter(block["groups"].values()))[0]
        self.assertNotIn("series", item)
        for key in (
            "state",
            "strength",
            "strength_parts",
            "strength_basis",
            "days",
            "date",
            "data_lag",
            "data_anomaly",
            "spark",
        ):
            self.assertIn(key, item)

    def test_snapshot_sector_entries_are_flat(self):
        """单层板块：条目不带层级、父级与二级列表，也不带二级评估。"""
        block = build_close_snapshot(_payload(), _sector_payload())["sectors"]
        self.assertNotIn("subsector_evaluation", block)
        self.assertGreater(
            len([i for rows in block["groups"].values() for i in rows]), 0
        )
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
        self.assertEqual(
            [s["segment"] for s in evaluation["segments"]],
            ["research", "exploratory_validation"],
        )
        for segment in evaluation["segments"]:
            self.assertNotIn("horizons", segment)
            self.assertEqual(set(segment["states"]), {"bull", "warn", "bear", "rise"})
            for cell in segment["states"].values():
                self.assertEqual(set(cell), {"10", "20"})
                for horizon in cell.values():
                    if horizon is None:
                        continue
                    for key in (
                        "rate",
                        "samples",
                        "successes",
                        "pending",
                        "wilson95",
                        "small_sample",
                    ):
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
        self.assertGreater(
            len([i for rows in block["groups"].values() for i in rows]), 0
        )


class SectorFreshnessTest(unittest.TestCase):
    """板块滞后字段：前端据此提示"数据截至 X，落后 N 个交易日"。"""

    def test_block_reports_expected_and_lag(self):
        payload = {
            "as_of": "2026-10-30",
            "is_mock": False,
            "groups": {},
            "algorithm_version": "x",
            "evaluation": None,
        }
        with (
            mock.patch.object(
                snapshot_module,
                "last_completed_trading_day",
                return_value=("2026-11-02", "calendar"),
            ),
            mock.patch.object(snapshot_module, "lag_trading_days", return_value=1),
        ):
            block = _sector_block(payload)
        self.assertEqual(block["expected_as_of"], "2026-11-02")
        self.assertEqual(block["expected_basis"], "calendar")
        self.assertEqual(block["lag_days"], 1)

    def test_up_to_date_block_reports_zero_lag(self):
        payload = {
            "as_of": "2026-11-02",
            "is_mock": False,
            "groups": {},
            "algorithm_version": "x",
            "evaluation": None,
        }
        with (
            mock.patch.object(
                snapshot_module,
                "last_completed_trading_day",
                return_value=("2026-11-02", "calendar"),
            ),
            mock.patch.object(snapshot_module, "lag_trading_days", return_value=0),
        ):
            block = _sector_block(payload)
        self.assertEqual(block["lag_days"], 0)

    def test_placeholder_keeps_freshness_fields(self):
        with mock.patch.object(
            snapshot_module,
            "last_completed_trading_day",
            return_value=("2026-11-02", "workday"),
        ):
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


class CloseRefreshTest(unittest.IsolatedAsyncioTestCase):
    """收盘任务链（sched-v4.1）：指数日线增量 + ETF 放量事件重建均在链上。"""

    NOW = datetime(2026, 11, 20, 17, 5, tzinfo=CN)  # 周五 17:05，触发收盘链

    async def run_close(self, events_result) -> tuple[MarketScheduler, dict]:
        """把收盘链跑一遍：pipeline 函数全部打桩，to_thread 改为同步直调。

        events_result 为 Exception 时模拟事件重建失败，否则为事件产物返回值。
        """
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with (
            mock.patch(
                "asyncio.to_thread", side_effect=lambda func, *a, **k: func(*a, **k)
            ),
            mock.patch("bootstrap.data_gap", return_value=FRESH_GAP),
            mock.patch("bootstrap.ensure_raw_data"),
            mock.patch("bootstrap.update_indices") as indices,
            mock.patch("bootstrap.update_institutions"),
            mock.patch("bootstrap.update_etf"),
            mock.patch("bootstrap.update_display_snapshots"),
            mock.patch(
                "build_dashboard.build_etf_volume_events",
                return_value={},
                side_effect=events_result
                if isinstance(events_result, Exception)
                else None,
            ),
            mock.patch("io_utils.protected_write") as write,
        ):
            await scheduler._tick_close(self.NOW)
        return scheduler, {"indices": indices, "write": write}

    async def test_close_chain_updates_indices_and_rebuilds_events(self):
        scheduler, stubs = await self.run_close({})
        self.assertEqual(stubs["indices"].call_count, 1, "指数日线增量必须在收盘链上")
        self.assertEqual(scheduler._close_date, "2026-11-20")
        self.assertIsNone(scheduler.last_error)
        # 事件产物按既定文件名落盘并压缩数组
        self.assertEqual(stubs["write"].call_args.args[0], "etf-volume-events.json")
        self.assertTrue(stubs["write"].call_args.kwargs.get("compact_arrays"))

    async def test_events_failure_warns_but_close_still_done(self):
        scheduler, _stubs = await self.run_close(RuntimeError("ETF 行情文件缺失"))
        self.assertEqual(
            scheduler._close_date, "2026-11-20", "展示层失败不得阻断收盘主链"
        )
        self.assertIn("ETF 放量事件重建失败", scheduler.last_error or "")


FRESH_GAP = {
    "expected": "2026-11-03",
    "basis": "calendar",
    "local": {
        "indices": "2026-11-03",
        "sectors": "2026-11-03",
        "institutions": "2026-11-03",
        "etf": "2026-11-03",
    },
    "lag": {"indices": 0, "sectors": 0, "institutions": 0, "etf": 0},
    "stale": [],
}
ETF_LAGGED_GAP = {
    "expected": "2026-11-03",
    "basis": "calendar",
    "local": {
        "indices": "2026-11-03",
        "sectors": "2026-11-03",
        "institutions": "2026-11-03",
        "etf": "2026-10-31",
    },
    "lag": {"indices": 0, "sectors": 0, "institutions": 0, "etf": 1},
    "stale": ["etf"],
}
LAGGED_GAP = {
    "expected": "2026-11-23",
    "basis": "calendar",
    "local": {
        "indices": "2026-11-20",
        "sectors": "2026-11-20",
        "institutions": "2026-11-20",
        "etf": "2026-11-20",
    },
    "lag": {"indices": 1, "sectors": 1, "institutions": 1, "etf": 1},
    "stale": ["indices", "sectors", "institutions", "etf"],
}
SECTOR_LAGGED_GAP = {
    "expected": "2026-11-23",
    "basis": "calendar",
    "local": {
        "indices": "2026-11-23",
        "sectors": "2026-11-20",
        "institutions": "2026-11-23",
        "etf": "2026-11-23",
    },
    "lag": {"indices": 0, "sectors": 1, "institutions": 0, "etf": 0},
    "stale": ["sectors"],
}


class StartupBackfillTest(unittest.TestCase):
    """启动即补齐（sched-v5.0）：不再等 17:00，板块线不阻塞首屏。"""

    def test_bootstrap_backfills_core_lines(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with (
            mock.patch(
                "bootstrap.ensure_fresh_data", return_value={"refreshed": {}}
            ) as fresh,
            mock.patch("bootstrap.data_gap", return_value=FRESH_GAP),
            mock.patch.object(scheduler, "_rebuild_close"),
        ):
            scheduler._bootstrap()
        self.assertEqual(fresh.call_count, 1, "启动必须判定并补齐落后数据")
        self.assertEqual(fresh.call_args.kwargs.get("lines"), CORE_LINES)
        self.assertIsNotNone(scheduler._fresh_checked_at)

    def test_sector_backfill_runs_in_background(self):
        """板块日线无页面消费且补齐慢：只起后台线程，不阻塞启动。"""
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with (
            mock.patch("bootstrap.ensure_fresh_data", return_value={}),
            mock.patch("bootstrap.data_gap", return_value=SECTOR_LAGGED_GAP),
            mock.patch.object(scheduler, "_rebuild_close"),
            mock.patch("server.scheduler.threading.Thread") as thread,
        ):
            scheduler._bootstrap()
        self.assertEqual(
            thread.call_args.kwargs.get("target"), scheduler._backfill_sectors
        )
        thread.return_value.start.assert_called_once()

    def test_backfill_failure_does_not_block_startup(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with (
            mock.patch(
                "bootstrap.ensure_fresh_data", side_effect=RuntimeError("网络断开")
            ),
            mock.patch("bootstrap.data_gap", return_value=FRESH_GAP),
            mock.patch.object(scheduler, "_rebuild_close") as rebuild,
        ):
            scheduler._bootstrap()
        rebuild.assert_called_once()
        self.assertIn("启动补齐失败", scheduler.last_error or "")


class FreshCheckTest(unittest.IsolatedAsyncioTestCase):
    """缺口检查（sched-v5.0）：盘中同样执行，落后就补，已覆盖时零请求。"""

    NOW = datetime(2026, 11, 24, 10, 0, tzinfo=CN)  # 周二盘中

    async def test_lagging_lines_are_backfilled_in_session(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with (
            mock.patch(
                "asyncio.to_thread", side_effect=lambda func, *a, **k: func(*a, **k)
            ),
            mock.patch("bootstrap.data_gap", return_value=LAGGED_GAP),
            mock.patch(
                "bootstrap.ensure_fresh_data",
                return_value={"refreshed": {"indices": ["sh000001"]}},
            ) as fresh,
            mock.patch.object(scheduler, "_rebuild_etf_volume_events"),
            mock.patch.object(scheduler, "_rebuild_close") as rebuild,
        ):
            await scheduler._tick_fresh(self.NOW)
        fresh.assert_called_once()
        self.assertEqual(fresh.call_args.kwargs.get("lines"), CORE_LINES)
        rebuild.assert_called_once()

    async def test_covered_data_makes_no_request(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with (
            mock.patch(
                "asyncio.to_thread", side_effect=lambda func, *a, **k: func(*a, **k)
            ),
            mock.patch("bootstrap.data_gap", return_value=FRESH_GAP),
            mock.patch("bootstrap.ensure_fresh_data") as fresh,
        ):
            await scheduler._tick_fresh(self.NOW)
        fresh.assert_not_called()

    async def test_check_is_throttled(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        scheduler._fresh_checked_at = self.NOW
        with mock.patch("bootstrap.data_gap") as gap:
            await scheduler._tick_fresh(
                self.NOW + timedelta(minutes=FRESH_CHECK_MINUTES - 1)
            )
        gap.assert_not_called()

    async def test_tick_runs_inside_trading_session(self):
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        with (
            mock.patch("server.scheduler.datetime") as clock,
            mock.patch.object(
                scheduler, "_tick_fresh", new_callable=mock.AsyncMock
            ) as fresh,
            mock.patch.object(
                scheduler, "_tick_close", new_callable=mock.AsyncMock
            ) as close,
            mock.patch.object(
                scheduler, "_tick_lhb", new_callable=mock.AsyncMock
            ) as lhb,
        ):
            clock.now.return_value = self.NOW
            await scheduler.tick()
        fresh.assert_awaited_once()
        close.assert_awaited_once()
        lhb.assert_awaited_once()


class HealthStatusTest(unittest.TestCase):
    """健康检查暴露调度版本、在用主源、冷却快照与逐线新鲜度（D6）。"""

    def make_scheduler(self) -> MarketScheduler:
        scheduler = MarketScheduler(SnapshotStore(), interval=30)
        return scheduler

    def test_status_exposes_version_and_source(self):
        scheduler = self.make_scheduler()
        with (
            mock.patch("bootstrap.data_gap", return_value=FRESH_GAP),
            mock.patch.object(scheduler.payloads, "indices", return_value=None),
        ):
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
        with (
            mock.patch("bootstrap.data_gap", return_value=FRESH_GAP),
            mock.patch.object(scheduler.payloads, "indices", return_value=None),
            mock.patch.object(
                LIMITER, "mark_limited", wraps=LIMITER.mark_limited
            ) as limited,
        ):
            limited("eastmoney")
            status = scheduler.status()
        self.assertGreater(status["rate_limiter"]["eastmoney"]["cooldown_remaining"], 0)
        # 冷却跳过不得报成「数据已覆盖」：冷却信息单独可见，不污染 covered 判定
        self.assertTrue(status["freshness"]["lines"]["indices"]["covered"])

    def test_status_lists_lines_individually(self):
        scheduler = self.make_scheduler()
        with (
            mock.patch("bootstrap.data_gap", return_value=ETF_LAGGED_GAP),
            mock.patch.object(scheduler.payloads, "indices", return_value=None),
        ):
            status = scheduler.status()
        lines = status["freshness"]["lines"]
        self.assertFalse(lines["etf"]["covered"])
        self.assertTrue(lines["indices"]["covered"])
        self.assertTrue(lines["institutions"]["covered"])
        # 板块日线已无页面消费，不再纳入逐线监控（即使 data_gap 报其落后）
        self.assertNotIn("sectors", lines)
        self.assertEqual(status["freshness"]["stale"], ["etf"])

    def test_status_separates_live_snapshot_and_daily_freshness(self):
        scheduler = self.make_scheduler()
        with (
            mock.patch("bootstrap.data_gap", return_value=ETF_LAGGED_GAP),
            mock.patch.object(scheduler.payloads, "indices", return_value=None),
        ):
            status = scheduler.status()
        # 无快照且数据落后：两个标记都必须如实为 False，不以单一字段混淆
        self.assertFalse(status["live_snapshot_available"])
        self.assertFalse(status["daily_up_to_date"])

    def test_status_declares_schedule_times(self):
        scheduler = self.make_scheduler()
        with (
            mock.patch("bootstrap.data_gap", return_value=FRESH_GAP),
            mock.patch.object(scheduler.payloads, "indices", return_value=None),
        ):
            status = scheduler.status()
        self.assertEqual(status["backfill"]["close_time_declared"], "17:00")


class InstitutionEndpointTest(unittest.TestCase):
    """机构多空单端点：参数校验、切片对齐、摘要不含序列、缺失产物降级。"""

    @classmethod
    def setUpClass(cls):
        cls.payload = json.loads(
            paths.INSTITUTION_POSITIONS.read_text(encoding="utf-8")
        )

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
        self.assertEqual(
            [line["code"] for line in data["market_lines"]], ["sh000001", "sh000300"]
        )
        for line in data["market_lines"]:
            self.assertEqual(len(line["close"]), len(data["dates"]), line["code"])
        self.assertEqual(data["market_lines"][1]["close"], data["benchmark"]["close"])

    def test_market_lines_do_not_change_statistics(self):
        """上证指数仅为展示叠加：去掉该字段后 benchmark/stats/summary 必须逐值一致。"""
        stripped = {
            key: value for key, value in self.payload.items() if key != "market_lines"
        }
        self.assertEqual(
            slice_payload(stripped, institution="all", variety="all")["market_lines"],
            [],
        )
        with_lines = slice_payload(self.payload, institution="all", variety="all")
        without = slice_payload(stripped, institution="all", variety="all")
        for key in (
            "as_of",
            "benchmark",
            "dates",
            "series",
            "stats",
            "summary",
            "quality",
        ):
            self.assertEqual(with_lines[key], without[key], f"{key} 被展示序列影响")

    def test_institution_filter_returns_only_requested_group(self):
        data = slice_payload(self.payload, institution="citic", variety="IF", limit=20)
        self.assertEqual(list(data["series"].keys()), ["citic"])
        self.assertEqual(list(data["stats"].keys()), ["citic"])
        self.assertEqual([meta["id"] for meta in data["institutions"]], ["citic"])
        self.assertEqual(len(data["dates"]), 20)

    def test_date_range_and_empty_window(self):
        dates = self.payload["dates"]
        data = slice_payload(
            self.payload,
            institution="guotai",
            variety="all",
            start=dates[5],
            end=dates[9],
        )
        self.assertEqual(data["dates"], dates[5:10])
        empty = slice_payload(
            self.payload,
            institution="guotai",
            variety="all",
            start="1990-01-01",
            end="1990-12-31",
        )
        self.assertEqual(empty["dates"], [])
        self.assertEqual(empty["series"]["guotai"]["all"]["net"], [])

    def test_summary_has_no_series(self):
        block = summary(self.payload)
        self.assertNotIn("series", block)
        self.assertNotIn("dates", block)
        self.assertEqual(
            [row["id"] for row in block["institutions"]], ["guotai", "citic", "others"]
        )
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
        response = client.get(
            "/api/institutions/positions", params={"institution": "bad"}
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("institution", response.json()["detail"])

    def test_endpoint_returns_slice(self):
        client = TestClient(app_module.app)
        with mock.patch.object(
            app_module,
            "INSTITUTION_STORE",
            InstitutionStore(paths.INSTITUTION_POSITIONS),
        ):
            response = client.get(
                "/api/institutions/positions",
                params={"institution": "guotai", "variety": "IH", "limit": 10},
            )
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data["dates"]), 10)
        self.assertEqual(list(data["series"].keys()), ["guotai"])
        for key in (
            "as_of",
            "generated_at",
            "stale",
            "institutions",
            "varieties",
            "summary",
            "series",
            "disclosure",
        ):
            self.assertIn(key, data, f"响应缺少契约字段 {key}")
        self.assertEqual(
            [row["id"] for row in data["summary"]["institutions"]], ["guotai"]
        )
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

            with mock.patch.object(
                institutions_module.json, "loads", side_effect=counting_loads
            ):
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

    def _write_fixtures(
        self, latest: str = "2026-11-03", drop: tuple[str, str] | None = None
    ) -> None:
        """6 只日线近期块共享同一段日期（起点由最晚上市的 588000 决定），指数覆盖全区间；
        drop 模拟齐备区间内某只单日缺数。日期轴用显式列表，不依赖 bdate_range 的星期分布。"""
        starts = {
            "510050": "2005-02-23",
            "159915": "2011-12-09",
            "510300": "2012-05-28",
            "510500": "2013-03-15",
            "512100": "2016-11-04",
            "588000": "2020-11-16",
        }
        block = [
            day
            for day in (
                "2026-10-20",
                "2026-10-21",
                "2026-10-22",
                "2026-10-23",
                "2026-10-26",
                "2026-10-27",
                "2026-10-28",
                "2026-10-29",
                "2026-10-30",
                "2026-11-02",
                "2026-11-03",
            )
            if day <= latest
        ]
        for code in ETF_CODES:
            lines = [
                "date,open,close,high,low,volume,amount",
                f"{starts[code]},1.0,1.0,1.0,1.0,1000,{code[-1]}000000.0",
            ]
            lines += [
                f"{day},1.0,1.0,1.0,1.0,1000,{int(code) * 1000 + step * 7 + 0.5}"
                for step, day in enumerate(block)
            ]
            if drop and drop[0] == code:
                lines = [line for line in lines if not line.startswith(drop[1] + ",")]
            (self.etf_dir / f"{code}.csv").write_text(
                "\n".join(lines) + "\n", encoding="utf-8"
            )
        closes = "\n".join(
            ["date,open,close,high,low,volume,amount"]
            + [f"{day},1,{3000 + step * 2},1,1,1" for step, day in enumerate(block)]
        )
        (self.index_dir / "sh000001.csv").write_text(closes + "\n", encoding="utf-8")
        universe = [
            {"code": code, "name": f"ETF{code}", "index": f"指数{code}"}
            for code in ETF_CODES
        ]
        self.manifest.write_text(
            json.dumps({"universe": universe}, ensure_ascii=False), encoding="utf-8"
        )

    def _series(self) -> dict:
        return EtfVolumeStore().series()

    def test_ready_fixture_shape(self):
        self._write_fixtures()
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            data = self._series()
        self.assertEqual(data["data_state"], "ready")
        self.assertEqual(len(data["etfs"]), 6)
        self.assertEqual(data["coverage"]["start"], "2026-10-20")

    def test_axis_starts_at_intersection_first_day(self):
        """齐备区间起点 = 6 份日线交集首日（上市最晚的 588000 决定）。"""
        self._write_fixtures()
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
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
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            data = self._series()
        self.assertNotIn("2026-10-22", data["dates"])
        self.assertIn("2026-10-21", data["dates"])
        self.assertIn("2026-10-23", data["dates"])
        self.assertEqual(len(data["total_amount"]), len(data["dates"]))
        self.assertTrue(all(value is not None for value in data["total_amount"]))

    def test_total_equals_sum_of_six_each_day(self):
        self._write_fixtures()
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            data = self._series()
        for index in range(len(data["dates"])):
            expected = round(sum(etf["amount"][index] for etf in data["etfs"]), 2)
            self.assertAlmostEqual(data["total_amount"][index], expected, places=2)

    def test_index_gap_keeps_null_without_touching_etf_values(self):
        """指数缺日只为 null（不插值不填充），ETF 数值与日期轴不受影响。"""
        self._write_fixtures()
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            before = self._series()
        path = self.index_dir / "sh000001.csv"
        lines = path.read_text(encoding="utf-8").splitlines()
        path.write_text(
            "\n".join(line for line in lines if not line.startswith("2026-10-21,"))
            + "\n",
            encoding="utf-8",
        )
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            after = self._series()
        self.assertEqual(after["dates"], before["dates"])
        self.assertEqual(after["total_amount"], before["total_amount"])
        position = after["dates"].index("2026-10-21")
        self.assertIsNone(after["index_close"][position])
        self.assertIsNotNone(after["index_close"][position + 1])
        self.assertEqual(len(after["index_close"]), len(after["dates"]))

    def test_l3_dates_filtered_to_ready_range(self):
        self._write_fixtures()
        self.events_file.write_text(
            json.dumps(
                {
                    "days": [
                        {
                            "date": "2026-10-21",
                            "level": "L3",
                            "etf_codes": ETF_CODES,
                            "total_amount": 7_000_000_000.0,
                        },
                        {
                            "date": "2026-10-22",
                            "level": "L2",
                            "etf_codes": ["510300"],
                            "total_amount": 1_000_000_000.0,
                        },
                        {
                            "date": "2019-01-02",
                            "level": "L3",
                            "etf_codes": ETF_CODES,
                            "total_amount": 6_000_000_000.0,
                        },
                    ]
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
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
                with (
                    contextlib.redirect_stdout(io.StringIO()),
                    mock.patch(
                        "trading_calendar.last_completed_trading_day",
                        return_value=("2026-11-03", "calendar"),
                    ),
                ):
                    data = self._series()
                self.assertEqual(data["data_state"], "ready")
                self.assertEqual(data["l3_dates"], [])
                self.assertGreater(len(data["dates"]), 10)
                self.assertEqual(len(data["total_amount"]), len(data["dates"]))

    def test_missing_or_corrupt_files_degrade_to_preparing(self):
        """文件缺失与内容损坏都降级为 preparing，绝不抛出异常。"""
        cases = {
            "manifest-missing": lambda: self.manifest.unlink(),
            "manifest-broken": lambda: self.manifest.write_text(
                "{bad", encoding="utf-8"
            ),
            "etf-csv-missing": lambda: (self.etf_dir / "510300.csv").unlink(),
            "etf-csv-corrupt": lambda: (self.etf_dir / "510300.csv").write_text(
                "date,open\n2026-10-20,1\n", encoding="utf-8"
            ),
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
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
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
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            data = self._series()
        self.assertTrue(data["stale"])
        self.assertIn("2026-10-30", data["stale_reason"])
        self.assertEqual(data["as_of"], "2026-10-30")
        self.assertGreater(len(data["dates"]), 0)

    def test_store_does_not_write_anything(self):
        self._write_fixtures()
        before = {
            path.name: (path.stat().st_mtime, path.read_bytes())
            for path in sorted(self.etf_dir.glob("*.csv"))
        }
        before["sh000001.csv"] = (
            (self.index_dir / "sh000001.csv").stat().st_mtime,
            (self.index_dir / "sh000001.csv").read_bytes(),
        )
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            self._series()
        after = {
            path.name: (path.stat().st_mtime, path.read_bytes())
            for path in sorted(self.etf_dir.glob("*.csv"))
        }
        after["sh000001.csv"] = (
            (self.index_dir / "sh000001.csv").stat().st_mtime,
            (self.index_dir / "sh000001.csv").read_bytes(),
        )
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
                _etf_csv_text(dates, float(code) * 1_000_000.0), encoding="utf-8"
            )
        (self.index_dir / "sh000001.csv").write_text(
            "\n".join(
                ["date,open,close,high,low,volume,amount"]
                + [f"{day},1,{3000 + step},1,1,1,1" for step, day in enumerate(dates)]
            ),
            encoding="utf-8",
        )
        universe = [
            {"code": code, "name": f"ETF{code}", "index": f"指数{code}"}
            for code in ETF_CODES
        ]
        self.manifest.write_text(
            json.dumps({"universe": universe}, ensure_ascii=False), encoding="utf-8"
        )
        self.store = EtfVolumeStore()
        self.client = TestClient(app_module.app)
        patcher = mock.patch.object(app_module, "ETF_VOLUME_STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_response_fields_and_array_alignment(self):
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            response = self.client.get("/api/etf/volume-series")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for key in (
            "data_state",
            "as_of",
            "generated_at",
            "stale",
            "stale_reason",
            "coverage",
            "dates",
            "etfs",
            "total_amount",
            "index_close",
            "l3_dates",
        ):
            self.assertIn(key, data)
        self.assertEqual(data["data_state"], "ready")
        self.assertEqual(len(data["etfs"]), 6)
        for etf in data["etfs"]:
            self.assertEqual(set(etf), {"code", "name", "index", "amount"})
            self.assertEqual(len(etf["amount"]), len(data["dates"]))
        for key in ("total_amount", "index_close"):
            self.assertEqual(len(data[key]), len(data["dates"]), key)

    def test_repeated_requests_are_byte_identical(self):
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
            first = self.client.get("/api/etf/volume-series").content
            second = self.client.get("/api/etf/volume-series").content
        self.assertEqual(first, second)

    def test_requests_do_not_touch_data_files(self):
        watched = sorted(self.etf_dir.glob("*.csv")) + [
            self.manifest,
            self.index_dir / "sh000001.csv",
        ]
        before = {
            path: (path.stat().st_mtime_ns, path.read_bytes()) for path in watched
        }
        productions = [
            paths.INDEX_SIGNALS,
            paths.SECTOR_TRENDS,
            paths.ETF_VOLUME_EVENTS,
            paths.INSTITUTION_POSITIONS,
        ]
        prod_before = {
            path: path.stat().st_mtime_ns for path in productions if path.exists()
        }
        with mock.patch(
            "trading_calendar.last_completed_trading_day",
            return_value=("2026-11-03", "calendar"),
        ):
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


class EtfShareFlowEndpointTest(unittest.TestCase):
    """GET /api/etf/share-flow：列式契约、缓存一致、只读与降级。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.product = Path(self.temp.name) / "etf-share-flow.json"
        self.store = EtfShareFlowStore(path=self.product)
        self.client = TestClient(app_module.app)
        patcher = mock.patch.object(app_module, "ETF_SHARE_FLOW_STORE", self.store)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _write_ready(self):
        payload = {
            "data_state": "ready",
            "as_of": "2026-11-19",
            "generated_at": "2026-11-20T10:00:00+08:00",
            "stale": False,
            "stale_reason": None,
            "coverage": {
                "start": "2026-11-17",
                "end": "2026-11-19",
                "trading_days": 3,
                "etf_count": 2,
                "min_counted": 2,
                "max_counted": 2,
            },
            "dates": ["2026-11-17", "2026-11-18", "2026-11-19"],
            "net_inflow_yi": [10.0, -5.0, 20.0],
            "index_close": [3580.0, None, 3590.0],
            "stats": {"last_day_yi": 20.0, "last_60d_yi": 25.0, "last_60d_days": 3},
            "universe": [{"code": "510300", "name": "沪深300ETF", "index": "沪深300"}],
            "sources": {"share": "sse+szse", "nav": "ths"},
        }
        self.product.write_text(
            json.dumps(payload, ensure_ascii=False), encoding="utf-8"
        )
        return payload

    def test_response_fields_and_array_alignment(self):
        self._write_ready()
        response = self.client.get("/api/etf/share-flow")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        for key in (
            "data_state",
            "as_of",
            "generated_at",
            "dates",
            "net_inflow_yi",
            "index_close",
            "universe",
            "coverage",
            "stats",
        ):
            self.assertIn(key, data)
        for key in ("net_inflow_yi", "index_close"):
            self.assertEqual(len(data[key]), len(data["dates"]), key)
        self.assertIsNone(data["index_close"][1])  # 指数缺失不插值

    def test_repeated_requests_are_byte_identical(self):
        self._write_ready()
        first = self.client.get("/api/etf/share-flow").content
        second = self.client.get("/api/etf/share-flow").content
        self.assertEqual(first, second)

    def test_requests_do_not_touch_product(self):
        self._write_ready()
        before = (self.product.stat().st_mtime_ns, self.product.read_bytes())
        self.client.get("/api/etf/share-flow")
        self.client.get("/api/etf/share-flow")
        self.assertEqual(
            (self.product.stat().st_mtime_ns, self.product.read_bytes()), before
        )

    def test_preparing_when_product_missing(self):
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.client.get("/api/etf/share-flow")
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(data["data_state"], "preparing")
        self.assertEqual(data["dates"], [])
        self.assertIsNotNone(data["stale_reason"])

    def test_preparing_when_product_corrupt(self):
        self.product.write_text("{not json", encoding="utf-8")
        with contextlib.redirect_stdout(io.StringIO()):
            response = self.client.get("/api/etf/share-flow")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["data_state"], "preparing")


if __name__ == "__main__":
    unittest.main()
