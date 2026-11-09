"""HTTP 服务：只读快照 API + 前端静态托管。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import paths
from .institutions import (
    DEFAULT_LIMIT, InstitutionStore, slice_payload,
    summary as institution_summary, validate_params,
)
from .scheduler import MarketScheduler
from .snapshot import PayloadStore, SnapshotStore, now_iso
from confirmed_sector_trends import STATE_NAMES  # noqa: E402  # paths 已配置 sys.path

STORE = SnapshotStore()
SECTOR_STORE = PayloadStore()
INSTITUTION_STORE = InstitutionStore()
SCHEDULER = MarketScheduler(STORE, interval=int(os.environ.get("REFRESH_SECONDS", "30")))
HISTORY_DAYS = (60, 120, 250)


@asynccontextmanager
async def lifespan(app: FastAPI):
    await SCHEDULER.start()
    try:
        yield
    finally:
        await SCHEDULER.stop()


app = FastAPI(title="SignalHub 数据服务", version="1.0.0", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
    allow_methods=["GET"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {**SCHEDULER.status(), "server_time": now_iso()}


@app.get("/api/snapshot")
def snapshot():
    data = STORE.get()
    if data is None:
        return JSONResponse(
            status_code=202,
            content={
                "data_state": "preparing",
                "mode": None,
                "as_of": None,
                "generated_at": None,
                "stale": True,
                "stale_since": None,
                "stale_reason": "数据准备中",
                "progress": SCHEDULER.progress.as_dict(),
                "indices": [],
                "sectors": {"as_of": None, "is_mock": True, "groups": {}, "evaluation": None,
                            "subsector_evaluation": None, "algorithm_version": None,
                            "expected_as_of": None, "expected_basis": None, "lag_days": None},
                "recent_signals": [],
                "candidates": [],
                "algorithm_version": None,
            },
        )
    return {**data, "institutions": institution_summary(INSTITUTION_STORE.load())}


@app.get("/api/institutions/positions")
def institution_positions(
    institution: str = "all",
    variety: str = "all",
    start: str | None = Query(None, alias="from"),
    end: str | None = Query(None, alias="to"),
    limit: int = DEFAULT_LIMIT,
):
    error = validate_params(institution, variety, start, end, limit)
    if error:
        raise HTTPException(status_code=400, detail=error)
    payload = INSTITUTION_STORE.load()
    if not payload or not payload.get("dates"):
        return JSONResponse(
            status_code=202,
            content={
                "data_state": "preparing", "stale": True, "as_of": None,
                "generated_at": None, "stale_since": None,
                "stale_reason": "机构持仓数据准备中",
                "dates": [], "series": {}, "stats": {},
            },
        )
    return slice_payload(payload, institution=institution, variety=variety,
                         start=start, end=end, limit=limit)


@app.get("/api/indices/{code}/history")
def index_history(code: str) -> dict:
    data = STORE.get()
    if not data:
        raise HTTPException(status_code=503, detail="数据准备中")
    for item in data["indices"]:
        if item["code"] == code:
            return {
                "code": item["code"], "name": item["name"],
                "as_of": data["as_of"], "mode": data["mode"],
                "history": item["history"], "signals": item["signals"],
                "current_signal": item["current_signal"],
                "accuracy": item["accuracy"],
                "candidates": item.get("candidates", []),
            }
    raise HTTPException(status_code=404, detail=f"未知指数代码：{code}")


def _find_sector(payload: dict, code: str) -> tuple[dict | None, dict | None]:
    for rows in payload.get("groups", {}).values():
        for item in rows:
            if item.get("code") == code:
                return item, None
            for sub in item.get("subsectors", []):
                if sub.get("code") == code:
                    return sub, item
    return None, None


@app.get("/api/sectors/{code}/history")
def sector_history(code: str, days: int = 120) -> dict:
    if days not in HISTORY_DAYS:
        raise HTTPException(status_code=400, detail="days 只支持 60 / 120 / 250")
    payload = SECTOR_STORE.sectors()
    if not payload:
        raise HTTPException(status_code=503, detail="数据准备中")
    item, parent = _find_sector(payload, code)
    if item is None:
        raise HTTPException(status_code=404, detail=f"未知板块代码：{code}")
    dates = payload.get("dates", [])
    close = item.get("series", {}).get("close", [])
    states = item.get("series", {}).get("state", [])
    total = min(len(dates), len(close), len(states))
    take = min(days, total)
    start = total - take
    series = [{
        "date": dates[index],
        "close": close[index],
        "state": STATE_NAMES.get(states[index]) if states[index] else None,
    } for index in range(start, total)]
    snapshot = STORE.get() or {}
    return {
        "code": item.get("code"), "name": item.get("name"), "level": item.get("level"),
        "parent": parent.get("name") if parent else item.get("parent"),
        "as_of": payload.get("as_of"), "date": item.get("date"),
        "requested_days": days,
        "state": item.get("state"), "strength": item.get("strength"),
        "strength_parts": item.get("strength_parts"),
        "strength_basis": item.get("strength_basis"),
        "days": item.get("days"),
        "data_anomaly": item.get("data_anomaly"), "data_lag": item.get("data_lag"),
        "series": series,
        "insufficient": take < days,
        "coverage": {
            "start": series[0]["date"] if series else None,
            "end": series[-1]["date"] if series else None,
            "points": len(series),
        },
        "stale": bool(snapshot.get("stale")),
        "stale_since": snapshot.get("stale_since"),
    }


@app.get("/{full_path:path}")
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(status_code=404, detail="接口不存在")
    dist = paths.WEB_DIST
    if full_path:
        candidate = (dist / full_path).resolve()
        if dist.resolve() in candidate.parents and candidate.is_file():
            return FileResponse(candidate)
    index_file = dist / "index.html"
    if index_file.exists():
        return FileResponse(index_file)
    return JSONResponse(
        status_code=404,
        content={"detail": "前端尚未构建，请先在 signal-hub-web 执行 npm run build"},
    )
