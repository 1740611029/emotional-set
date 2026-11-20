"""个股日线只读取数：点击连板个股时按代码现拉不复权日线，仅用于弹窗展示。

口径（用户 2026-11-20 确认，当日由东财改用新浪源）：
- 数据源 akshare 新浪财经 stock_zh_a_hist_sina；
  东财 push2 在本机持续被拦截，且 akshare 把连接失败也包装成「限流」文案，故换源；
- 不复权 adjust=""，与项目既有板块日线口径保持一致；
- 只取近 60 个交易日，截止日只到今日，只含已发生交易日，不预测、不补齐、不插值；
- 涨跌幅由相邻两日收盘价计算（新浪接口不提供该列），只用已发生数据，无未来函数；
- 进程内短 TTL 缓存，避免重复点击重复请求；失败不缓存，便于重试；
- 不参与任何信号计算，不写任何文件（AGENTS §1 / §5 / §10）。
"""
from __future__ import annotations

import threading
import time
from typing import Any

import pandas as pd

HIST_DAYS = 60
CACHE_TTL_SECONDS = 60

_cache: dict[str, tuple[float, dict[str, Any]]] = {}
_cache_lock = threading.Lock()


def _hist_function():
    """新浪 A 股日线：兼容 akshare 1.20.46 起 stock_zh_a_daily → stock_zh_a_hist_sina 的改名。"""
    import akshare as ak

    fn = getattr(ak, "stock_zh_a_hist_sina", None) or getattr(ak, "stock_zh_a_daily", None)
    if fn is None:
        raise RuntimeError("当前 akshare 版本未提供新浪 A 股日线接口")
    return fn


def _sina_symbol(code: str) -> str:
    """6 位 A 股代码转新浪带市场前缀的 symbol；未知前缀报错，不盲猜。"""
    if code.startswith(("60", "68", "9")):
        return f"sh{code}"
    if code.startswith(("00", "30", "2")):
        return f"sz{code}"
    if code.startswith(("4", "8")):
        return f"bj{code}"
    raise ValueError(f"不支持的 A 股代码前缀：{code[:2]}")


def _pick_column(frame: pd.DataFrame, *names: str) -> str | None:
    for name in names:
        if name in frame.columns:
            return name
    return None


def _to_points(frame: pd.DataFrame) -> list[dict[str, Any]]:
    """把新浪英文列名映射为前端 K 线契约（OHLC + 成交量）；不推算价格，只按已发生数据算涨跌幅。"""
    date_col = _pick_column(frame, "日期", "date")
    open_col = _pick_column(frame, "开盘", "open")
    close_col = _pick_column(frame, "收盘", "close")
    high_col = _pick_column(frame, "最高", "high")
    low_col = _pick_column(frame, "最低", "low")
    vol_col = _pick_column(frame, "成交量", "volume")
    required = {
        "日期": date_col,
        "开盘": open_col,
        "收盘": close_col,
        "最高": high_col,
        "最低": low_col,
    }
    missing = [name for name, col in required.items() if not col]
    if missing:
        raise ValueError(f"日线缺少字段：{'、'.join(missing)}（K 线需完整 OHLC）")

    points: list[dict[str, Any]] = []
    for _, row in frame.iterrows():
        day = str(row[date_col])[:10]
        try:
            values = [float(row[col]) for col in (open_col, close_col, high_col, low_col)]
        except (TypeError, ValueError):
            continue
        if not day or any(pd.isna(value) for value in values):
            continue
        open_, close, high, low = values

        volume: int | None = None
        if vol_col:
            try:
                volume = int(float(row[vol_col]))
            except (TypeError, ValueError):
                volume = None

        points.append(
            {
                "date": day,
                "open": round(open_, 2),
                "close": round(close, 2),
                "high": round(high, 2),
                "low": round(low, 2),
                "change_pct": None,
                "volume": volume,
            }
        )

    # 新浪不返回涨跌幅列：用相邻两个已发生交易日的收盘价计算，不用任何未来数据
    points.sort(key=lambda point: point["date"])
    for index in range(1, len(points)):
        previous_close = points[index - 1]["close"]
        if previous_close > 0:
            points[index]["change_pct"] = round(
                (points[index]["close"] - previous_close) / previous_close * 100, 2
            )
    return points[-HIST_DAYS:]


def fetch_daily(code: str) -> dict[str, Any]:
    """现拉单只个股日线；任何失败都返回 error 结构，不向前端抛半成品。"""
    if len(code) != 6 or not code.isdigit():
        return {
            "code": code,
            "data_state": "error",
            "error": "股票代码需为 6 位数字",
            "points": [],
        }
    try:
        symbol = _sina_symbol(code)
    except ValueError as exc:
        return {"code": code, "data_state": "error", "error": str(exc), "points": []}

    try:
        frame = _hist_function()(symbol=symbol, adjust="")
    except Exception as exc:  # 网络/限流/接口变更统一降级为可展示错误
        message = str(exc)
        if "限流" in message or "RateLimit" in type(exc).__name__:
            error = "日线数据源暂时限流，请稍后再试"
        else:
            error = f"日线数据拉取失败：{message[:160]}"
        return {"code": code, "data_state": "error", "error": error, "points": []}

    if frame is None or getattr(frame, "empty", True):
        return {
            "code": code,
            "data_state": "error",
            "error": "该代码在新浪财经无日线数据",
            "points": [],
        }
    try:
        points = _to_points(frame)
    except (ValueError, KeyError) as exc:
        return {
            "code": code,
            "data_state": "error",
            "error": f"日线字段解析失败：{exc}",
            "points": [],
        }
    if not points:
        return {
            "code": code,
            "data_state": "error",
            "error": "该代码无有效收盘价记录",
            "points": [],
        }

    return {
        "code": code,
        "data_state": "ready",
        "source": "akshare 新浪财经",
        "adjust": "不复权",
        "as_of": points[-1]["date"],
        "error": None,
        "points": points,
    }


def load_daily(code: str) -> dict[str, Any]:
    """带 60 秒进程内缓存的读取；只缓存成功结果，失败可立即重试。"""
    code = str(code).strip()
    now = time.monotonic()
    with _cache_lock:
        cached = _cache.get(code)
        if cached and now - cached[0] < CACHE_TTL_SECONDS:
            return cached[1]

    payload = fetch_daily(code)
    if payload.get("data_state") == "ready":
        with _cache_lock:
            _cache[code] = (now, payload)
    return payload
