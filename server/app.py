"""HTTP 服务：只读快照 API + 前端静态托管。"""

from __future__ import annotations

import os
from contextlib import asynccontextmanager

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse

from . import paths
from .blogger import load_posts as blogger_posts
from .blogger import mark_presence as blogger_presence
from .blogger import trigger_refresh as blogger_trigger_refresh
from .etf_share_flow import EtfShareFlowStore
from .etf_volume import EtfVolumeStore
from .institutions import (
    DEFAULT_LIMIT,
    InstitutionStore,
    slice_payload,
    validate_params,
)
from .lhb import (
    DEFAULT_LIMIT as LHB_DEFAULT_LIMIT,
    MAX_LIMIT as LHB_MAX_LIMIT,
    LhbStore,
)
from .lhb import (
    daily as lhb_daily,
    overview as lhb_overview,
    trader_detail as lhb_trader_detail,
)
from .limit_cycle import load_payload as limit_cycle_payload
from .scheduler import MarketScheduler
from .stock_daily import load_daily as stock_daily_load
from .snapshot import SnapshotStore, now_iso

STORE = SnapshotStore()
INSTITUTION_STORE = InstitutionStore()
# ETF 成交额序列：只读展示层，与快照轮询解耦，不参与任何信号计算
ETF_VOLUME_STORE = EtfVolumeStore()
# 宽基 ETF 申购净流入：只读展示层，不参与任何信号计算
ETF_SHARE_FLOW_STORE = EtfShareFlowStore()
# 龙虎榜知名游资：只读展示层，不参与任何信号计算
LHB_STORE = LhbStore()
SCHEDULER = MarketScheduler(
    STORE, interval=int(os.environ.get("REFRESH_SECONDS", "30"))
)


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
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)


@app.get("/api/health")
def health() -> dict:
    return {**SCHEDULER.status(), "server_time": now_iso()}


@app.get("/api/tasks")
def task_list() -> dict:
    """服务端 2 个定时调度分支的开关状态；网页侧轮询开关由前端自行管理。"""
    return {"tasks": SCHEDULER.task_states()}


@app.post("/api/tasks")
def task_switch(payload: dict = Body(default={})):
    """启停单个或全部定时任务：{"task": "close_rebuild" | "lhb_daily" | "all", "enabled": bool}。

    只切换调度分支的开关，不触发任何抓取、重算或信号计算。
    """
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="请求体必须是对象")
    task = payload.get("task")
    enabled = payload.get("enabled")
    if not isinstance(task, str) or not isinstance(enabled, bool):
        raise HTTPException(status_code=400, detail="需要 task 与布尔型 enabled")
    try:
        states = SCHEDULER.set_task_enabled(task, enabled)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"tasks": states}


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
                "data_state": "preparing",
                "stale": True,
                "as_of": None,
                "generated_at": None,
                "stale_since": None,
                "stale_reason": "机构持仓数据准备中",
                "dates": [],
                "series": {},
                "stats": {},
            },
        )
    return slice_payload(
        payload,
        institution=institution,
        variety=variety,
        start=start,
        end=end,
        limit=limit,
    )


@app.get("/api/etf/volume-series")
def etf_volume_series():
    """ETF 成交额序列（列式）：读盘异常已降级为 preparing，不返回 5xx。"""
    return ETF_VOLUME_STORE.series()


@app.get("/api/etf/share-flow")
def etf_share_flow():
    """宽基 ETF 申购净流入（列式）：只读落盘产物，未就绪返回 preparing 不返回 5xx。"""
    return ETF_SHARE_FLOW_STORE.series()


@app.get("/api/blogger/posts")
def blogger_post_list():
    """同花顺博主【全能的野人】发文列表（只读落盘数据，保留近一个月）。"""
    return blogger_posts()


@app.post("/api/blogger/present")
def blogger_presence_api(payload: dict = Body(default={})):
    """网页在线心跳：只写在线标记文件，不触发采集、不参与任何信号计算。

    notify 为浏览器通知授权状态；采集脚本仅在「网页在线且已授权」时跳过桌面 Toast。
    """
    online = bool(payload.get("online", True))
    notify = bool(payload.get("notify", False))
    return blogger_presence(online, notify)


@app.post("/api/blogger/refresh")
async def blogger_refresh_api():
    """手动触发一轮同花顺发文采集（同步等待采集完成后返回）。

    采集逻辑完全复用 collect.js（与计划任务同一脚本），本接口只负责拉起进程并等待，
    不改任何数据口径、不参与信号计算。
    status：ok=采集完成 / busy=已有采集在跑 / timeout / error。
    """
    return await blogger_trigger_refresh()


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


@app.get("/api/limit-cycle")
def limit_cycle_api():
    """连板周期只读数据：产物未落地时返回 preparing 空结构，不返回演示数值。"""
    return limit_cycle_payload()


@app.get("/api/stock/daily/{code}")
def stock_daily_api(code: str):
    """个股日线（连板个股弹窗展示用）：东财不复权近 60 交易日；失败返回 error 结构。"""
    return stock_daily_load(code)


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
        return FileResponse(
            index_file, headers={"Cache-Control": "no-cache, no-store, must-revalidate"}
        )
    return JSONResponse(
        status_code=404,
        content={"detail": "前端尚未构建，请先在 signal-hub-web 执行 npm run build"},
    )
