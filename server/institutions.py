"""机构多空单端点：只读产物、按 mtime 缓存、按参数切片。

产物为日频数据，端点只读不抓取；请求路径内 MUST NOT 触发任何外部数据请求。
"""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

from . import paths

INSTITUTION_IDS = ("guotai", "citic", "others")
VARIETIES = ("all", "IF", "IC", "IH", "IM")
DEFAULT_LIMIT = 60
MAX_LIMIT = 800


class InstitutionStore:
    """按文件 mtime 缓存产物，避免每次请求重复解析。"""

    def __init__(self, path: Path | None = None) -> None:
        self._path = path or paths.INSTITUTION_POSITIONS
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


def summary(payload: dict | None) -> dict | None:
    """快照用摘要：只含最新交易日的三家数值与同向率，不含任何序列。"""
    if not payload or not payload.get("dates"):
        return None
    last = len(payload["dates"]) - 1
    rows = []
    for meta in payload["institutions"]:
        block = payload["series"][meta["id"]]["all"]
        rows.append({
            "id": meta["id"], "name": meta["name"],
            "long": block["long"][last], "short": block["short"][last],
            "net": block["net"][last], "chg": block["chg"][last],
            "relation": block["relation"][last], "next_ret": block["next_ret"][last],
        })
    stats = {}
    for gid in INSTITUTION_IDS:
        stat = payload["stats"][gid]["all"]
        stats[gid] = {key: stat[key] for key in
                      ("rate", "decided", "same", "hedge", "pending", "small_sample", "start", "end")}
    return {"as_of": payload["as_of"], "date": payload["dates"][last],
            "institutions": rows, "stats": stats}


def _slice(dates: list[str], start: str | None, end: str | None, limit: int) -> tuple[int, int]:
    lo, hi = 0, len(dates)
    if start:
        lo = next((index for index, day in enumerate(dates) if day >= start), len(dates))
    if end:
        hi = next((index for index, day in enumerate(dates) if day > end), len(dates))
    lo = min(lo, hi)
    if limit and hi - lo > limit:
        lo = hi - limit
    return lo, hi


def slice_payload(payload: dict, *, institution: str, variety: str,
                  start: str | None = None, end: str | None = None,
                  limit: int = DEFAULT_LIMIT) -> dict:
    dates = payload["dates"]
    lo, hi = _slice(dates, start, end, limit)
    selected = INSTITUTION_IDS if institution == "all" else (institution,)
    series = {
        gid: {variety: {key: payload["series"][gid][variety][key][lo:hi]
                        for key in ("long", "short", "net", "chg", "next_ret", "relation")}}
        for gid in selected
    }
    stats = {gid: {variety: payload["stats"][gid][variety]} for gid in selected}
    # 展示用大盘对照序列：旧产物可缺该字段，缺失时返回空列表而非报错
    market_lines = [{"code": line["code"], "name": line["name"], "close": line["close"][lo:hi]}
                    for line in payload.get("market_lines") or []]
    stale = bool(payload.get("as_of")) and payload.get("as_of") != payload["window"]["end"]
    sliced_dates = dates[lo:hi]
    summary_block = None
    if sliced_dates:
        # 当日快照对齐机构数据最新交易日；日历已到新交易日但机构产物滞后时，
        # 不得把无数据的最新日历日当作当日快照。
        as_of = payload.get("as_of")
        summary_index = sliced_dates.index(as_of) if as_of in sliced_dates else len(sliced_dates) - 1
        rows = []
        for meta in payload["institutions"]:
            gid = meta["id"]
            if gid not in selected:
                continue
            view = series[gid][variety]
            rows.append({
                "id": gid, "name": meta["name"],
                "long": view["long"][summary_index], "short": view["short"][summary_index],
                "net": view["net"][summary_index], "chg": view["chg"][summary_index],
                "relation": view["relation"][summary_index],
                "next_ret": view["next_ret"][summary_index],
            })
        summary_block = {"date": sliced_dates[summary_index], "institutions": rows}
    return {
        "as_of": payload["as_of"],
        "generated_at": payload["generated_at"],
        "algorithm_version": payload["algorithm_version"],
        "data_state": "degraded" if stale else "ready",
        "stale": stale,
        "stale_since": payload["as_of"] if stale else None,
        "stale_reason": "机构持仓尚未更新到最新交易日" if stale else None,
        "summary": summary_block,
        "disclosure": {
            "aggregation_note": payload["aggregation_note"],
            "scope_note": payload["scope_note"],
            "benchmark": payload["benchmark"]["name"],
        },
        "requested": {"institution": institution, "variety": variety,
                      "from": start, "to": end, "limit": limit},
        "window": payload["window"],
        "varieties": payload["varieties"],
        "institutions": [meta for meta in payload["institutions"] if meta["id"] in selected],
        "aggregation_note": payload["aggregation_note"],
        "scope_note": payload["scope_note"],
        "benchmark": {**payload["benchmark"], "close": payload["benchmark"]["close"][lo:hi]},
        "market_lines": market_lines,
        "dates": dates[lo:hi],
        "series": series,
        "stats": stats,
        "quality": payload["quality"],
    }


def validate_params(institution: str, variety: str, start: str | None, end: str | None,
                    limit: int) -> str | None:
    """返回错误说明；None 表示参数合法。"""
    if institution not in ("all", *INSTITUTION_IDS):
        return f"institution 只支持 {', '.join(('all', *INSTITUTION_IDS))}"
    if variety not in VARIETIES:
        return f"variety 只支持 {', '.join(VARIETIES)}"
    for label, value in (("from", start), ("to", end)):
        if value:
            if not isinstance(value, str):
                return f"{label} 必须为 YYYY-MM-DD"
            try:
                date.fromisoformat(value)
            except ValueError:
                return f"{label} 必须为 YYYY-MM-DD"
    if start and end and start > end:
        return "from 不得晚于 to"
    if limit < 1 or limit > MAX_LIMIT:
        return f"limit 必须在 1–{MAX_LIMIT} 之间"
    return None
