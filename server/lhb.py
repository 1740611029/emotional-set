"""龙虎榜知名游资端点：只读产物、按 mtime 缓存、按参数切片。

与 institutions.py 同一模式：产物为日频数据，端点只读不抓取，
请求路径内 MUST NOT 触发任何外部数据请求；更不参与任何信号计算。
"""
from __future__ import annotations

import json
from pathlib import Path

from . import paths

DEFAULT_LIMIT = 200
MAX_LIMIT = 2000


class LhbStore:
    """按文件 mtime 缓存产物，避免每次请求重复解析。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.LHB_TRADERS
        self._mtime: float | None = None
        self._payload: dict | None = None

    def load(self) -> dict | None:
        path = self._path
        if not path.exists():
            self._mtime, self._payload = None, None
            return None
        mtime = path.stat().st_mtime
        if self._payload is None or self._mtime != mtime:
            self._payload = json.loads(path.read_text(encoding="utf-8"))
            self._mtime = mtime
        return self._payload


def preparing() -> dict:
    return {
        "data_state": "preparing", "stale": True,
        "as_of": None, "generated_at": None,
        "stale_reason": "龙虎榜游资数据准备中（首次建库或当日尚未更新）",
        "trading_dates": [], "traders": [], "daily": {},
        "seat_map_version": None, "algorithm_version": None,
        "scope_note": None,
    }


def overview(payload: dict | None) -> dict:
    """总览：每个游资的统计 + 完整记录（单日榜+多日榜标记，前端按需过滤）。"""
    if not payload or not payload.get("traders"):
        return preparing()
    traders = []
    for trader in payload["traders"]:
        traders.append({
            "id": trader["id"], "name": trader["name"], "full_name": trader["full_name"],
            "tier": trader["tier"], "style": trader["style"],
            "confidence": trader["confidence"], "note": trader["note"],
            "seats": trader["seats"], "stats": trader["stats"],
            "records": trader["records"],
        })
    return {
        "data_state": "ok", "stale": False,
        "as_of": payload.get("as_of"), "generated_at": payload.get("generated_at"),
        "algorithm_version": payload.get("algorithm_version"),
        "seat_map_version": payload.get("seat_map_version"),
        "scope_note": payload.get("scope_note"),
        "trading_dates": payload.get("trading_dates", []),
        "traders": traders,
        "quality": payload.get("quality"),
    }


def trader_detail(payload: dict | None, trader_id: str, *, limit: int = DEFAULT_LIMIT) -> dict:
    """单个游资完整记录（单日榜 + 多日榜标记），按日期倒序已由产物保证。"""
    if not payload or not payload.get("traders"):
        return preparing()
    for trader in payload["traders"]:
        if trader["id"] == trader_id:
            records = trader["records"][:limit]
            return {
                "data_state": "ok", "stale": False,
                "as_of": payload.get("as_of"), "generated_at": payload.get("generated_at"),
                "seat_map_version": payload.get("seat_map_version"),
                "scope_note": payload.get("scope_note"),
                "trader": {key: trader[key] for key in
                           ("id", "name", "full_name", "tier", "style",
                            "confidence", "note", "seats", "stats")},
                "records": records,
                "total_records": len(trader["records"]),
            }
    return {"data_state": "not_found", "stale": False, "as_of": payload.get("as_of"),
            "trader": None, "records": []}


def daily(payload: dict | None, date: str) -> dict:
    """某交易日全部已收录游资的操作动向（单日榜口径）。"""
    if not payload or not payload.get("traders"):
        return preparing()
    rows = payload.get("daily", {}).get(date)
    if rows is None:
        return {"data_state": "not_found", "stale": False, "as_of": payload.get("as_of"),
                "date": date, "rows": [],
                "available_dates": payload.get("trading_dates", [])}
    return {
        "data_state": "ok", "stale": False,
        "as_of": payload.get("as_of"), "generated_at": payload.get("generated_at"),
        "seat_map_version": payload.get("seat_map_version"),
        "scope_note": payload.get("scope_note"),
        "date": date, "rows": rows,
        "trading_dates": payload.get("trading_dates", []),
    }
