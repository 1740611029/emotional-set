"""确认版板块四态、当前有向强度与持续天数；本模块不写文件。

v1.1 变更：二级板块改为使用自身日线独立判定 `state` / `days`，不再继承所属一级板块。
有向强度采用基础方向分减 50，不再按四态取反；四态判据与持续天数计数规则与 v1.0 完全一致。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_algorithms import (
    evaluate_sector_states,
    sector_state,
    summarize_sector_events,
)

ALGORITHM_VERSION = "sector-donchian20-ma60-v1.1-independent-subsectors"
HISTORY_DAYS = 250
SPARK_DAYS = 30
LAG_LIMIT_DAYS = 30
STATE_CODES = {"bull": "B", "warn": "W", "bear": "D", "rise": "R"}
STATE_NAMES = {code: name for name, code in STATE_CODES.items()}


def validate_history(frame: pd.DataFrame) -> None:
    columns = ("open", "high", "low", "close")
    if not {"date", *columns}.issubset(frame.columns):
        raise ValueError("板块行情缺少 date 或 OHLC 列")
    dates = frame["date"]
    if not pd.api.types.is_datetime64_any_dtype(dates):
        raise ValueError("date 必须为已解析的交易日期")
    if dates.isna().any() or dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
        raise ValueError("板块只接受无时区的有效日线日期")
    if dates.duplicated().any() or not dates.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("交易日期必须唯一且升序，行索引不得重复")
    values = frame[list(columns)]
    if not all(pd.api.types.is_numeric_dtype(values[key]) for key in columns):
        raise ValueError("OHLC 必须为数值")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("OHLC 不得缺失或含非有限值")
    if (values <= 0).any().any():
        raise ValueError("板块价格必须为正")
    # 只严格检查研究段内的 OHLC 关系；历史数据异常仅警告（不影响60日滚动窗口后的计算）
    ohlc_bad = ((frame["high"] < values[["open", "close", "low"]].max(axis=1))
                | (frame["low"] > values[["open", "close", "high"]].min(axis=1)))
    research_start = pd.Timestamp("2020-10-30")
    recent_bad = ohlc_bad & (dates >= research_start)
    if recent_bad.any():
        raise ValueError(f"研究段内 OHLC 高低价关系异常，共 {recent_bad.sum()} 行")
    if ohlc_bad.any():
        import warnings
        warnings.warn(f"历史数据存在 {ohlc_bad.sum()} 行 OHLC 异常（在研究段之前，不影响计算）", UserWarning)


def calculate_sector_trend(frame: pd.DataFrame) -> pd.DataFrame:
    validate_history(frame)
    close = frame["close"]
    highest = frame["high"].rolling(20).max()
    lowest = frame["low"].rolling(20).min()
    average = close.rolling(60).mean()
    width = highest - lowest
    zero_channel = highest.notna() & width.eq(0)

    channel = (close - lowest) / width.replace(0, np.nan) * 100
    ma_score = 50 + 50 * np.tanh((close / average - 1) / 0.10)
    base_strength = (channel + ma_score) / 2
    state = sector_state(frame, "Donchian20_MA60")
    strength = (base_strength - 50).where(state.notna()).clip(-50, 50)
    strength = strength.mask(zero_channel & state.notna(), 0.0)

    # 强度构成：区间位置分（0=区间最低，100=区间最高）与 MA60 偏离分
    range_position = channel.where(state.notna()).clip(0, 100)
    range_position = range_position.mask(zero_channel & state.notna(), 50.0)
    ma_part = ma_score.where(state.notna()).clip(0, 100)
    basis = pd.Series("directional", index=frame.index, dtype="string")
    basis[zero_channel & state.notna()] = "neutral"
    basis[state.isna()] = pd.NA

    days = []
    previous = None
    streak = 0
    for value in state:
        if pd.isna(value):
            previous = None
            streak = 0
            days.append(pd.NA)
            continue
        streak = streak + 1 if value == previous else 1
        previous = value
        days.append(streak)

    return pd.DataFrame({
        "date": frame["date"],
        "state": state,
        "strength": strength,
        "days": pd.Series(days, index=frame.index, dtype="Int64"),
        "data_anomaly": zero_channel,
        "range_position": range_position,
        "ma_score": ma_part,
        "strength_basis": basis,
    }, index=frame.index)


def latest_sector_record(item: dict, trend: pd.DataFrame) -> dict:
    sufficient = not trend.empty and pd.notna(trend["state"].iat[-1])
    parts = None
    if sufficient:
        parts = {
            "range_position": round(float(trend["range_position"].iat[-1]), 4),
            "ma_score": round(float(trend["ma_score"].iat[-1]), 4),
        }
    return {
        "level": item["level"],
        "code": item["code"],
        "name": item["name"],
        "parent": item["parent"],
        "date": None if trend.empty else trend["date"].iat[-1].strftime("%Y-%m-%d"),
        "state": str(trend["state"].iat[-1]) if sufficient else None,
        "strength": round(float(trend["strength"].iat[-1]), 4) if sufficient else None,
        "strength_parts": parts,
        "strength_basis": str(trend["strength_basis"].iat[-1]) if sufficient else None,
        "days": int(trend["days"].iat[-1]) if sufficient else None,
        "data_anomaly": False if trend.empty else bool(trend["data_anomaly"].iat[-1]),
        "data_lag": False,
    }


def _clear_state(record: dict) -> None:
    record["state"] = None
    record["strength"] = None
    record["strength_parts"] = None
    record["strength_basis"] = None
    record["days"] = None


def calculate_sector_universe(manifest: list[dict], frames: dict[str, pd.DataFrame]) -> list[dict]:
    """一级与二级板块各自用自身日线判定四态；二级不再继承父级。"""
    items = [item for item in manifest if item["level"] in {"first", "second"}]
    trends = {item["code"]: calculate_sector_trend(frames[item["code"]]) for item in items}
    result = []
    first_by_name: dict[str, dict] = {}
    for item in items:
        if item["level"] != "first":
            continue
        record = latest_sector_record(item, trends[item["code"]])
        record["subsectors"] = []
        result.append(record)
        first_by_name[item["name"]] = record

    as_of = max((record["date"] for record in result if record["date"]), default=None)
    for record in result:
        record["data_lag"] = bool(as_of and record["date"] and record["date"] != as_of)

    for item in items:
        if item["level"] != "second":
            continue
        if item["parent"] not in first_by_name:
            raise ValueError(f"二级板块 {item['name']} 找不到所属一级板块")
        parent = first_by_name[item["parent"]]
        record = latest_sector_record(item, trends[item["code"]])
        if record["date"] and as_of:
            lag = (pd.Timestamp(as_of) - pd.Timestamp(record["date"])).days
            record["data_lag"] = record["date"] != as_of
            if lag > LAG_LIMIT_DAYS:
                # 数据严重滞后时不再展示状态，避免用陈旧结论误导
                _clear_state(record)
        parent["subsectors"].append(record)
    return result


def sector_history(frame: pd.DataFrame, days: int = HISTORY_DAYS) -> list[dict]:
    """最近 N 个交易日的收盘与当日状态；不含未来数据。"""
    if days <= 0:
        raise ValueError("days 必须为正整数")
    trend = calculate_sector_trend(frame)
    tail = trend.tail(days)
    close = frame["close"].reindex(tail.index)
    rows = []
    for position, (_, row) in enumerate(tail.iterrows()):
        state = row["state"]
        rows.append({
            "date": row["date"].strftime("%Y-%m-%d"),
            "close": round(float(close.iloc[position]), 4),
            "state": None if pd.isna(state) else str(state),
        })
    return rows


def spark_series(frame: pd.DataFrame, days: int = SPARK_DAYS) -> list[float]:
    return [round(float(value), 4) for value in frame["close"].tail(days)]


def state_codes(series: list[dict]) -> list[str | None]:
    return [None if row["state"] is None else STATE_CODES[row["state"]] for row in series]


def _build_horizons(rows: list[dict], horizon: int) -> dict:
    horizon_rows = [row for row in rows if row["horizon"] == horizon]
    if not horizon_rows:
        return {"entries": 0, "samples": 0, "successes": 0, "pending": 0,
                "rate": None, "wilson95": [None, None], "small_sample": True,
                "note": "无已判定样本"}
    row = horizon_rows[0]
    return {
        "entries": row["entries"],
        "samples": row["samples"],
        "successes": row["successes"],
        "pending": row["pending"],
        "rate": row["rate"],
        "wilson95": row["wilson95"],
        "small_sample": row["small_sample"],
        "note": "样本量较小，仅供参考" if row["small_sample"] else None,
    }


def build_subsector_evaluation(
    manifest: list[dict], frames: dict[str, pd.DataFrame],
) -> dict:
    """二级板块独立四态的分状态方向评估，仅作描述性统计。"""
    items = [item for item in manifest if item["level"] == "second"]
    all_events = []
    for item in items:
        frame = frames[item["code"]]
        state = sector_state(frame, "Donchian20_MA60")
        all_events.extend(evaluate_sector_states(
            frame, state, code=item["code"], name=item["name"], method=ALGORITHM_VERSION,
        ))

    summaries = summarize_sector_events(all_events, ["type"])
    segments = []
    for segment_name in ("research", "exploratory_validation"):
        rows = [row for row in summaries if row["segment"] == segment_name]
        states = {}
        for state in ("bull", "warn", "bear", "rise"):
            state_rows = [row for row in rows if row["type"] == state]
            states[state] = {str(horizon): _build_horizons(state_rows, horizon)
                             for horizon in (10, 20)}
        segments.append({"segment": segment_name, "independent": False, "states": states})

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "scope": "second",
        "research_status": "post_hoc_exploratory_not_independent",
        "rate_unit": "fraction",
        "boundary_policy": "strict_segment_end_inclusive",
        "note": "二级板块独立四态为描述性分类，方向胜率不代表交易收益。",
        "stability_note": "研究段与验证段的方向胜率不一致，未观察到稳定优势，仅供描述性参考。",
        "segments": segments,
    }


def build_sector_evaluation(
    manifest: list[dict], frames: dict[str, pd.DataFrame],
) -> dict:
    """构建板块入场计次验证统计，仅覆盖一级板块。"""
    items = [item for item in manifest if item["level"] == "first"]
    all_events = []
    for item in items:
        frame = frames[item["code"]]
        state = sector_state(frame, "Donchian20_MA60")
        events = evaluate_sector_states(
            frame, state, code=item["code"], name=item["name"], method=ALGORITHM_VERSION,
        )
        all_events.extend(events)

    summaries = summarize_sector_events(all_events, [])
    research = [row for row in summaries if row["segment"] == "research"]
    validation = [row for row in summaries if row["segment"] == "exploratory_validation"]

    def build_segment(rows: list[dict], segment_name: str) -> dict:
        result = {"segment": segment_name, "independent": False, "horizons": {}}
        for horizon in (10, 20):
            horizon_rows = [row for row in rows if row["horizon"] == horizon]
            if not horizon_rows:
                result["horizons"][str(horizon)] = None
                continue
            row = horizon_rows[0]
            result["horizons"][str(horizon)] = {
                "entries": row["entries"],
                "samples": row["samples"],
                "successes": row["successes"],
                "pending": row["pending"],
                "rate": row["rate"],
                "wilson95": row["wilson95"],
                "small_sample": row["small_sample"],
                "note": "样本量较小，仅供参考" if row["small_sample"] else None,
            }
        return result

    return {
        "algorithm_version": ALGORITHM_VERSION,
        "research_status": "post_hoc_exploratory_not_independent",
        "rate_unit": "fraction",
        "boundary_policy": "strict_segment_end_inclusive",
        "note": "板块趋势为描述性分类，方向胜率不代表交易收益；空头状态历史胜率低于50%，仅供参考。",
        "segments": [
            build_segment(research, "research"),
            build_segment(validation, "exploratory_validation"),
        ],
    }
