"""刷新调度：盘中 30 秒实时快照，收盘后重算日线。

单飞设计：同一时刻只有一个刷新任务；并发请求直接读内存快照。
启动直接出首屏，不做联网增量补齐（避免源接口慢/失败导致长时间不就绪）；
数据更新统一交给收盘后重算、晚间固定补齐时刻与手动脚本。
东财行业板块日线常在收盘数小时后才发布，所以收盘后还有几个固定的补齐时刻。
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta

from . import paths  # noqa: F401  # 配置 sys.path
from .realtime import CN, fetch_spot, trading_session
from .snapshot import (
    HistoryStore, PayloadStore, SnapshotStore, build_close_snapshot, build_live_snapshot,
)

DEFAULT_INTERVAL = 30
# 收盘重算时刻：同花顺指数日线约 15:30 才定稿、东财板块日线更晚，15:00 就拉常拿到
# 未定稿/缺失数据且赶上全市场收盘后拉取高峰；改为收盘 2 小时后（17:00）首试
CLOSE_HOUR_MINUTE = 17 * 60
MAX_CLOSE_ATTEMPTS = 3
CLOSE_RETRY_MINUTES = 10
# 收盘后补齐时刻：东财行业板块日线常在 T 日晚间甚至 T+1 上午才发布，17:00 重算常拿不到
EVENING_SLOTS = (("17:30", 17 * 60 + 30), ("20:00", 20 * 60), ("22:00", 22 * 60))
# 龙虎榜知名游资日更时刻：东财龙虎榜约 17:00-19:00 逐步发布，20:00 首试、22:00 兜底补试
LHB_SLOTS = (("20:00", 20 * 60), ("22:00", 22 * 60))


def _stale_detail(gap: dict) -> str:
    """把落后的数据类拼成一句日志可读的说明。"""
    parts = []
    for key in gap["stale"]:
        lag = gap["lag"].get(key)
        parts.append(f"{key} 停在 {gap['local'].get(key) or '—'}"
                     f"（应为 {gap['expected']}，差 {lag if lag is not None else '未知'} 个交易日）")
    return "、".join(parts)


class MarketScheduler:
    def __init__(self, store: SnapshotStore, interval: int = DEFAULT_INTERVAL) -> None:
        self.store = store
        self.interval = max(5, int(interval))
        self.history = HistoryStore()
        self.payloads = PayloadStore()
        self.progress = _Progress()
        self._lock = asyncio.Lock()
        # 原始日线增量刷新单飞：启动补齐与收盘刷新共用，避免并发写同一批文件
        self._refresh_lock = asyncio.Lock()
        self._close_date: str | None = None
        self._close_day: str | None = None
        self._close_version: tuple[float, float, float] | None = None
        self._close_attempts = 0
        self._close_retry_at: datetime | None = None
        self._evening_day: str | None = None
        self._evening_done: set[str] = set()
        # 龙虎榜知名游资日更状态（展示层数据，失败不影响信号链路）
        self._lhb_day: str | None = None
        self._lhb_done_slots: set[str] = set()
        self._task: asyncio.Task | None = None
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
        """启动顺序：原始数据 → 首屏收盘数据；启动不做增量补齐。

        增量补齐容易因源接口慢/未发布/空响应而长时间阻塞启动，改为统一交给
        收盘后自动重算、晚间补齐时刻与手动脚本（重建信号数据.bat / bootstrap.py）。
        启动时数据落后不阻断：先用本地数据出首屏，页面会显示滞后标记。
        前一步失败不阻断启动。
        """
        from bootstrap import ensure_raw_data  # noqa: PLC0415

        try:
            ensure_raw_data(self.progress)
        except Exception as exc:  # 启动失败要可观测，不吞异常
            self.last_error = f"原始数据准备失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)
        try:
            self._ensure_close_data()
            self._rebuild_close()
        except Exception as exc:
            self.last_error = f"首屏数据构建失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)

    def _backfill_blocking(self) -> bool:
        """把落后的日线补上，真补到了才重算并落盘两份产物（同步调用，耗时可达特别久）。

        调用方负责取锁。失败不抛出：源未发布或拉取报错时先用现有数据启动，
        下一次启动或下一个补齐时刻会再试。
        """
        from bootstrap import data_gap, ensure_fresh_data  # noqa: PLC0415
        from build_dashboard import build_indices, build_sectors  # noqa: PLC0415
        from io_utils import protected_write  # noqa: PLC0415

        try:
            result = ensure_fresh_data(self.progress)
        except Exception as exc:
            self.last_error = f"数据补齐失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}（先用现有数据启动）", flush=True)
            return False
        changed = result.get("refreshed") or {}
        if changed.get("indices") or changed.get("sectors"):
            # 指数宽度依赖东财行业板块日线，任一类原始数据变化都重算两份产物
            self.progress.update("build", "增量补齐后重算")
            protected_write("index-signals.json", build_indices())
            # 板块产物含 31 × 250 的序列，标量数组压成一行以控制体积（与 build_dashboard 的落盘格式一致）
            protected_write("sector-trends.json", build_sectors(), compact_arrays=True)
        self.history.invalidate()
        self.payloads.invalidate()
        after = result.get("after") or data_gap()
        if after["stale"]:
            print(f"[scheduler] 补齐后仍落后：{_stale_detail(after)}"
                  f"（源尚未发布或拉取失败，下次启动或下一个补齐时刻再试）", flush=True)
            return False
        if changed:
            print(f"[scheduler] 补齐完成并已重算落盘：指数 {after['local'].get('indices')}、"
                  f"板块 {after['local'].get('sectors')}（应为 {after['expected']}）", flush=True)
        return True

    # ---------- 收盘数据 ----------

    def _ensure_close_data(self) -> None:
        from build_dashboard import build_indices, build_sectors  # noqa: PLC0415
        from io_utils import protected_write  # noqa: PLC0415

        if self.payloads.indices() is None:
            self.progress.update("build", "构建指数信号数据")
            protected_write("index-signals.json", build_indices())
            self.payloads.invalidate()
        if self.payloads.sectors() is None:
            self.progress.update("build", "构建板块趋势数据")
            # 压行格式与 build_dashboard 一致，避免服务自己补建时产物体积胀十几倍
            protected_write("sector-trends.json", build_sectors(), compact_arrays=True)
            self.payloads.invalidate()

    def _rebuild_close(self, *, stale: bool = False, reason: str | None = None) -> None:
        indices = self.payloads.indices()
        sectors = self.payloads.sectors()
        if indices is None:
            return
        self.store.set(build_close_snapshot(
            indices, sectors,
            stale=stale,
            stale_since=None if not stale else datetime.now(CN).isoformat(timespec="seconds"),
            stale_reason=reason,
            etf_volume_events=self.payloads.etf_volume_events(),
        ))

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
        if trading_session(now):
            async with self._lock:
                await self._tick_live(now)
        else:
            # 收盘分支内部自己取锁：原始日线刷新可能长达十余分钟，不得长期占住盘中刷新的单飞锁
            await self._tick_close(now)
            await self._tick_evening(now)
            await self._tick_lhb(now)

    async def _tick_live(self, now: datetime) -> None:
        from fetch_market import INDEXES  # noqa: PLC0415

        volumes = await asyncio.to_thread(self.history.volumes, list(INDEXES))
        errors: list[str] = []
        quotes = await asyncio.to_thread(fetch_spot, volumes, errors)
        sectors = self.payloads.sectors()
        if quotes:
            events = await asyncio.to_thread(self.payloads.etf_volume_events)
            snapshot = await asyncio.to_thread(
                build_live_snapshot, quotes, self.history, sectors,
                etf_volume_events=events,
                indices_payload=self.payloads.indices(),
            )
            self.store.set(snapshot)
            self.last_error = None
            return

        # 实时源不可用：保留上一次成功数据并标记陈旧
        previous = self.store.get()
        reason = errors[0] if errors else "实时行情不可用"
        if previous:
            self.store.set({
                **previous,
                "stale": True,
                "stale_since": previous.get("generated_at"),
                "stale_reason": reason,
                "data_state": "degraded",
            })
        else:
            self._rebuild_close(stale=True, reason=reason)
        self.last_error = reason

    async def _tick_close(self, now: datetime) -> None:
        async with self._lock:
            version = self.payloads.version()
            if self.store.mode != "close" or version != self._close_version:
                self._rebuild_close()
                self._close_version = version

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
        # 失败后按间隔重试，且每日最多尝试固定次数，避免形成重试风暴
        if self._close_attempts >= MAX_CLOSE_ATTEMPTS:
            return
        if self._close_retry_at is not None and now < self._close_retry_at:
            return

        from bootstrap import data_gap, refresh_daily  # noqa: PLC0415
        from build_dashboard import build_indices, build_sectors  # noqa: PLC0415
        from io_utils import protected_write  # noqa: PLC0415

        async with self._refresh_lock:
            self._close_attempts += 1
            try:
                result = await asyncio.to_thread(refresh_daily, self.progress)
                if result["indices"] or result["sectors"]:
                    self.progress.update("build", "收盘重算")
                    await asyncio.to_thread(protected_write, "index-signals.json", build_indices())
                    await asyncio.to_thread(protected_write, "sector-trends.json", build_sectors(),
                                            compact_arrays=True)
                self.history.invalidate()
                self.payloads.invalidate()
                self._rebuild_close()
                self._close_version = self.payloads.version()
                self.last_error = None
                # 只有真的覆盖到最近交易日才算当天完成：源尚未发布时留给晚间补齐时刻再试
                if not (await asyncio.to_thread(data_gap))["stale"]:
                    self._close_date = today
            except Exception as exc:
                self._close_retry_at = now + timedelta(minutes=CLOSE_RETRY_MINUTES)
                self.last_error = f"收盘重算失败：{type(exc).__name__}: {exc}"
                print(f"[scheduler] {self.last_error}", flush=True)

    async def _tick_evening(self, now: datetime) -> None:
        """收盘后的固定补齐时刻：源发布晚时不至于要等到第二天启动才补上板块数据。"""
        from bootstrap import data_gap  # noqa: PLC0415

        today = now.strftime("%Y-%m-%d")
        if self._evening_day != today:
            self._evening_day = today
            self._evening_done = set()
        minutes = now.hour * 60 + now.minute
        pending = [slot for slot, at in EVENING_SLOTS
                   if minutes >= at and slot not in self._evening_done]
        if not pending:
            return

        gap = await asyncio.to_thread(data_gap)
        if not gap["stale"]:
            # 数据已覆盖最近交易日，当晚不必再拉，顺带把收盘分支标记为完成
            self._evening_done.update(slot for slot, _ in EVENING_SLOTS)
            self._close_date = today
            return

        slot = pending[-1]  # 只补做最靠后的时刻，较早的时刻视为已被它覆盖
        self._evening_done.update(pending)
        print(f"[scheduler] 晚间补齐 {slot}：{_stale_detail(gap)}", flush=True)

        async with self._refresh_lock:
            filled = await asyncio.to_thread(self._backfill_blocking)
            if filled:
                self._rebuild_close()
                self._close_version = self.payloads.version()
        if not filled:
            return  # _backfill_blocking 已输出仍落后原因，等下一个时刻或下次启动再试
        self._close_date = today
        self._evening_done.update(item for item, _ in EVENING_SLOTS)
        print(f"[scheduler] 晚间补齐 {slot} 成功：已覆盖最近交易日", flush=True)

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

    def status(self) -> dict:
        snapshot = self.store.get()
        return {
            "alive": True,
            "mode": snapshot.get("mode") if snapshot else None,
            "last_refresh_at": snapshot.get("generated_at") if snapshot else None,
            "data_state": snapshot.get("data_state") if snapshot else "preparing",
            "stale": snapshot.get("stale") if snapshot else True,
            "algorithm_version": snapshot.get("algorithm_version") if snapshot else None,
            "data_source": "同花顺公开日线（收盘）/ 新浪指数快照（盘中）",
            "progress": self.progress.as_dict(),
            "backfill": {
                "close_attempts": self._close_attempts,
                "close_done_on": self._close_date,
                "evening_slots_done": sorted(self._evening_done),
            },
            "last_error": self.last_error,
            "interval_seconds": self.interval,
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
        return {"stage": self.stage, "detail": self.detail, "started_at": self.started_at}
