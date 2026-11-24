"""调度：启动/盘中/收盘都检查数据缺口并补齐，龙虎榜游资日更。

2026-11-20：指数总览/板块趋势页下线，盘中实时快照任务移除。
2026-11-24：启动即补齐（不再等 17:00）；盘中不再跳过调度；收盘任务按实际缺口判完成。
"""

from __future__ import annotations

import asyncio
import json
import threading
from datetime import datetime, timedelta
from functools import partial

from . import paths  # noqa: F401  # 配置 sys.path
from .realtime import CN
from .snapshot import (
    PayloadStore,
    SnapshotStore,
    build_close_snapshot,
)

DEFAULT_INTERVAL = 30
# 调度版本：随调度逻辑/时刻口径变更而升版，供多机经 /api/health 比对是否同步
# sched-v4.0：2026-11-20 移除盘中快照/晚间补齐任务与快照接口，收盘任务不再重算信号产物
# sched-v4.1：2026-11-20 收盘任务补回指数日线增量与 ETF 放量事件重建（机构页/ETF 页依赖）
# sched-v5.0：2026-11-24 启动即补齐落后数据；盘中不再跳过调度；收盘任务按实际缺口判完成
SCHEDULER_VERSION = "sched-v5.0"
# 收盘重算时刻声明：指数/ETF 源为新浪（全量 1 请求，定稿较早）。
# 17:00 首试；未真的覆盖到最近已完成交易日（源未发布）不算完成，
# 按 CLOSE_RETRY_MINUTES 间隔持续重试，直到补齐或进入下一个交易日。
# 校准口径见 data/research/source-eval/source-migration-20261114.md。
CLOSE_HOUR_MINUTE = (
    17 * 60
)  # 收盘重算首试：17:00（板块/指数 15:30 后陆续定稿，避开收盘高峰）
MAX_CLOSE_ATTEMPTS = 3  # 保留供 health 观测/兼容，不再作为停止条件
CLOSE_RETRY_MINUTES = 10
# 数据缺口检查节流：盘中与盘后一致，落后时每 N 分钟重试一次，已覆盖时零请求
FRESH_CHECK_MINUTES = 10
# 页面消费的数据线：启动与盘中补齐只跑这三条，单线失败不影响其余线
CORE_LINES = ("indices", "institutions", "etf")
# 板块日线无页面消费且全量补齐慢（10-30 分钟），只在启动后由后台线程补齐
SECTOR_LINES = ("sectors",)
# 龙虎榜知名游资日更时刻：东财龙虎榜约 17:00-19:00 逐步发布，20:00 首试、22:00 兜底补试
LHB_SLOTS = (("20:00", 20 * 60), ("22:00", 22 * 60))
# 可由页面单独启停的服务端调度分支；网页侧轮询开关由前端自行管理，不在此登记。
# 2026-11-20：指数总览/板块趋势页下线后，live_quote（盘中快照）与 evening_backfill
# （板块日线补齐→信号）已无消费页面，连同快照接口一并移除；仅保留收盘数据刷新与龙虎榜日更。
SERVER_TASKS = ("close_rebuild", "lhb_daily")


def _limiter_snapshot() -> dict:
    """统一限速器冷却快照：冷却中的发行方、剩余秒数与被跳过的数据线。"""
    try:
        from rate_limiter import LIMITER  # noqa: PLC0415  # pipeline 平级导入

        return LIMITER.snapshot()
    except Exception:
        return {}


