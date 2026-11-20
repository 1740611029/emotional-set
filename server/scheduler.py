"""刷新调度：盘中 30 秒实时快照，收盘后重算日线。

单飞设计：同一时刻只有一个刷新任务；并发请求直接读内存快照。
启动直接出首屏，不做联网增量补齐（避免源接口慢/失败导致长时间不就绪）；
数据更新统一交给收盘后重算、晚间固定补齐时刻与手动脚本。
同花顺行业指数日线通常在收盘当晚即可取得，所以收盘后保留几个固定的补齐时刻。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from . import paths  # noqa: F401  # 配置 sys.path
from .realtime import CN, fetch_spot, trading_session
from .snapshot import (
    HistoryStore,
    PayloadStore,
    SnapshotStore,
    build_close_snapshot,
    build_live_snapshot,
)

DEFAULT_INTERVAL = 30
# 调度版本：随调度逻辑/时刻口径变更而升版，供多机经 /api/health 比对是否同步
# sched-v4.0：2026-11-20 移除盘中快照/晚间补齐任务与快照接口，收盘任务不再重算信号产物
SCHEDULER_VERSION = "sched-v4.0-no-index-signal"
# 收盘重算与晚间补齐时刻的校准声明：指数/ETF 已换新浪（全量 1 请求，定稿较早）；
# 板块线 2026-11-14 起为同花顺行业指数日线（90 个细分行业，通常当晚即可取得），17:00 首试并保留
# 17:30/20:00/22:00 三个补齐时刻。校准口径见 data/research/source-eval/source-migration-20261114.md。
CLOSE_HOUR_MINUTE = (
    17 * 60
)  # 收盘重算首试：17:00（板块/指数 15:30 后陆续定稿，避开收盘高峰）
MAX_CLOSE_ATTEMPTS = 3
CLOSE_RETRY_MINUTES = 10
# 晚间补齐时刻常量：补齐任务已于 2026-11-20 下线，保留供健康状态声明与测试参考
EVENING_SLOTS = (("17:30", 17 * 60 + 30), ("20:00", 20 * 60), ("22:00", 22 * 60))
# 龙虎榜知名游资日更时刻：东财龙虎榜约 17:00-19:00 逐步发布，20:00 首试、22:00 兜底补试
LHB_SLOTS = (("20:00", 20 * 60), ("22:00", 22 * 60))
# 可由页面单独启停的服务端调度分支；网页侧轮询开关由前端自行管理，不在此登记。
# 2026-11-20：指数总览/板块趋势页下线后，live_quote（盘中快照）与 evening_backfill
# （板块日线补齐→信号）已无消费页面，连同快照接口一并移除；仅保留收盘数据刷新与龙虎榜日更。
SERVER_TASKS = ("close_rebuild", "lhb_daily")


def _stale_detail(gap: dict) -> str:
    """把落后的数据类拼成一句日志可读的说明。"""
    parts = []
    for key in gap["stale"]:
        lag = gap["lag"].get(key)
        parts.append(
            f"{key} 停在 {gap['local'].get(key) or '—'}"
            f"（应为 {gap['expected']}，差 {lag if lag is not None else '未知'} 个交易日）"
        )
    return "、".join(parts)


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
        # 晚间补齐状态字段（任务已下线，保留供健康状态兼容读取）
        self._evening_day: str | None = None
        self._evening_done: set[str] = set()
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
        """启动顺序：原始数据 → 内部收盘快照（供 /api/health 读取状态）。

        2026-11-20：指数/板块信号产物不再自动构建或重算（无页面消费）。
        首屏只用本地已有数据组装健康状态；如需重建信号产物，走手动重建脚本。
        启动时数据落后不阻断，前一步失败也不阻断。
        """
        from bootstrap import ensure_raw_data  # noqa: PLC0415

        try:
            ensure_raw_data(self.progress)
        except Exception as exc:  # 启动失败要可观测，不吞异常
            self.last_error = f"原始数据准备失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)
        try:
            self._rebuild_close()
        except Exception as exc:
            self.last_error = f"首屏数据构建失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}", flush=True)

    # ---------- 收盘数据 ----------

    def _backfill_blocking(self) -> bool:
        """补齐落后日线并重建三份产物（同步，耗时可达十分钟级）。

        该内部方法已不再由调度主循环自动触发（晚间补齐任务于 2026-11-20 下线），
        保留供手动/测试调用；自动重建请走 pipeline 的重建脚本。
        """
        from bootstrap import data_gap, ensure_fresh_data  # noqa: PLC0415
        from build_dashboard import (
            build_etf_volume_events,
            build_indices,
            build_sectors,
        )  # noqa: PLC0415
        from io_utils import protected_write  # noqa: PLC0415

        try:
            result = ensure_fresh_data(self.progress)
        except Exception as exc:
            self.last_error = f"数据补齐失败：{type(exc).__name__}: {exc}"
            print(f"[scheduler] {self.last_error}（先用现有数据启动）", flush=True)
            return False
        changed = result.get("refreshed") or {}
        etf_changed = bool((changed.get("etf") or {}).get("updated"))
        if changed.get("indices") or changed.get("sectors"):
            self.progress.update("build", "增量补齐后重算")
            protected_write("index-signals.json", build_indices())
            protected_write("sector-trends.json", build_sectors(), compact_arrays=True)
        if etf_changed:
            self.progress.update("build", "增量补齐后重建 ETF 放量事件")
            protected_write(
                "etf-volume-events.json", build_etf_volume_events(), compact_arrays=True
            )
        self.history.invalidate()
        self.payloads.invalidate()
        after = result.get("after") or data_gap()
        if after["stale"]:
            print(
                f"[scheduler] 补齐后仍落后：{_stale_detail(after)}"
                f"（源尚未发布或拉取失败，下次启动或下一个补齐时刻再试）",
                flush=True,
            )
            return False
        if changed:
            print(
                f"[scheduler] 补齐完成并已重算落盘：指数 {after['local'].get('indices')}、"
                f"板块 {after['local'].get('sectors')}（应为 {after['expected']}）",
                flush=True,
            )
        return True

    def _ensure_close_data(self) -> None:
        """产物缺失时的首屏兜底构建（不再由启动自动调用，保留供手动/测试使用）。"""
        from build_dashboard import (
            build_etf_volume_events,
            build_indices,
            build_sectors,
        )  # noqa: PLC0415
        from io_utils import protected_write  # noqa: PLC0415

        if self.payloads.indices() is None:
            self.progress.update("build", "构建指数信号数据")
            protected_write("index-signals.json", build_indices())
            self.payloads.invalidate()
        if self.payloads.sectors() is None:
            self.progress.update("build", "构建板块趋势数据")
            protected_write("sector-trends.json", build_sectors(), compact_arrays=True)
            self.payloads.invalidate()
        if self.payloads.load("etf-volume-events.json", paths.ETF_VOLUME_EVENTS) is None:
            self.progress.update("build", "构建 ETF 放量事件数据")
            protected_write(
                "etf-volume-events.json", build_etf_volume_events(), compact_arrays=True
            )
            self.payloads.invalidate()

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
        # 2026-11-20：盘中实时快照已下线，剩余页面数据均为盘后产物；盘中不再有调度动作。
        if trading_session(now):
            return
        await self._tick_close(now)
        if self.task_enabled["lhb_daily"]:
            await self._tick_lhb(now)

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
        # 失败后按间隔重试，且每日最多尝试固定次数，避免形成重试风暴
        if self._close_attempts >= MAX_CLOSE_ATTEMPTS:
            return
        if self._close_retry_at is not None and now < self._close_retry_at:
            return

        # 2026-11-20：指数总览/板块趋势页下线，指数/板块日线与三份信号产物
        # （index-signals / sector-trends / etf-volume-events）不再自动重算。
        # 收盘任务仅刷新仍在服役页面所需的数据：机构持仓、ETF 日线、连板/成分股展示产物。
        # 信号产物如需重建，仍可用「重建信号数据.bat / pipeline 手动脚本」显式触发。
        from bootstrap import (
            ensure_raw_data,
            update_display_snapshots,
            update_etf,
            update_institutions,
        )  # noqa: PLC0415

        async with self._refresh_lock:
            self._close_attempts += 1
            try:
                await asyncio.to_thread(ensure_raw_data, self.progress)
                await asyncio.to_thread(update_institutions, self.progress)
                await asyncio.to_thread(update_etf, self.progress)
                await asyncio.to_thread(update_display_snapshots, self.progress)
                self.history.invalidate()
                self.payloads.invalidate()
                self.last_error = None
                self._close_date = today
            except Exception as exc:
                self._close_retry_at = now + timedelta(minutes=CLOSE_RETRY_MINUTES)
                self.last_error = f"收盘数据刷新失败：{type(exc).__name__}: {exc}"
                print(f"[scheduler] {self.last_error}", flush=True)

    async def _tick_evening(self, now: datetime) -> None:
        """晚间补齐：已不再由调度主循环触发（任务 2026-11-20 下线），保留供测试/手动调用。"""
        from bootstrap import data_gap  # noqa: PLC0415

        today = now.strftime("%Y-%m-%d")
        if self._evening_day != today:
            self._evening_day = today
            self._evening_done = set()
        minutes = now.hour * 60 + now.minute
        pending = [
            slot for slot, at in EVENING_SLOTS
            if minutes >= at and slot not in self._evening_done
        ]
        if not pending:
            return

        gap = await asyncio.to_thread(data_gap)
        if not gap["stale"]:
            self._evening_done.update(slot for slot, _ in EVENING_SLOTS)
            self._close_date = today
            return

        slot = pending[-1]
        self._evening_done.update(pending)
        print(f"[scheduler] 晚间补齐 {slot}：{_stale_detail(gap)}", flush=True)
        async with self._refresh_lock:
            filled = await asyncio.to_thread(self._backfill_blocking)
            if filled:
                self._rebuild_close()
                self._close_version = self.payloads.version()
        if not filled:
            return
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
        """各数据线新鲜度：as_of / 最近已完成交易日 / 落后交易日数，逐线给出。"""
        from bootstrap import data_gap  # noqa: PLC0415

        gap = data_gap()
        lines = {}
        for key in ("indices", "sectors", "institutions", "etf"):
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
            "stale": list(gap["stale"]),
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
            "data_source": "同花顺公开日线（收盘）/ 新浪指数快照（盘中）",
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
            "backfill": {
                "close_attempts": self._close_attempts,
                "close_done_on": self._close_date,
                "evening_slots_done": sorted(self._evening_done),
                "close_time_declared": "17:00",
                "evening_slots_declared": [slot for slot, _ in EVENING_SLOTS],
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
