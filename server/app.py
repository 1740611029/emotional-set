"""HTTP 服务：只读快照 API + 前端静态托管。"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import paths
from .blogger import load_posts as blogger_posts
from .etf_volume import EtfVolumeStore
from .institutions import (
    DEFAULT_LIMIT, InstitutionStore, slice_payload,
    summary as institution_summary, validate_params,
)
from .lhb import DEFAULT_LIMIT as LHB_DEFAULT_LIMIT, MAX_LIMIT as LHB_MAX_LIMIT, LhbStore
from .lhb import daily as lhb_daily, overview as lhb_overview, trader_detail as lhb_trader_detail
from .scheduler import MarketScheduler
from .snapshot import PayloadStore, SnapshotStore, now_iso
from confirmed_sector_trends import STATE_NAMES  # noqa: E402  # paths 已配置 sys.path

STORE = SnapshotStore()
SECTOR_STORE = PayloadStore()
INSTITUTION_STORE = InstitutionStore()
# ETF 成交额序列：只读展示层，与快照轮询解耦，不参与任何信号计算
ETF_VOLUME_STORE = EtfVolumeStore()
# 龙虎榜知名游资：只读展示层，不参与任何信号计算
LHB_STORE = LhbStore()
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
                            "algorithm_version": None,
                            "expected_as_of": None, "expected_basis": None, "lag_days": None},
                "recent_signals": [],
                "candidates": [],
                "etf_volume_events": [],
                "algorithm_version": None,
                "precision": None,
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


@app.get("/api/etf/volume-series")
def etf_volume_series():
    """ETF 成交额序列（列式）：读盘异常已降级为 preparing，不返回 5xx。"""
    return ETF_VOLUME_STORE.series()


@app.get("/api/blogger/posts")
def blogger_post_list():
    """同花顺博主【全能的野人】发文列表（只读落盘数据，保留近一个月）。"""
    return blogger_posts()


@app.get("/api/lhb/overview")
def lhb_overview_api():
    """龙虎榜知名游资总览：26 位游资统计 + 最近操作（只读落盘数据）。"""
    return lhb_overview(LHB_STORE.load())


@app.get("/api/lhb/traders/{trader_id}")
def lhb_trader_api(trader_id: str, limit: int = LHB_DEFAULT_LIMIT):
    """单个游资完整操作记录。"""
    limit = max(1, min(int(limit), LHB_MAX_LIMIT))
    return lhb_trader_detail(LHB_STORE.load(), trader_id, limit=limit)


@app.get("/api/lhb/daily/{date}")
def lhb_daily_api(date: str):
    """某交易日全部已收录游资的买卖动向（单日榜口径）。"""
    return lhb_daily(LHB_STORE.load(), date)


@app.get("/api/indices/{code}/history")
def index_history(code: str) -> dict:
    data = STORE.get()
    if not data:
        raise HTTPException(status_code=503, detail="数据准备中")
    for item in data["indices"]:
        if item["code"] == code:
            precision = data.get("precision") or {}
            matrix = item.get("precision_matrix") or {}
            # 个位数样本与不足 30 的标记如实反映，不隐藏、不替读者下结论。
            sample_flags = {}
            for segment in ("train", "validation"):
                horizons = (matrix.get(segment) or {}).get("horizons", {})
                for horizon in ("5", "10", "20", "30"):
                    for kind in ("bottom", "top"):
                        cell = (horizons.get(horizon) or {}).get(kind)
                        if not cell:
                            continue
                        sample_flags[f"{segment}/{horizon}/{kind}"] = {
                            "resolved": cell["resolved"],
                            "small_sample": cell["small_sample"],
                            "single_digit_resolved": cell["resolved"] < 10,
                        }
            return {
                "code": item["code"], "name": item["name"],
                "as_of": data["as_of"], "mode": data["mode"],
                "history": item["history"], "signals": item["signals"],
                "current_signal": item["current_signal"],
                "accuracy": item["accuracy"],
                "candidates": item.get("candidates", []),
                # 多持有期矩阵、拐点三日期与三层确认逐层日志
                "precision_matrix": matrix,
                "precision": precision,
                "pivot_anchors": (precision.get("pivot_anchors") or {}).get(code, []),
                "criteria_note": precision.get("criteria_note"),
                "legacy_rate_note": precision.get("legacy_rate_note"),
                "sample_flags": sample_flags,
                # 与 /api/snapshot 同一份事件：单独查历史时也能复现同样的标记
                "etf_volume_events": data.get("etf_volume_events") or [],
            }
    raise HTTPException(status_code=404, detail=f"未知指数代码：{code}")


def _find_sector(payload: dict, code: str) -> dict | None:
    """单层板块：直接在分组内按代码定位，不依赖层级或父级名称。"""
    for rows in payload.get("groups", {}).values():
        for item in rows:
            if item.get("code") == code:
                return item
    return None


@app.get("/api/sectors/{code}/history")
def sector_history(code: str, days: int = 120) -> dict:
    if days not in HISTORY_DAYS:
        raise HTTPException(status_code=400, detail="days 只支持 60 / 120 / 250")
    payload = SECTOR_STORE.sectors()
    if not payload:
        raise HTTPException(status_code=503, detail="数据准备中")
    item = _find_sector(payload, code)
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
        "code": item.get("code"), "name": item.get("name"),
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
        # 入口 HTML 必须每次校验更新，否则浏览器缓存旧页面会继续加载上一版前端包（需手动刷新）
        return FileResponse(index_file, headers={"Cache-Control": "no-cache, no-store, must-revalidate"})
    return JSONResponse(
        status_code=404,
        content={"detail": "前端尚未构建，请先在 signal-hub-web 执行 npm run build"},
    )
