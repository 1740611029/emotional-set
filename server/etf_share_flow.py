"""宽基 ETF 申购净流入：只读落盘产物，按文件 mtime 缓存，不参与任何信号计算。

依赖方向单向：本模块只读 `data/etf-share-flow.json`，不 import 任何信号模块，也不写文件。
产物由 `pipeline/fetch_etf_share_flow.py` 生成；产物缺失或损坏一律降级为 preparing，
字段齐全、数值空数组，前端不做 undefined 猜测。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import paths

TRADING_DAY_FIELDS = ("start", "end", "trading_days", "etf_count", "min_counted", "max_counted")


def _failed(reason: str) -> dict:
    return {
        "data_state": "preparing",
        "as_of": None,
        "generated_at": None,
        "coverage": dict.fromkeys(TRADING_DAY_FIELDS, 0),
        "dates": [],
        "net_inflow_yi": [],
        "index_close": [],
        "stats": {"last_day_yi": None, "last_60d_yi": 0.0, "last_60d_days": 0},
        "universe": [],
        "sources": {},
        "stale": True,
        "stale_reason": reason,
    }


class EtfShareFlowStore:
    """按产物 mtime 缓存整份序列；读盘异常一律降级，绝不向上抛 5xx。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.DATA / "etf-share-flow.json"
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
            return _failed(f"宽基 ETF 申购净流入产物不可用：{exc}")
        with self._lock:
            if self._mtime == mtime and self._result is not None:
                return self._result
        try:
            payload = json.loads(self._path.read_text(encoding="utf-8"))
            for key in ("dates", "net_inflow_yi", "index_close", "universe"):
                if not isinstance(payload.get(key), list):
                    raise ValueError(f"产物缺少 {key} 数组")
            if payload.get("data_state") != "ready":
                raise ValueError("产物状态非 ready")
        except Exception as exc:
            print(f"[etf_share_flow] 产物解析失败（降级为准备中）：{exc}", flush=True)
            return _failed(f"宽基 ETF 申购净流入产物暂不可用：{exc}")
        with self._lock:
            self._mtime, self._result = mtime, payload
        return payload