class MarketScheduler:
    def __init__(self, store: SnapshotStore, interval: int = DEFAULT_INTERVAL) -> None:
        self.store = store
        self.interval = max(5, int(interval))
        self.payloads = PayloadStore()
        self.progress = _Progress()
        self._lock = asyncio.Lock()
        # 原始日线增量刷新单飞：启动补齐与收盘刷新共用，避免并发写同一批文件
        self._refresh_lock = asyncio.Lock()
        # 启动/盘中缺口检查节流：避免每 30 秒 tick 都打源站请求
        self._fresh_checked_at: datetime | None = None
        self._close_date: str | None = None
        self._close_day: str | None = None
        self._close_version: tuple[float, float, float] | None = None
        self._close_attempts = 0
        self._close_retry_at: datetime | None = None
        # 龙虎榜知名游资日更状态（展示层数据，失败不影响信号链路）
        self._lhb_day: str | None = None
        self._lhb_done_slots: set[str] = set()
        self._task: asyncio.Task | None = None
        # 定时任务开关只在进程内生效：重启服务一律恢复全开，避免"忘了开过"导致数据长期不更新
        self.task_enabled = {name: True for name in SERVER_TASKS}
        self.last_error: str | None = None

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        # 启动先判定数据是否已更新：未更新则先补齐，补齐（或失败）之后才出首屏数据
        await asyncio.to_thread(self._bootstrap)
        self._task = asyncio.create_task(self._loop(), name="market-refresh")

    async def stop(self) -> None:
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _bootstrap(self) -> None:
        """启动即判定并补齐：每次启动都检查缺口，落后就补，不再等 17:00。

        页面消费的三条线（指数/机构/ETF）阻塞补齐后才出首屏；板块日线无页面消费
        且全量补齐要 10-30 分钟，交给后台线程，避免拖住启动。
        任一步失败都不阻断启动，用现有数据照常出图，失败原因留痕到 /api/health。
        """
        from bootstrap import data_gap, ensure_fresh_data  # noqa: PLC0415

        try:
            result = ensure_fresh_data(self.progress, lines=CORE_LINES)
            if result.get("refreshed"):
                self.payloads.invalidate()
        except Exception as exc:  # 启动补齐失败要可观测，不吞异常
            self.last_error = f"启动补齐失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)
        self._fresh_checked_at = datetime.now(CN)
        try:
            self._rebuild_close()
        except Exception as exc:
            self.last_error = f"首屏数据构建失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)
        try:
            if "sectors" in data_gap()["stale"]:
                threading.Thread(
                    target=self._backfill_sectors,
                    name="sector-backfill",
                    daemon=True,
                ).start()
        except Exception as exc:
            print(
                f"[scheduler] 板块后台补齐启动失败：{type(exc).__name__}: {exc}",
                flush=True,
            )

    def _backfill_sectors(self) -> None:
        """板块日线后台补齐：无页面消费，失败只告警，不污染启动结果。"""
        from bootstrap import ensure_fresh_data  # noqa: PLC0415

        try:
            result = ensure_fresh_data(self.progress, lines=SECTOR_LINES)
            print(f"[scheduler] 板块后台补齐完成：{result.get('after')}", flush=True)
        except Exception as exc:
            self.last_error = f"板块后台补齐失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)

    # ---------- 收盘数据 ----------

    def _rebuild_etf_volume_events(self) -> None:
        """重建 ETF 放量事件产物（ETF 放量页 L3 标记的数据源，纯展示层）。

        依赖刚由 update_etf / update_indices 刷新的 ETF 日线与沪深300，
        失败（如个别 CSV 缺失）向上抛出，由调用方告警，不静默吞掉。
        """
        from build_dashboard import build_etf_volume_events  # noqa: PLC0415
        from io_utils import protected_write  # noqa: PLC0415

        protected_write(
            "etf-volume-events.json",
            build_etf_volume_events(),
            compact_arrays=True,
        )

    def _rebuild_close(self, *, stale: bool = False, reason: str | None = None) -> None:
        indices = self.payloads.indices()
        sectors = self.payloads.sectors()
        if indices is None:
            return
        self.store.set(
            build_close_snapshot(
                indices,
                sectors,
                stale=stale,
                stale_since=None
                if not stale
                else datetime.now(CN).isoformat(timespec="seconds"),
                stale_reason=reason,
                etf_volume_events=self.payloads.etf_volume_events(),
            )
        )

    # ---------- 定时任务开关 ----------

    def task_states(self) -> dict:
        """返回各调度分支的开关快照；调用方拿到的是副本，改它不影响调度器。"""
        return dict(self.task_enabled)

    def set_task_enabled(self, task: str, enabled: bool) -> dict:
        """启停单个分支或全部（task="all"）。只改开关，不触碰任何信号计算与已落盘数据。

        未知任务名抛 ValueError，由 HTTP 层转成 400，避免静默接受拼写错误。
        """
        if task != "all" and task not in SERVER_TASKS:
            raise ValueError(f"未知定时任务：{task}")
        targets = SERVER_TASKS if task == "all" else (task,)
        for name in targets:
            self.task_enabled[name] = bool(enabled)
        return self.task_states()

    # ---------- 主循环 ----------

    async def _loop(self) -> None:
        while True:
            try:
                await self.tick()
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                self.last_error = f"刷新失败：{type(exc).__name__}: {exc}"
                print(f"[scheduler] {self.last_error}", flush=True)
            await asyncio.sleep(self.interval)

    async def tick(self) -> None:
        now = datetime.now(CN)
        # 2026-11-24：盘中不再跳过——每个节流窗口都检查缺口，落后就补。
        # 已覆盖最近交易日时 ensure_fresh_data 直接返回，不产生任何远端请求。
        await self._tick_fresh(now)
        await self._tick_close(now)
        if self.task_enabled["lhb_daily"]:
            await self._tick_lhb(now)

    async def _tick_fresh(self, now: datetime) -> None:
        """盘中与盘后一律检查数据缺口，落后就增量补齐。

        收盘任务 17:00 触发时，新浪指数日线/中金所持仓往往尚未定稿；旧实现把
        「没抛异常」当成「已补到」，当日不再重试，数据会长期停在上一个交易日。
        这里按实际缺口判定：源未发布就留到下一轮（每 FRESH_CHECK_MINUTES 一次），
        补齐后收盘快照与 ETF 放量事件同步重建。
        """
        if not self.task_enabled["close_rebuild"]:
            return
        if (
            self._fresh_checked_at is not None
            and now - self._fresh_checked_at < timedelta(minutes=FRESH_CHECK_MINUTES)
        ):
            return
        self._fresh_checked_at = now

        from bootstrap import data_gap, ensure_fresh_data  # noqa: PLC0415

        try:
            gap = await asyncio.to_thread(data_gap)
        except Exception as exc:
            self.last_error = f"数据缺口判定失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)
            return
        lagging = [key for key in gap["stale"] if key in CORE_LINES]
        if not lagging:
            return
        detail = "、".join(
            f"{key} 停在 {gap['local'].get(key) or '—'}" for key in lagging
        )
        print(
            f"[scheduler] 数据落后（应为 {gap['expected']}）：{detail}，开始增量补齐",
            flush=True,
        )
        async with self._refresh_lock:
            try:
                result = await asyncio.to_thread(
                    partial(ensure_fresh_data, self.progress, lines=CORE_LINES)
                )
            except Exception as exc:
                self.last_error = f"增量补齐失败：{type(exc).__name__}: {exc}"
                print(f"[scheduler] {self.last_error}", flush=True)
                return
        if not (result.get("refreshed") or {}):
            return
        self.payloads.invalidate()
        # ETF 放量事件是展示层产物，失败只告警，不阻断补齐主链
        try:
            await asyncio.to_thread(self._rebuild_etf_volume_events)
        except Exception as exc:
            self.last_error = f"ETF 放量事件重建失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)
        self._rebuild_close()
        after = result.get("after") or {}
        still = [key for key in (after.get("stale") or []) if key in CORE_LINES]
        if still:
            print(
                f"[scheduler] 补齐后仍落后：{'、'.join(still)}（源尚未发布，下一轮再试）",
                flush=True,
            )

    async def _tick_close(self, now: datetime) -> None:
        async with self._lock:
            version = self.payloads.version()
            if self.store.mode != "close" or version != self._close_version:
                self._rebuild_close()
                self._close_version = version

        # 开关只停 17:00 的日线重算/增量刷新；上面的快照重建是展示层同步，必须保留
        if not self.task_enabled["close_rebuild"]:
            return

        minutes = now.hour * 60 + now.minute
        today = now.strftime("%Y-%m-%d")
        if self._close_day != today:
            # 新交易日重新给足尝试次数，否则长期运行的服务超过 3 次后永不自动刷新
            self._close_day = today
            self._close_attempts = 0
            self._close_retry_at = None
        if minutes < CLOSE_HOUR_MINUTE or self._close_date == today:
            return
        # 周末不会有新的收盘数据，避免无谓的全量拉取
        if now.weekday() >= 5:
            return
        # 失败或源未发布都按间隔重试，直到真的覆盖最近交易日：
        # 不再用"当日尝试次数"兜底——17:00 一次假成功比多几次请求更糟。
        if self._close_retry_at is not None and now < self._close_retry_at:
            return

        # 2026-11-20：指数总览/板块趋势页下线，指数/板块信号产物
        # （index-signals / sector-trends）不再自动重算。
        # 收盘任务刷新仍在服役页面所需的数据：指数日线（机构页基准沪深300 与
        # ETF 页上证指数对照线依赖，单只失败自带补偿重试且不影响其余指数）、
        # 机构持仓、ETF 日线、ETF 放量事件（L3 标记）、连板/涨停池展示产物。
        # 信号产物如需重建，仍可用「重建信号数据.bat / pipeline 手动脚本」显式触发。
        from bootstrap import (
            data_gap,
            ensure_raw_data,
            update_display_snapshots,
            update_etf,
            update_institutions,
            update_indices,
        )  # noqa: PLC0415

        async with self._refresh_lock:
            self._close_attempts += 1
            try:
                await asyncio.to_thread(ensure_raw_data, self.progress)
                await asyncio.to_thread(update_indices, self.progress)
                await asyncio.to_thread(update_institutions, self.progress)
                await asyncio.to_thread(update_etf, self.progress)
                await asyncio.to_thread(update_display_snapshots, self.progress)
                self.payloads.invalidate()
            except Exception as exc:
                self._close_retry_at = now + timedelta(minutes=CLOSE_RETRY_MINUTES)
                self.last_error = f"收盘数据刷新失败：{type(exc).__name__}: {exc}"
                print(f"[scheduler] {self.last_error}", flush=True)
                return
            # 完成判定只认「真的覆盖到最近已完成交易日」：源未发布不算完成，
            # 否则 17:00 一次假成功就会让当天不再重试（2026-11-23 停更的根因）。
            try:
                gap = await asyncio.to_thread(data_gap)
            except Exception as exc:
                gap = {}
                print(
                    f"[scheduler] 收盘完成判定失败：{type(exc).__name__}: {exc}",
                    flush=True,
                )
            still = [key for key in (gap.get("stale") or []) if key != "sectors"]
            if still:
                self._close_retry_at = now + timedelta(minutes=CLOSE_RETRY_MINUTES)
                self.last_error = (
                    f"收盘数据未覆盖 {gap.get('expected')}：{'、'.join(still)} 仍落后"
                )
                print(f"[scheduler] {self.last_error}", flush=True)
                return
            self.last_error = None
            self._close_date = today
            # ETF 放量事件是展示层产物：失败只告警，不阻断收盘主链（次日收盘自动再试）
            try:
                await asyncio.to_thread(self._rebuild_etf_volume_events)
            except Exception as exc:
                self.last_error = f"ETF 放量事件重建失败：{type(exc).__name__}: {exc}"
                print(f"[scheduler] {self.last_error}", flush=True)

    async def _tick_lhb(self, now: datetime) -> None:
        """龙虎榜知名游资日更：只影响 lhb-traders.json 展示产物，失败不影响信号链路。

        每个时刻每日只试一次；20:00 首试，当日数据未发布则 22:00 兜底再试。
        """
        today = now.strftime("%Y-%m-%d")
        if self._lhb_day != today:
            self._lhb_day = today
            self._lhb_done_slots = set()
        minutes = now.hour * 60 + now.minute
        due = [slot for slot, at in LHB_SLOTS if minutes >= at]
        if not due:
            return
        slot = due[-1]
        if slot in self._lhb_done_slots:
            return
        self._lhb_done_slots.add(slot)
        if now.weekday() >= 5:
            return
        from lhb_traders import run_daily_update  # noqa: PLC0415  # pipeline 平级导入

        print(f"[scheduler] 龙虎榜游资日更 {slot} 开始", flush=True)
        try:
            result = await asyncio.to_thread(run_daily_update)
            print(f"[scheduler] 龙虎榜游资日更 {slot} 完成：{result}", flush=True)
        except Exception as exc:  # noqa: BLE001  # 展示层数据，失败留到下一时刻或下次启动
            self.last_error = f"龙虎榜游资日更失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)

    # ---------- 观测 ----------

    @staticmethod
    def _primary_source() -> dict:
        """在用主源标识：读各线 manifest 的 source 字段，缺失时按现值兜底声明。"""

        def manifest_source(name: str, fallback: str) -> str:
            try:
                payload = json.loads(
                    (paths.DATA / "raw" / name).read_text(encoding="utf-8")
                )
                return str(payload.get("source") or fallback)
            except Exception:
                return fallback

        return {
            "indices": manifest_source(
                "indices-ths-manifest.json", "新浪公开指数日线（不复权）"
            ),
            "sectors": manifest_source(
                "sectors-ths-industry-manifest.json", "同花顺行业指数（不复权）"
            ),
            "etf": manifest_source(
                "etf-daily-manifest.json", "东财 ETF 历史日线（不复权）"
            ),
        }

    def _freshness(self) -> dict:
        """各数据线新鲜度：as_of / 最近已完成交易日 / 落后交易日数，逐线给出。

        板块日线（sectors）2026-11-20 起无任何页面消费，不再纳入监控，
        以免 health 永远显示「落后」；其本地原始数据保留，仅供研究脚本使用。
        """
        from bootstrap import data_gap  # noqa: PLC0415

        gap = data_gap()
        lines = {}
        for key in ("indices", "institutions", "etf"):
            lag = gap["lag"].get(key)
            lines[key] = {
                "as_of": gap["local"].get(key),
                "expected": gap["expected"],
                "lag_days": lag,
                "covered": key not in gap["stale"],
            }
        return {
            "expected": gap["expected"],
            "basis": gap["basis"],
            "stale": [key for key in gap["stale"] if key != "sectors"],
            "lines": lines,
        }

    def status(self) -> dict:
        snapshot = self.store.get()
        indices_payload = self.payloads.indices()
        tails = (indices_payload or {}).get("index_tails") or {}
        as_of = (indices_payload or {}).get("as_of")
        freshness = self._freshness()
        return {
            "alive": True,
            "mode": snapshot.get("mode") if snapshot else None,
            "last_refresh_at": snapshot.get("generated_at") if snapshot else None,
            "data_state": snapshot.get("data_state") if snapshot else "preparing",
            "stale": snapshot.get("stale") if snapshot else True,
            "algorithm_version": snapshot.get("algorithm_version")
            if snapshot
            else None,
            "data_source": "盘后产物（新浪指数日线 / 东财机构持仓与 ETF 日线）",
            # 多机版本比对（D6）：调度版本与在用主源标识随 health 暴露
            "scheduler_version": SCHEDULER_VERSION,
            "primary_source": self._primary_source(),
            # 冷却状态可见：冷却中的发行方、剩余秒数与被跳过的数据线
            "rate_limiter": _limiter_snapshot(),
            # 各数据线新鲜度分别可见：不再用单一 stale 掩盖个别线落后
            "freshness": freshness,
            # 短板标的随日期一起返回（D5）：各指数尾日与落后标的代码
            "index_tails": tails,
            "lagging_indexes": [
                code for code, tail in tails.items() if as_of and tail > as_of
            ],
            # 「实时快照可用」与「日线已跟上」必须区分：仅有前者不代表数据新鲜
            "live_snapshot_available": snapshot is not None,
            "daily_up_to_date": not freshness["stale"],
            "progress": self.progress.as_dict(),
            # 缺口检查节流与覆盖范围：盘中也会检查，便于判断"没更新"是源未发布还是调度没跑
            "fresh_check": {
                "interval_minutes": FRESH_CHECK_MINUTES,
                "checked_at": self._fresh_checked_at.isoformat(timespec="seconds")
                if self._fresh_checked_at
                else None,
                "lines": list(CORE_LINES),
            },
            "backfill": {
                "close_attempts": self._close_attempts,
                "close_done_on": self._close_date,
                "close_time_declared": "17:00",
            },
            "last_error": self.last_error,
            "interval_seconds": self.interval,
            "tasks": self.task_states(),
        }


class _Progress:
    def __init__(self) -> None:
        self.stage = "idle"
        self.detail = ""
        self.started_at: str | None = None

    def update(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        self.detail = detail
        if self.started_at is None:
            self.started_at = datetime.now(CN).isoformat(timespec="seconds")
        print(f"[progress] {stage} {detail}", flush=True)

    def as_dict(self) -> dict:
        return {
            "stage": self.stage,
            "detail": self.detail,
            "started_at": self.started_at,
        }
