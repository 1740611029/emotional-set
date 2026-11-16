"""同花顺博主发文（【全能的野人】）展示层。

数据来源：独立的采集脚本 `同花顺博主追踪/collect.js`
- 结构化落盘：`同花顺博主追踪/data/posts.json`
- 人可读记录：`【全能的野人】同花顺发文.md`（与 JSON 同源生成）

列表读取为只读模式；`trigger_refresh` 负责拉起 collect.js 完成手动采集，
采集与落盘逻辑全部复用脚本现有实现，本模块不发起任何同花顺网络请求，也不参与信号计算。
"""
from __future__ import annotations

import asyncio
import json
import os
import shutil
import subprocess
import time
from datetime import datetime
from pathlib import Path

from . import paths

POSTS_FILE = paths.ROOT / "同花顺博主追踪" / "data" / "posts.json"
MD_FILE = paths.ROOT / "【全能的野人】同花顺发文.md"
# 网页在线标记：采集脚本据此决定桌面通知由网页侧还是采集侧发出（避免同一条发文弹两次）
PRESENCE_FILE = paths.ROOT / "同花顺博主追踪" / "data" / "web-presence.json"

AUTHOR = "全能的野人"
USER_ID = 639669238
HOMEPAGE = "https://t.10jqka.com.cn/lgt/user_page/?fid=305529#/"
RETENTION_DAYS = 30


def _empty_payload() -> dict:
    return {
        "author": AUTHOR,
        "user_id": USER_ID,
        "homepage": HOMEPAGE,
        "retention_days": RETENTION_DAYS,
        "updated_at": None,
        "total": 0,
        "posts": [],
    }


def load_posts() -> dict:
    """读取采集结果；文件缺失或损坏时返回空结构，由前端提示，不伪造内容。"""
    payload = _empty_payload()
    if not POSTS_FILE.exists():
        return payload

    try:
        data = json.loads(POSTS_FILE.read_text(encoding="utf-8-sig"))
    except (OSError, json.JSONDecodeError):
        return payload

    raw_posts = data.get("posts") or []
    items: list[dict] = []
    for item in raw_posts:
        if not isinstance(item, dict):
            continue
        date = item.get("date")
        time = item.get("time")
        if not date or not time:
            continue
        stocks = []
        for stock in item.get("stocks") or []:
            if isinstance(stock, dict) and stock.get("name"):
                stocks.append({"name": str(stock["name"]), "code": str(stock.get("code") or "")})
        # 配图直链（同花顺 u.thsi.cn CDN，浏览器直接热链加载）
        image_urls = [
            str(url)
            for url in item.get("image_urls") or []
            if isinstance(url, str) and url.startswith(("http://", "https://"))
        ]
        items.append({
            "id": str(item.get("id") or ""),
            "date": str(date),
            "time": str(time),
            "text": str(item.get("text") or ""),
            "stocks": stocks,
            "images": int(item.get("images") or 0),
            "image_urls": image_urls,
            "url": str(item.get("url") or ""),
        })

    # 展示口径：最新在前
    items.sort(key=lambda row: (row["date"], row["time"]), reverse=True)

    payload["posts"] = items
    payload["total"] = len(items)
    payload["updated_at"] = _updated_at()
    return payload


def _updated_at() -> str | None:
    """记录文件最后写入时间：以 Markdown 为准（仅在有新发文或清理时写盘）。"""
    for candidate in (MD_FILE, POSTS_FILE):
        try:
            if candidate.exists():
                ts = datetime.fromtimestamp(candidate.stat().st_mtime)
                return ts.strftime("%Y-%m-%d %H:%M:%S")
        except OSError:
            continue
    return None


