"""ETF 成交额序列：只读原始日线组装展示序列，不参与任何信号与放量计算。

依赖方向单向：本模块只读 `data/raw/` 下的 ETF 日线与指数日线，不 import 任何
信号模块，也不写任何文件。放量事件（L3 日期）直接取自生产产物，级别口径由
`etf-volume-spike` 唯一决定，展示层不自算。

齐备区间口径：6 只 ETF 全部已有日线的交易日交集；区间内任一只缺当日成交额则
整日剔除（合计不补 0、不用部分标的之和冒充）。指数按日期查表对齐，缺失为
`None`，不向前填充、不插值。
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

import pandas as pd

from . import paths
from .snapshot import PayloadStore, now_iso  # noqa: F401  # snapshot 已配置 sys.path

AMOUNT_COLUMNS = ("date", "open", "close", "high", "low", "volume", "amount")
TRADING_DAY_FIELDS = ("start", "end", "trading_days")


def _source_files(universe: list[dict]) -> list[Path]:
    """缓存键覆盖的全部输入文件；缺失文件不计入（由读盘阶段抛降级原因）。"""
    files = [paths.ETF_DAILY_MANIFEST]
    files += [paths.ETF_DAILY_RAW / f"{item['code']}.csv" for item in universe]
    files.append(paths.INDEX_THS_RAW / f"{paths.SH_INDEX_CODE}.csv")
    return [path for path in files if path.exists()]


def _read_etf_daily(code: str) -> dict[str, float]:
    """单只 ETF 日线 → {日期: 成交额（元）}；只认非 NaN 数值行。"""
    frame = pd.read_csv(paths.ETF_DAILY_RAW / f"{code}.csv", dtype={"date": str})
    missing = [column for column in ("date", "amount") if column not in frame.columns]
    if missing:
        raise ValueError(f"{code} 日线缺少字段：{missing}")
    amounts = pd.to_numeric(frame["amount"], errors="coerce")
    return {date: float(amount)
            for date, amount in zip(frame["date"], amounts)
            if pd.notna(amount)}


def _read_index_close() -> dict[str, float]:
    """上证指数日线 → {日期: 收盘价}；与 ETF 日期轴查表对齐，不做任何填充。"""
    path = paths.INDEX_THS_RAW / f"{paths.SH_INDEX_CODE}.csv"
    frame = pd.read_csv(path, dtype={"date": str})
    if "close" not in frame.columns:
        raise ValueError(f"{path.name} 缺少 close 字段")
    closes = pd.to_numeric(frame["close"], errors="coerce")
    return {date: float(close)
            for date, close in zip(frame["date"], closes)
            if pd.notna(close)}


def _assemble(universe: list[dict], events: PayloadStore) -> dict:
    """读盘组装整份列式序列；任何文件/内容异常都由调用方降级为 preparing。"""
    if len(universe) != 6:
        raise ValueError(f"manifest universe 应有 6 只，实际 {len(universe)}")
    for item in universe:
        for key in ("code", "name", "index"):
            if not item.get(key):
                raise ValueError(f"manifest universe 条目缺少 {key}：{item}")

    amount_maps = {item["code"]: _read_etf_daily(item["code"]) for item in universe}
    # 齐备区间 = 6 份日线日期集合的交集（升序）；区间内任一只缺当日额也整日剔除
    common = set.intersection(*(set(mapping) for mapping in amount_maps.values()))
    dates = sorted(day for day in common
                   if all(pd.notna(amount_maps[code][day]) for code in amount_maps))
    if not dates:
        raise ValueError("6 份 ETF 日线没有共同的齐备交易日")

    etfs = [{
        "code": item["code"], "name": item["name"], "index": item["index"],
        "amount": [round(amount_maps[item["code"]][day], 2) for day in dates],
    } for item in universe]
    total = [round(sum(amount_maps[item["code"]][day] for item in universe), 2)
             for day in dates]

    index_closes = _read_index_close()
    aligned = [index_closes.get(day) for day in dates]

    # L3 只保留落在齐备区间内的日期；级别口径由生产产物唯一决定
    l3_dates = sorted({row["date"] for row in events.etf_volume_events()
                       if row["level"] == "L3" and row["date"] in set(dates)})

    return {
        "data_state": "ready",
        "as_of": dates[-1],
        "generated_at": now_iso(),
        "coverage": {"start": dates[0], "end": dates[-1], "trading_days": len(dates)},
        "dates": dates,
        "etfs": etfs,
        "total_amount": total,
        "index_close": aligned,
        "l3_dates": l3_dates,
    }


def _freshness(series: dict) -> None:
    """序列终点 vs 最近已完成交易日：陈旧只标记，数值原样保留。"""
    from trading_calendar import lag_trading_days, last_completed_trading_day  # noqa: PLC0415

    expected, basis = last_completed_trading_day()
    lag = lag_trading_days(series["as_of"], expected)
    series["stale"] = lag > 0
    series["stale_reason"] = (
        f"ETF 日线停在 {series['as_of']}，最近已完成交易日为 {expected}（{basis} 口径）"
        if series["stale"] else None)


def _failed(reason: str) -> dict:
    """降级结果也必须字段齐全：前端不做 undefined 猜测，数值一律空数组。"""
    return {
        "data_state": "preparing",
        "as_of": None,
        "generated_at": now_iso(),
        "stale": True,
        "stale_reason": reason,
        "coverage": dict.fromkeys(TRADING_DAY_FIELDS, None),
        "dates": [],
        "etfs": [],
        "total_amount": [],
        "index_close": [],
        "l3_dates": [],
    }


class EtfVolumeStore:
    """按输入文件 max(mtime) 缓存整份序列；读盘异常一律降级，绝不向上抛 5xx。"""

    def __init__(self, events: PayloadStore | None = None) -> None:
        self._events = events or PayloadStore()
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._result: dict | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._mtime, self._result = None, None

    def series(self) -> dict:
        try:
            manifest = json.loads(paths.ETF_DAILY_MANIFEST.read_text(encoding="utf-8"))
            universe = list(manifest.get("universe") or [])
            mtime = max((path.stat().st_mtime for path in _source_files(universe)),
                        default=0.0)
        except Exception as exc:
            return _failed(f"ETF 日线清单不可用：{exc}")
        with self._lock:
            if self._mtime == mtime and self._result is not None:
                return self._result  # 命中缓存：generated_at 固定，重复请求逐字节一致
        try:
            result = _assemble(universe, self._events)
            _freshness(result)
        except Exception as exc:
            print(f"[etf_volume] 序列组装失败（已降级为准备中）：{exc}", flush=True)
            return _failed(f"ETF 成交额序列暂不可用：{exc}")
        with self._lock:
            self._mtime, self._result = mtime, result
        return result
