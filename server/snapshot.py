"""快照组装：把落盘 JSON 与盘中实时行情合成统一响应。

- 收盘态：直接使用落盘数据（`data/*.json`），保证与流水线输出逐字节一致。
- 盘中态：在日线序列尾部合成"当日未完成 K 线"后复用同一套确认算法，
  结果只存在于内存，标记 `provisional`，不写回任何文件。
"""
from __future__ import annotations

import json
import threading
from datetime import datetime
from zoneinfo import ZoneInfo

import pandas as pd

from . import paths  # noqa: F401  # 配置 sys.path
from candidate_state import candidate_records  # noqa: E402
from confirmed_index_signals import (  # noqa: E402
    ACTIVE_ALGORITHM, ALGORITHM_VERSION, confirmed_signals, detail_records,
    evaluate_signals, legacy_accuracy, signal_details, signal_records,
)
from research_algorithms import read_index_csv, read_sector_csv  # noqa: E402
from swing_indicators import breadth_table  # noqa: E402
from bigwave_bottom_engine import bottom_zones  # noqa: E402
from trading_calendar import lag_trading_days, last_completed_trading_day  # noqa: E402

CN = ZoneInfo("Asia/Shanghai")


def now_iso() -> str:
    return datetime.now(CN).isoformat(timespec="seconds")


def today_str() -> str:
    return datetime.now(CN).strftime("%Y-%m-%d")


def current_signal(signals: list[dict], history: list[dict]) -> dict:
    active_dates = {row["date"] for row in history[-10:]}
    active = [row for row in signals if row["date"] in active_dates]
    if not active:
        return {"type": "none", "date": None}
    latest = active[-1]
    return {"type": latest["type"], "date": latest["date"]}


class HistoryStore:
    """指数日线内存缓存，避免每 30 秒重复读盘。"""

    def __init__(self) -> None:
        self._frames: dict[str, pd.DataFrame] = {}
        self._lock = threading.Lock()

    def get(self, code: str) -> pd.DataFrame:
        with self._lock:
            frame = self._frames.get(code)
        if frame is None:
            frame = read_index_csv(paths.DATA / "raw" / "indices-ths" / f"{code}.csv")
            with self._lock:
                self._frames[code] = frame
        return frame

    def volumes(self, codes) -> dict[str, list[float]]:
        result: dict[str, list[float]] = {}
        for code in codes:
            try:
                frame = self.get(code)
                result[code] = frame["volume"].tail(20).tolist()
            except Exception:
                result[code] = []
        return result

    def invalidate(self) -> None:
        with self._lock:
            self._frames.clear()


class PayloadStore:
    """落盘 JSON 读取缓存，按 mtime 失效。"""

    def __init__(self) -> None:
        self._cache: dict[str, tuple[float, dict]] = {}
        self._lock = threading.Lock()

    def load(self, name: str, path) -> dict | None:
        if not path.exists():
            return None
        mtime = path.stat().st_mtime
        with self._lock:
            cached = self._cache.get(name)
        if cached and cached[0] == mtime:
            return cached[1]
        payload = json.loads(path.read_text(encoding="utf-8"))
        with self._lock:
            self._cache[name] = (mtime, payload)
        return payload

    def indices(self) -> dict | None:
        return self.load("index-signals.json", paths.INDEX_SIGNALS)

    def sectors(self) -> dict | None:
        return self.load("sector-trends.json", paths.SECTOR_TRENDS)

    def etf_volume_events(self) -> list[dict]:
        """ETF 放量事件日（按日期升序）；产物缺失、解析失败一律降级为空数组。"""
        try:
            payload = self.load("etf-volume-events.json", paths.ETF_VOLUME_EVENTS)
        except Exception as exc:  # 展示用产物损坏不得把整个快照打成 500
            print(f"[snapshot] ETF 放量事件读取失败（已降级为空）：{exc}", flush=True)
            return []
        if not payload:
            return []
        rows = [{
            "date": str(row["date"]), "level": row["level"],
            "etf_codes": list(row.get("etf_codes") or []),
            "total_amount": float(row["total_amount"]),
        } for row in payload.get("days") or []]
        return sorted(rows, key=lambda row: row["date"])

    def invalidate(self) -> None:
        with self._lock:
            self._cache.clear()

    def version(self) -> tuple[float, float, float]:
        """落盘文件版本（mtime），用于判断是否需要重建快照。"""
        def mtime(path) -> float:
            return path.stat().st_mtime if path.exists() else 0.0
        return (mtime(paths.INDEX_SIGNALS), mtime(paths.SECTOR_TRENDS),
                mtime(paths.ETF_VOLUME_EVENTS))


# 板块为单层结构：条目只有自身判定结果，无层级、父级与二级列表字段
_SECTOR_SUMMARY_KEYS = (
    "code", "name", "date", "state", "strength", "strength_parts",
    "strength_basis", "days", "data_anomaly", "data_lag", "spark",
)


