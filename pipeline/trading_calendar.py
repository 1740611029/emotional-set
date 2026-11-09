"""交易日历与"最近已完成交易日"判定。

只服务于数据刷新调度与页面提示，MUST NOT 被任何顶部/底部信号、板块趋势或候选状态计算读取。

口径：
- 日历源：akshare 新浪交易日历（`tool_trade_date_hist_sina`），落盘缓存，覆盖到当年 12-31 才不再联网；
- 无缓存或缓存未覆盖当前日期时，退回"工作日近似"，并在返回值 `basis` 中标注，便于页面说明口径；
- 15:00（含）之后才算当日收盘：15:00 之前最近已完成交易日是上一交易日，避免把未完成的当日日线判成缺口。
"""
from __future__ import annotations

import bisect
import json
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
CACHE_PATH = ROOT / "data" / "raw" / "trade-calendar.json"
CN = ZoneInfo("Asia/Shanghai")
CLOSE_MINUTE = 15 * 60
CACHE_SOURCE = "akshare tool_trade_date_hist_sina"

# 内存缓存：dates 为空列表表示"已知无日历"，避免热路径反复读盘
_MEM: dict[str, object] = {"loaded": False, "dates": []}


def _parse(values) -> list[date]:
    result = set()
    for value in values:
        text = str(value)[:10]
        try:
            result.add(datetime.strptime(text, "%Y-%m-%d").date())
        except ValueError:
            continue
    return sorted(result)


def _to_date(text: str) -> date:
    return datetime.strptime(text[:10], "%Y-%m-%d").date()


def read_cache() -> dict | None:
    """只读本地缓存，绝不联网；供快照组装等热路径使用。"""
    if not CACHE_PATH.exists():
        return None
    try:
        payload = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(payload, dict):
        return None
    dates = _parse(payload.get("dates") or [])
    if not dates:
        return None
    return {"dates": dates, "cached_at": payload.get("cached_at"), "source": payload.get("source")}


def _write_cache(dates: list[date]) -> None:
    CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "source": CACHE_SOURCE,
        "cached_at": datetime.now(CN).isoformat(timespec="seconds"),
        "dates": [item.strftime("%Y-%m-%d") for item in dates],
    }
    tmp = CACHE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    tmp.replace(CACHE_PATH)


def _remember(cached: dict | None) -> None:
    _MEM["loaded"] = True
    _MEM["dates"] = cached["dates"] if cached else []


def refresh_calendar(*, force: bool = False) -> dict | None:
    """需要时才联网刷新日历缓存；失败保留旧缓存，返回当前可用内容。"""
    cached = read_cache()
    year = datetime.now(CN).year
    if cached and not force and cached["dates"][-1] >= date(year, 12, 31):
        _remember(cached)
        return cached
    try:
        import akshare as ak  # 延迟导入，避免仅为读缓存而加载

        frame = ak.tool_trade_date_hist_sina()
        dates = _parse(frame["trade_date"].tolist())
    except Exception as exc:  # 网络与解析异常都退回缓存/近似
        print(f"[calendar] 交易日历刷新失败，沿用本地缓存：{type(exc).__name__}: {exc}", flush=True)
        _remember(cached)
        return cached
    if not dates:
        print("[calendar] 交易日历返回空数据，沿用本地缓存", flush=True)
        _remember(cached)
        return cached
    _write_cache(dates)
    result = {"dates": dates, "source": CACHE_SOURCE}
    _remember(result)
    print(f"[calendar] 交易日历已缓存至 {dates[-1]:%Y-%m-%d}（{len(dates)} 个交易日）", flush=True)
    return result


def trading_days() -> list[date] | None:
    """内存 → 磁盘缓存；两处都没有时返回 None，由调用方走工作日近似。"""
    if not _MEM["loaded"]:
        _remember(read_cache())
    dates = _MEM["dates"] or []
    return dates or None


def last_completed_trading_day(now: datetime | None = None) -> tuple[str, str]:
    """返回 (日期字符串, 口径)：口径为 calendar 或 workday（近似）。"""
    now = now or datetime.now(CN)
    today = now.date()
    minutes = now.hour * 60 + now.minute
    days = trading_days()
    if days:
        index = bisect.bisect_right(days, today)
        if index and days[index - 1] == today and minutes < CLOSE_MINUTE:
            index -= 1  # 当日尚未收盘，不算最近已完成交易日
        if index:
            return days[index - 1].strftime("%Y-%m-%d"), "calendar"
    candidate = today if minutes >= CLOSE_MINUTE else today - timedelta(days=1)
    while candidate.weekday() >= 5:
        candidate -= timedelta(days=1)
    return candidate.strftime("%Y-%m-%d"), "workday"


def lag_trading_days(earlier: str | None, later: str | None) -> int:
    """earlier 落后 later 多少个交易日；非交易日按其后最近交易日归位，落后为 0 表示已跟上。"""
    if not earlier or not later or earlier >= later:
        return 0
    days = trading_days()
    if days:
        low = bisect.bisect_right(days, _to_date(earlier))
        high = bisect.bisect_right(days, _to_date(later))
        return max(high - low, 0)
    count, cursor = 0, _to_date(earlier) + timedelta(days=1)
    end = _to_date(later)
    while cursor <= end:
        if cursor.weekday() < 5:
            count += 1
        cursor += timedelta(days=1)
    return count


def _reset_memory() -> None:
    """仅供测试重置内存缓存。"""
    _MEM.update({"loaded": False, "dates": []})
