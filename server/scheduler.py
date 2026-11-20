"""调度：收盘后刷新数据，龙虎榜游资日更。

2026-11-20：指数总览/板块趋势页下线，盘中实时快照与晚间补齐任务一并移除。
启动直接用本地已有数据组装健康状态；数据更新由收盘任务与手动脚本负责。
"""

from __future__ import annotations

import asyncio
import json
from datetime import datetime, timedelta

from . import paths  # noqa: F401  # 配置 sys.path
from .realtime import CN, trading_session
from .snapshot import (
    PayloadStore,
    SnapshotStore,
    build_close_snapshot,
)

DEFAULT_INTERVAL = 30
# 调度版本：随调度逻辑/时刻口径变更而升版，供多机经 /api/health 比对是否同步
# sched-v4.0：2026-11-20 移除盘中快照/晚间补齐任务与快照接口，收盘任务不再重算信号产物
# sched-v4.1：2026-11-20 收盘任务补回指数日线增量与 ETF 放量事件重建（机构页/ETF 页依赖）
SCHEDULER_VERSION = "sched-v4.1"
# 收盘重算时刻声明：指数/ETF 源为新浪（全量 1 请求，定稿较早）。
# 17:00 首试，失败按 CLOSE_RETRY_MINUTES 间隔当日最多重试 MAX_CLOSE_ATTEMPTS 次。
# 校准口径见 data/research/source-eval/source-migration-20261114.md。
CLOSE_HOUR_MINUTE = (
    17 * 60
)  # 收盘重算首试：17:00（板块/指数 15:30 后陆续定稿，避开收盘高峰）
MAX_CLOSE_ATTEMPTS = 3
CLOSE_RETRY_MINUTES = 10
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

        # 2026-11-20：指数总览/板块趋势页下线，指数/板块信号产物
        # （index-signals / sector-trends）不再自动重算。
        # 收盘任务刷新仍在服役页面所需的数据：指数日线（机构页基准沪深300 与
        # ETF 页上证指数对照线依赖，单只失败自带补偿重试且不影响其余指数）、
        # 机构持仓、ETF 日线、ETF 放量事件（L3 标记）、连板/涨停池展示产物。
        # 信号产物如需重建，仍可用「重建信号数据.bat / pipeline 手动脚本」显式触发。
        from bootstrap import (
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
                self.last_error = None
                self._close_date = today
            except Exception as exc:
                self._close_retry_at = now + timedelta(minutes=CLOSE_RETRY_MINUTES)
                self.last_error = f"收盘数据刷新失败：{type(exc).__name__}: {exc}"
                print(f"[scheduler] {self.last_error}", flush=True)
                return
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
