"""盘中实时行情适配层。

主源：akshare 新浪指数快照（一次批量返回全部指数，代码形如 sh000001）
备源：akshare 东方财富指数快照（代码为 6 位数字，需要映射）

本模块只负责取数与归一化，不做任何信号计算；失败一律返回 None 由上层降级。
"""
from __future__ import annotations

import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import numpy as np

from . import paths  # noqa: F401  # 配置 sys.path
from fetch_market import INDEXES  # noqa: E402

CN = ZoneInfo("Asia/Shanghai")
WANTED = set(INDEXES)
SHORT_CODES = {code[-6:]: code for code in INDEXES}

# 实时成交量单位可能与历史日线不同（手 vs 股），用历史量级自动校准
VOLUME_SCALE_RANGE = (50.0, 200.0)


def _to_float(value) -> float | None:
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    if not np.isfinite(number) or number <= 0:
        return None
    return number


def _row_get(row, *names):
    for name in names:
        if name in row.index:
            return row[name]
    return None


def _normalize_volume(value: float, history_volumes) -> float:
    if history_volumes is None or len(history_volumes) == 0:
        return value
    median = float(np.median(np.asarray(history_volumes, dtype=float)[-20:]))
    if median <= 0:
        return value
    ratio = median / value
    if VOLUME_SCALE_RANGE[0] <= ratio <= VOLUME_SCALE_RANGE[1]:
        return value * 100.0
    return value


def _build_quote(code: str, name: str, close, open_, high, low, volume, history_volumes) -> dict | None:
    values = {key: _to_float(raw) for key, raw in
              (("close", close), ("open", open_), ("high", high), ("low", low), ("volume", volume))}
    if any(values[key] is None for key in ("close", "open", "high", "low")):
        return None
    if values["high"] < max(values["open"], values["close"]) or values["low"] > min(values["open"], values["close"]):
        return None
    values["volume"] = _normalize_volume(values["volume"] or 0.0, history_volumes)
    if values["volume"] <= 0:
        return None
    return {
        "code": code, "name": name,
        "open": values["open"], "high": values["high"], "low": values["low"],
        "close": values["close"], "volume": values["volume"],
        "ts": datetime.now(CN).isoformat(timespec="seconds"),
    }


def _fetch_sina(history_volumes: dict[str, list[float]]) -> dict[str, dict]:
    import akshare as ak

    frame = ak.stock_zh_index_spot_sina()
    quotes: dict[str, dict] = {}
    for _, row in frame.iterrows():
        code = str(_row_get(row, "代码") or "").strip()
        if code not in WANTED:
            continue
        quote = _build_quote(
            code, INDEXES[code],
            _row_get(row, "最新价"), _row_get(row, "今开"),
            _row_get(row, "最高"), _row_get(row, "最低"), _row_get(row, "成交量"),
            history_volumes.get(code),
        )
        if quote:
            quotes[code] = quote
    return quotes


def _fetch_eastmoney(history_volumes: dict[str, list[float]]) -> dict[str, dict]:
    import akshare as ak

    frame = ak.stock_zh_index_spot_em(symbol="沪深重要指数")
    quotes: dict[str, dict] = {}
    for _, row in frame.iterrows():
        raw = str(_row_get(row, "代码") or "").strip()
        code = SHORT_CODES.get(raw.zfill(6))
        if not code:
            continue
        quote = _build_quote(
            code, INDEXES[code],
            _row_get(row, "最新价"), _row_get(row, "今开"),
            _row_get(row, "最高"), _row_get(row, "最低"), _row_get(row, "成交量"),
            history_volumes.get(code),
        )
        if quote:
            quotes[code] = quote
    return quotes


def fetch_spot(history_volumes: dict[str, list[float]] | None = None,
               errors: list[str] | None = None) -> dict[str, dict] | None:
    """返回 {code: quote}；全部失败返回 None。"""
    history_volumes = history_volumes or {}
    for name, loader in (("sina", _fetch_sina), ("eastmoney", _fetch_eastmoney)):
        try:
            quotes = loader(history_volumes)
        except Exception as exc:  # 网络与解析异常都走降级
            message = f"{name} 实时行情失败：{type(exc).__name__}: {exc}"
            print(f"[realtime] {message}", flush=True)
            if errors is not None:
                errors.append(message)
            continue
        if quotes:
            missing = WANTED - set(quotes)
            if missing:
                print(f"[realtime] {name} 缺少指数：{sorted(missing)}", flush=True)
            return quotes
        if errors is not None:
            errors.append(f"{name} 返回空结果")
    return None


def trading_session(now: datetime | None = None) -> bool:
    """仅按时间窗判断，节假日由实时源回执二次校验。"""
    now = now or datetime.now(CN)
    if now.weekday() >= 5:
        return False
    minutes = now.hour * 60 + now.minute
    return (9 * 60 + 30 <= minutes <= 11 * 60 + 30) or (13 * 60 <= minutes <= 15 * 60)