def mark_presence(online: bool, notify: bool) -> dict:
    """记录网页在线状态，供采集脚本决定桌面通知由哪一侧发出。

    - online=True：网页心跳，at 为毫秒时间戳，notify 表示浏览器通知是否已授权。
    - online=False：页面卸载（sendBeacon），at=0 让采集脚本立即接管，不必等心跳过期。
    写标记失败时返回 at=0，采集脚本按「网页不在线」兜底发通知，不会漏提醒。
    """
    payload = {
        "at": int(time.time() * 1000) if online else 0,
        "notify": bool(notify) if online else False,
    }
    try:
        PRESENCE_FILE.parent.mkdir(parents=True, exist_ok=True)
        tmp = PRESENCE_FILE.with_name(PRESENCE_FILE.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        tmp.replace(PRESENCE_FILE)
    except OSError:
        return {"at": 0, "notify": False, "written": False}
    return {**payload, "written": True}


# ---------- 手动触发采集 ----------
# 采集脚本位置：同花顺博主追踪/collect.js（零第三方依赖，Node 18+ 可直接运行）
COLLECT_JS = paths.ROOT / "同花顺博主追踪" / "collect.js"
# 计划任务 vbs 所用 node 路径优先，找不到时回退 PATH 中的 node
NODE_CANDIDATES = (Path("E:/nvm/nodejs/node.exe"),)
# 单次采集等待上限：正常 2-5 秒；令牌失效起无头 Chrome 约 3-20 秒；抓取重试最多 3 次
COLLECT_TIMEOUT_SECONDS = 120.0

_collect_lock: asyncio.Lock | None = None


def _collect_lock_instance() -> asyncio.Lock:
    """懒初始化锁：须在事件循环内首次调用（FastAPI 请求处理内安全）。"""
    global _collect_lock
    if _collect_lock is None:
        _collect_lock = asyncio.Lock()
    return _collect_lock


def _find_node() -> Path | None:
    for candidate in NODE_CANDIDATES:
        if candidate.exists():
            return candidate
    found = shutil.which("node")
    return Path(found) if found else None


async def trigger_refresh() -> dict:
    """手动触发一轮采集：同步等待 collect.js 跑完后返回状态。

    - 并发防护：进程内 asyncio.Lock 单飞；collect.js 自带 run.lock（3 分钟窗口）
      兜底计划任务与手动触发撞车，撞车方立即退出且退出码 0。
    - 数据口径不变：采集、去重、落盘、通知逻辑全部复用 collect.js 现有实现。
    - 返回 status：ok=采集完成；busy=已有采集在跑（本次未重复执行）；
      timeout=等待超时已终止进程；error=脚本退出异常。
    """
    if not COLLECT_JS.exists():
        return {"status": "error", "detail": "采集脚本不存在"}
    node = _find_node()
    if node is None:
        return {"status": "error", "detail": "未找到 node 可执行文件"}

    lock = _collect_lock_instance()
    if lock.locked():
        return {"status": "busy", "detail": "已有采集任务在执行"}

    async with lock:
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        try:
            proc = await asyncio.create_subprocess_exec(
                str(node), str(COLLECT_JS),
                cwd=str(COLLECT_JS.parent),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                creationflags=creationflags,
            )
        except OSError as exc:
            return {"status": "error", "detail": f"启动采集进程失败：{exc}"}

        try:
            out, _ = await asyncio.wait_for(proc.communicate(), timeout=COLLECT_TIMEOUT_SECONDS)
        except asyncio.TimeoutError:
            proc.kill()
            await proc.wait()
            return {"status": "timeout", "detail": f"采集超过 {int(COLLECT_TIMEOUT_SECONDS)} 秒未完成，已终止"}

    text = (out or b"").decode("utf-8", errors="replace")
    lines = [line for line in text.strip().splitlines() if line.strip()]
    tail = lines[-1] if lines else ""
    if proc.returncode == 0:
        if "本轮跳过" in text:
            return {"status": "busy", "detail": tail}
        return {"status": "ok", "detail": tail}
    return {"status": "error", "detail": tail or f"采集进程退出码 {proc.returncode}"}