def _sector_item(item: dict) -> dict:
    """只保留摘要字段与 30 日缩略序列；长序列由历史端点按需提供。"""
    return {key: item.get(key) for key in _SECTOR_SUMMARY_KEYS}


def _sector_block(payload: dict | None) -> dict:
    """板块快照：只读落盘产物，另补充与最近已完成交易日的差距供页面提示。"""
    expected, basis = last_completed_trading_day()
    if not payload:
        return {"as_of": None, "is_mock": True, "groups": {}, "evaluation": None,
                "algorithm_version": None,
                "expected_as_of": expected, "expected_basis": basis, "lag_days": None}
    as_of = payload.get("as_of")
    return {
        "as_of": as_of,
        "expected_as_of": expected,
        "expected_basis": basis,
        "lag_days": lag_trading_days(as_of, expected),
        "is_mock": payload.get("is_mock", False),
        "algorithm_version": payload.get("algorithm_version"),
        "groups": {group: [_sector_item(item) for item in rows]
                   for group, rows in payload.get("groups", {}).items()},
        "evaluation": payload.get("evaluation"),
    }


def _index_tail_block(indices_payload: dict | None) -> dict:
    """短板标的可见（D5）：从收盘产物透传 index_tails 并标出落后标的。

    落后标的 = 尾日早于最晚尾日的指数（6 尾日 T、1 尾日 T-1 → 标出 T-1 那只）。
    """
    tails = (indices_payload or {}).get("index_tails") or {}
    as_of = (indices_payload or {}).get("as_of")
    latest_tail = max(tails.values()) if tails else None
    lagging = [code for code, tail in tails.items()
               if latest_tail and tail < latest_tail]
    return {
        "index_tails": tails,
        "lagging_indexes": lagging,
        "as_of_rule": (indices_payload or {}).get("as_of_rule") or "min(index_tails)",
    }


def build_close_snapshot(indices_payload: dict | None, sectors_payload: dict | None,
                         *, stale: bool = False, stale_since: str | None = None,
                         stale_reason: str | None = None,
                         etf_volume_events: list[dict] | None = None) -> dict:
    if not indices_payload:
        raise ValueError("缺少 index-signals.json，无法生成收盘快照")
    indices = []
    for item in indices_payload["indices"]:
        indices.append({**item, "provisional": False})
    return {
        "mode": "close",
        "as_of": indices_payload.get("as_of"),
        **_index_tail_block(indices_payload),
        "generated_at": now_iso(),
        "stale": stale,
        "stale_since": stale_since,
        "stale_reason": stale_reason,
        "provisional": False,
        "data_state": "degraded" if stale else "ready",
        "algorithm_version": ALGORITHM_VERSION,
        "indices": indices,
        # 多持有期矩阵、统计覆盖范围与口径说明（收盘产物直透传）。
        "precision": indices_payload.get("precision"),
        "sectors": _sector_block(sectors_payload),
        "recent_signals": indices_payload.get("recent_signals", []),
        "candidates": indices_payload.get("candidates", []),
        "etf_volume_events": etf_volume_events or [],
    }


def _merged_frame(frame: pd.DataFrame, quote: dict) -> pd.DataFrame:
    """把实时行情合成为当日未完成 K 线；当日已存在则替换末行。"""
    today = pd.Timestamp(today_str())
    amount = float(frame["amount"].tail(20).median()) if "amount" in frame else 0.0
    row = {
        "date": today, "open": float(quote["open"]), "high": float(quote["high"]),
        "low": float(quote["low"]), "close": float(quote["close"]),
        "volume": float(quote["volume"]), "amount": amount,
    }
    columns = ["date", "open", "close", "high", "low", "volume", "amount"]
    if len(frame) and frame["date"].iat[-1].normalize() == today:
        merged = frame.copy()
        for key in ("open", "high", "low", "close", "volume", "amount"):
            merged.loc[merged.index[-1], key] = row[key]
        return merged
    appended = pd.DataFrame([row], columns=columns)
    merged = pd.concat([frame[columns], appended], ignore_index=True)
    return merged


_BREADTH_CACHE: dict[str, object] = {"mtime": None, "frame": None}
_BREADTH_LOCK = threading.Lock()


def breadth_frame() -> pd.DataFrame | None:
    """东财行业板块宽度（与收盘重建读同一份日线），按文件修改时间缓存；板块文件不足 20 个时返回 None。"""
    files = sorted(paths.SECTOR_EM_RAW.glob("*.csv"))
    if len(files) < 20:
        return None
    mtime = max(path.stat().st_mtime for path in files)
    with _BREADTH_LOCK:
        if _BREADTH_CACHE["mtime"] == mtime and _BREADTH_CACHE["frame"] is not None:
            return _BREADTH_CACHE["frame"]  # type: ignore[return-value]
    frame = breadth_table({path.stem: read_sector_csv(path) for path in files})
    with _BREADTH_LOCK:
        _BREADTH_CACHE.update({"mtime": mtime, "frame": frame})
    return frame


