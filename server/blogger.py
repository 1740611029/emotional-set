"""同花顺博主发文（【全能的野人】）只读展示层。

数据来源：独立的采集脚本 `同花顺博主追踪/collect.js`
- 结构化落盘：`同花顺博主追踪/data/posts.json`
- 人可读记录：`【全能的野人】同花顺发文.md`（与 JSON 同源生成）

本模块只读取已落盘数据，不发起任何网络请求，也不参与信号计算。
"""
from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from . import paths

POSTS_FILE = paths.ROOT / "同花顺博主追踪" / "data" / "posts.json"
MD_FILE = paths.ROOT / "【全能的野人】同花顺发文.md"

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
