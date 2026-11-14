"""连板周期只读数据层。

只读取 data/limit-cycle.json 产物；产物缺失、结构不完整或 data_state != ready
时一律返回 preparing 空结构，绝不生成演示数值（AGENTS 第 1 / 5 / 10 条）。
本模块不参与任何信号计算，也不写任何文件。
"""
from __future__ import annotations

import json
from typing import Any

from . import paths

REQUIRED_KEYS = ("phase", "temperature", "ladder", "stocks", "metrics")


def preparing_payload() -> dict[str, Any]:
    """数据未接入时的空结构：所有数值字段为 null / 空数组。"""
    return {
        "data_state": "preparing",
        "as_of": None,
        "generated_at": None,
        "methodology": None,
        "phase": None,
        "temperature": None,
        "ladder": [],
        "stocks": [],
        "metrics": None,
    }


def load_payload() -> dict[str, Any]:
    """读产物；任何异常都降级为 preparing，不向前端抛出半成品数据。"""
    path = paths.LIMIT_CYCLE
    if not path.exists():
        return preparing_payload()
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return preparing_payload()
    if not isinstance(payload, dict) or payload.get("data_state") != "ready":
        return preparing_payload()
    if any(key not in payload for key in REQUIRED_KEYS):
        return preparing_payload()
    if not isinstance(payload.get("ladder"), list) or not isinstance(payload.get("stocks"), list):
        return preparing_payload()
    return payload