def build_live_index(frame: pd.DataFrame, quote: dict, name: str,
                     breadth: pd.DataFrame | None = None) -> dict:
    merged = _merged_frame(frame, quote)
    code = quote["code"]
    if ACTIVE_ALGORITHM == "bigwave":
        records = detail_records(merged, signal_details(merged, breadth, code))
        candidates = []
    elif ACTIVE_ALGORITHM == "swing":
        records = detail_records(merged, signal_details(merged, breadth))
        candidates = candidate_records(merged, breadth)
    else:
        records = signal_records(merged, confirmed_signals(merged, breadth, code))
        candidates = []

    # 准确率只基于真实日线计算：盘中临时信号不进入统计
    daily_signals = confirmed_signals(frame, breadth, code)
    evaluation = evaluate_signals(frame, daily_signals)
    accuracy = legacy_accuracy(evaluation)

    history = [{"date": row.date.strftime("%Y-%m-%d"), "close": float(row.close)}
               for row in merged.itertuples()]
    latest = float(merged["close"].iat[-1])
    previous = float(merged["close"].iat[-2]) if len(merged) > 1 else latest
    return {
        "code": quote["code"], "name": name,
        "latest": latest,
        "change_pct": round((latest / previous - 1) * 100, 4),
        "open": float(quote["open"]), "high": float(quote["high"]),
        "low": float(quote["low"]), "volume": float(quote["volume"]),
        "current_signal": current_signal(records, history),
        "history": history,
        "signals": records,
        "evaluation": evaluation,
        "accuracy": accuracy,
        "candidates": candidates,
        "bottom_zones": bottom_zones(merged, breadth),
        "provisional": True,
    }


def build_live_snapshot(quotes: dict[str, dict], history: HistoryStore,
                        sectors_payload: dict | None, *,
                        stale: bool = False, stale_since: str | None = None,
                        stale_reason: str | None = None,
                        etf_volume_events: list[dict] | None = None,
                        indices_payload: dict | None = None) -> dict:
    from fetch_market import INDEXES  # noqa: PLC0415

    # 盘中只刷新行情与信号；准确率矩阵沿用收盘冻结产物，不在盘中重算（避免口径漂移）。
    matrix_by_code = {
        item["code"]: item.get("precision_matrix")
        for item in (indices_payload or {}).get("indices", [])
    }
    indices = []
    recent: list[dict] = []
    pending: list[dict] = []
    breadth = breadth_frame()
    for code, name in INDEXES.items():
        quote = quotes.get(code)
        if not quote:
            continue
        frame = history.get(code)
        item = build_live_index(frame, quote, name, breadth)
        if matrix_by_code.get(code) is not None:
            item["precision_matrix"] = matrix_by_code[code]
        indices.append(item)
        for signal in item["signals"]:
            recent.append({
                "date": signal["date"], "code": code, "name": name,
                "type": signal["type"], "price": signal["price"],
                "grade": signal.get("grade"),
                "max_drawdown_20": next(
                    (event.get("max_drawdown_20")
                     for segment in item["evaluation"]["segments"]
                     for event in segment["events"]
                     if event["date"] == signal["date"] and event["type"] == signal["type"]),
                    None,
                ),
                "change_since": round((item["latest"] / signal["price"] - 1) * 100, 4),
            })
        pending.extend({**record, "code": code, "name": name} for record in item["candidates"])

    recent.sort(key=lambda row: (row["date"], row["code"]), reverse=True)
    pending.sort(key=lambda row: (row["date"], row["code"]), reverse=True)
    # as_of 口径统一（D5）：与 build_close_snapshot 相同规则——读收盘产物
    # as_of = min(index_tails)（indices_payload 缺失时才退回盘中序列尾日最大值），
    # 不再因收盘/盘中模式不同相差一个交易日。
    tails_block = _index_tail_block(indices_payload)
    if (indices_payload or {}).get("as_of"):
        as_of = indices_payload["as_of"]
    else:
        as_of = max((item["history"][-1]["date"] for item in indices), default=today_str())
        tails_block["as_of_rule"] = "max(live_history_tail)（收盘产物缺失时的兜底口径）"
    return {
        "mode": "live",
        "as_of": as_of,
        **tails_block,
        "generated_at": now_iso(),
        "stale": stale,
        "stale_since": stale_since,
        "stale_reason": stale_reason,
        "provisional": True,
        "data_state": "degraded" if stale else "ready",
        "algorithm_version": ALGORITHM_VERSION,
        "indices": indices,
        "precision": (indices_payload or {}).get("precision"),
        "sectors": _sector_block(sectors_payload),
        "recent_signals": recent,
        "candidates": pending,
        "etf_volume_events": etf_volume_events or [],
    }


class SnapshotStore:
    """线程安全的当前快照容器。"""

    def __init__(self) -> None:
        self._snapshot: dict | None = None
        self._lock = threading.Lock()

    def set(self, snapshot: dict) -> None:
        with self._lock:
            self._snapshot = snapshot

    def get(self) -> dict | None:
        with self._lock:
            return self._snapshot

    @property
    def mode(self) -> str | None:
        snapshot = self.get()
        return snapshot.get("mode") if snapshot else None
