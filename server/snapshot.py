"""收盘快照组装：把落盘 JSON（data/*.json）合成统一响应。

指数总览/板块趋势页与盘中实时快照已于 2026-11-20 下线；本模块只剩收盘态组装，
供 /api/health 状态字段与调度器内部使用。
"""

from __future__ import annotations

import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

from . import paths  # noqa: F401  # 配置 sys.path
from confirmed_index_signals import ALGORITHM_VERSION  # noqa: E402
from trading_calendar import lag_trading_days, last_completed_trading_day  # noqa: E402

CN = ZoneInfo("Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


class PayloadStore:
    """落盘 JSON 读取缓存，按 mtime 失效。"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def load(self, name: str, path) -> dict | None:
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        payload = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            self._cache[name] = (mtime, payload)
        return payload

    def indices(self) -> dict | None:
        return self.load("index-signals.json", paths.INDEX_SIGNALS)

    def sectors(self) -> dict | None:
        return self.load("sector-trends.json", paths.SECTOR_TRENDS)

    def etf_volume_events(self) -> list[dict]:
        """ETF 放量事件日（按日期升序）；产物缺失、解析失败一律降级为空数组。"""
        try:
            payload = self.load("etf-volume-events.json", paths.ETF_VOLUME_EVENTS)
        except Exception as exc:  # 展示用产物损坏不得把整个快照打成 500
            print(f"[snapshot] ETF 放量事件读取失败（已降级为空）：{exc}", flush=True)
            return []
        if not payload:
            return []
        rows = [
            {
                "date": str(row["date"]),
                "level": row["level"],
                "etf_codes": list(row.get("etf_codes") or []),
                "total_amount": float(row["total_amount"]),
            }
            for row in payload.get("days") or []
        ]
        return sorted(rows, key=lambda row: row["date"])

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def version(self) -> tuple[float, float, float]:
        """落盘文件版本（mtime），用于判断是否需要重建快照。"""

        def mtime(path) -> float:
            return path.stat().st_mtime if path.exists() else 0.0

        return (
            mtime(paths.INDEX_SIGNALS),
            mtime(paths.SECTOR_TRENDS),
            mtime(paths.ETF_VOLUME_EVENTS),
        )


# 板块为单层结构：条目只有自身判定结果，无层级、父级与二级列表字段
_SECTOR_SUMMARY_KEYS = (
    "code",
    "name",
    "date",
    "state",
    "strength",
    "strength_parts",
    "strength_basis",
    "days",
    "data_anomaly",
    "data_lag",
    "spark",
)


def _sector_item(item: dict) -> dict:
    """只保留摘要字段与 30 日缩略序列；长序列由历史端点按需提供。"""
    return {key: item.get(key) for key in _SECTOR_SUMMARY_KEYS}


def _sector_block(payload: dict | None) -> dict:
    """板块快照：只读落盘产物，另补充与最近已完成交易日的差距供页面提示。"""
    expected, basis = last_completed_trading_day()
    if not payload:
        return {
            "as_of": None,
            "is_mock": True,
            "groups": {},
            "evaluation": None,
            "algorithm_version": None,
            "expected_as_of": expected,
            "expected_basis": basis,
            "lag_days": None,
        }
    as_of = payload.get("as_of")
    return {
        "as_of": as_of,
        "expected_as_of": expected,
        "expected_basis": basis,
        "lag_days": lag_trading_days(as_of, expected),
        "is_mock": payload.get("is_mock", False),
        "algorithm_version": payload.get("algorithm_version"),
        "groups": {
            group: [_sector_item(item) for item in rows]
            for group, rows in payload.get("groups", {}).items()
        },
        "evaluation": payload.get("evaluation"),
    }


def _index_tail_block(indices_payload: dict | None) -> dict:
    """短板标的可见（D5）：从收盘产物透传 index_tails 并标出落后标的。

    落后标的 = 尾日早于最晚尾日的指数（6 尾日 T、1 尾日 T-1 → 标出 T-1 那只）。
    """
    tails = (indices_payload or {}).get("index_tails") or {}
    as_of = (indices_payload or {}).get("as_of")
    latest_tail = max(tails.values()) if tails else None
    lagging = [
        code for code, tail in tails.items() if latest_tail and tail < latest_tail
    ]
    return {
        "index_tails": tails,
        "lagging_indexes": lagging,
        "as_of_rule": (indices_payload or {}).get("as_of_rule") or "min(index_tails)",
    }


def build_close_snapshot(
    indices_payload: dict | None,
    sectors_payload: dict | None,
    *,
    stale: bool = False,
    stale_since: str | None = None,
    stale_reason: str | None = None,
    etf_volume_events: list[dict] | None = None,
) -> dict:
    if not indices_payload:
        raise ValueError("缺少 index-signals.json，无法生成收盘快照")
    indices = []
    for item in indices_payload["indices"]:
        indices.append({**item, "provisional": False})
    return {
        "mode": "close",
        "as_of": indices_payload.get("as_of"),
        **_index_tail_block(indices_payload),
        "generated_at": now_iso(),
        "stale": stale,
        "stale_since": stale_since,
        "stale_reason": stale_reason,
        "provisional": False,
        "data_state": "degraded" if stale else "ready",
        "algorithm_version": ALGORITHM_VERSION,
        "indices": indices,
        # 多持有期矩阵、统计覆盖范围与口径说明（收盘产物直透传）。
        "precision": indices_payload.get("precision"),
        "sectors": _sector_block(sectors_payload),
        "recent_signals": indices_payload.get("recent_signals", []),
        "candidates": indices_payload.get("candidates", []),
        "etf_volume_events": etf_volume_events or [],
    }


class SnapshotStore:
    """线程安全的当前快照容器。"""

    def __init__(self) -> None:
        self._snapshot: dict | None = None
        self._lock = threading.Lock()

    def set(self, snapshot: dict) -> None:
        with self._lock:
            self._snapshot = snapshot

    def get(self) -> dict | None:
        with self._lock:
            return self._snapshot

    @property
    def mode(self) -> str | None:
        snapshot = self.get()
        return snapshot.get("mode") if snapshot else None
