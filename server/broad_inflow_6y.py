"""宽基 ETF 申购净流入近 6 年（逐日动态池）：只读落盘产物，按 mtime 缓存。

与 `server/etf_share_flow.py`（既有柱状图，2025 年起、单日固化池）完全独立：
本模块只读 `data/broad-inflow-6y.json`，不 import 任何信号模块，也不写文件。
产物缺失或损坏一律降级为 preparing，字段齐全、数值空数组，前端不做 undefined 猜测。
"""

from __future__ import annotations

import json
import threading
from pathlib import Path

from . import paths

ARRAY_FIELDS = ("dates", "daily_inflow_yi", "cum_inflow_yi", "index_close", "etf_count")


def _failed(reason: str) -> dict:
    return {
        "data_state": "preparing",
        "as_of": None,
        "generated_at": None,
        "coverage": {
            "start": None,
            "end": None,
            "trading_days": 0,
            "codes_seen": 0,
            "min_pool": 0,
            "max_pool": 0,
            "min_counted": 0,
            "max_counted": 0,
        },
        "dates": [],
        "daily_inflow_yi": [],
        "cum_inflow_yi": [],
        "index_close": [],
        "etf_count": [],
        "stats": {
            "cum_yi": 0.0,
            "last_day_yi": None,
            "last_60d_yi": 0.0,
            "last_60d_days": 0,
        },
        "anomalies": {"split_skipped": 0, "nav_missing": 0},
        "sources": {},
        "stale": True,
        "stale_reason": reason,
    }


class BroadInflow6yStore:
    """按产物 mtime 缓存整份序列；读盘异常一律降级，绝不向上抛 5xx。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.DATA / "broad-inflow-6y.json"
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._result: dict | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._mtime, self._result = None, None

    def series(self) -> dict:
        try:
            mtime = self._path.stat().st_mtime
        except OSError as exc:
            return _failed(f"6 年宽基申购净流入产物不可用：{exc}")
        with self._lock:
            if self._mtime == mtime and self._result is not None:
                return self._result
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            for key in ARRAY_FIELDS:
                if not isinstance(payload.get(key), list):
                    raise ValueError(f"产物缺少 {key} 数组")
            lengths = {len(payload[key]) for key in ARRAY_FIELDS}
            if len(lengths) != 1:
                raise ValueError(f"产物列式数组长度不一致：{sorted(lengths)}")
            if payload.get("data_state") != "ready":
                raise ValueError("产物状态非 ready")
        except Exception as exc:
            print(f"[broad_inflow_6y] 产物解析失败（降级为准备中）：{exc}", flush=True)
            return _failed(f"6 年宽基申购净流入产物暂不可用：{exc}")
        with self._lock:
            self._mtime, self._result = mtime, payload
        return payload
