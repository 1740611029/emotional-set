"""同花顺博主发文（【全能的野人】）只读展示层。

数据来源：独立的采集脚本 `同花顺博主追踪/collect.js`
- 结构化落盘：`同花顺博主追踪/data/posts.json`
- 人可读记录：`【全能的野人】同花顺发文.md`（与 JSON 同源生成）

本模块只读取已落盘数据，不发起任何网络请求，也不参与信号计算。
"""
from __future__ import annotations

import json
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
        items.append({
            "id": str(item.get("id") or ""),
            "date": str(date),
            "time": str(time),
            "text": str(item.get("text") or ""),
            "stocks": stocks,
            "images": int(item.get("images") or 0),
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
