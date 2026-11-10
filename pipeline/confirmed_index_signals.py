"""确认版指数信号与分段评价接口；本模块不写文件。

生产算法由 ACTIVE_ALGORITHM 选择：
- ``legacy``：四因子确认算法（index-mf4-participation-v1.1-confirmed），当前生产口径。
- ``swing``：六方向波段引擎（index-swing6-v2.0），代码与字段保留，改开关即可切回。
评价口径保持"后续出现均线反转"，回撤指标只做并列诊断，不进入胜率分母。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from multifactor_research import confirmed_multifactor_signals
from research_algorithms import RESEARCH_END, RESEARCH_START, ma_reversal_masks, wilson_interval
from swing_signal_engine import ALGORITHM_VERSION as SWING_ALGORITHM_VERSION
from swing_signal_engine import swing_details, swing_signals
from bigwave_bottom_engine import ALGORITHM_VERSION as BIGWAVE_ALGORITHM_VERSION
from bigwave_bottom_engine import bottom_details as bigwave_bottom_details

LEGACY_ALGORITHM_VERSION = "index-mf4-participation-v1.1-confirmed"
# 生产算法开关：bigwave=大波段底部（顶部关闭）｜swing=六因子波段（回滚用）｜legacy=四因子。
ACTIVE_ALGORITHM = "bigwave"
_ALGORITHM_VERSIONS = {
    "bigwave": BIGWAVE_ALGORITHM_VERSION,
    "swing": SWING_ALGORITHM_VERSION,
    "legacy": LEGACY_ALGORITHM_VERSION,
}
ALGORITHM_VERSION = _ALGORITHM_VERSIONS[ACTIVE_ALGORITHM]
RESEARCH_STATUS = "post_hoc_exploratory_not_independent"
SMALL_SAMPLE_NOTE = "样本量较小，仅供参考"
KINDS = ("bottom", "top")
DRAWDOWN_WINDOW = 20
BASELINE_HORIZON = 60
BASELINE_SEED = 20261031


def validate_history(frame: pd.DataFrame) -> None:
    """拒绝异常输入，避免补值或重排静默改变信号；空/短历史由指标自然弃权。"""
    columns = ("open", "high", "low", "close", "volume")
    if not {"date", *columns}.issubset(frame.columns):
        raise ValueError("指数行情缺少 date 或 OHLCV 列")
    dates = frame["date"]
    if not pd.api.types.is_datetime64_any_dtype(dates):
        raise ValueError("date 必须为已解析的交易日期")
    if dates.isna().any() or dates.dt.tz is not None or not dates.eq(dates.dt.normalize()).all():
        raise ValueError("指数只接受无时区的有效日线日期")
    if dates.duplicated().any() or not dates.is_monotonic_increasing or not frame.index.is_unique:
        raise ValueError("交易日期必须唯一且升序，行索引不得重复")
    values = frame[list(columns)]
    if not all(pd.api.types.is_numeric_dtype(values[key]) for key in columns):
        raise ValueError("OHLCV 必须为数值")
    if not np.isfinite(values.to_numpy(dtype=float)).all():
        raise ValueError("OHLCV 不得缺失或含非有限值")
    if (values[["open", "high", "low", "close"]] <= 0).any().any() or (values["volume"] < 0).any():
        raise ValueError("指数价格必须为正，成交量不得为负")
    if ((frame["high"] < values[["open", "close", "low"]].max(axis=1))
            | (frame["low"] > values[["open", "close", "high"]].min(axis=1))).any():
        raise ValueError("OHLC 高低价关系异常")


def confirmed_signals(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                      code: str | None = None) -> pd.Series:
    """按 ACTIVE_ALGORITHM 输出生产信号；当前为 bigwave（大波段底部，顶部关闭）。"""
    validate_history(frame)
    if ACTIVE_ALGORITHM == "bigwave":
        return bigwave_signals(frame, breadth, code)
    if ACTIVE_ALGORITHM == "swing":
        return swing_signals(frame, breadth)
    return confirmed_multifactor_signals(
        frame, 4, trigger_mode="original", participation_mode="and",
        confirmation_window=3,
    )


def bigwave_signals(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                    code: str | None = None) -> pd.Series:
    """大波段底部引擎入口（只产出 bottom）；保留给服务端盘中复用。"""
    validate_history(frame)
    signals = pd.Series("", index=frame.index, dtype="string")
    lookup = {day.strftime("%Y-%m-%d"): position for position, day in enumerate(frame["date"])}
    for record in bigwave_bottom_details(frame, breadth, code):
        signals.iat[lookup[record["date"]]] = "bottom"
    return signals


def swing_confirmed_signals(frame: pd.DataFrame, breadth: pd.DataFrame | None = None) -> pd.Series:
    """波段引擎入口，保留用于新旧对照与一键切回。"""
    validate_history(frame)
    return swing_signals(frame, breadth)


def legacy_confirmed_signals(frame: pd.DataFrame) -> pd.Series:
    """旧算法入口，保留用于新旧对照与回滚。"""
    validate_history(frame)
    return confirmed_multifactor_signals(
        frame, 4, trigger_mode="original", participation_mode="and",
        confirmation_window=3,
    )


def signal_details(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                   code: str | None = None) -> list[dict]:
    validate_history(frame)
    if ACTIVE_ALGORITHM == "bigwave":
        return bigwave_bottom_details(frame, breadth, code)
    return swing_details(frame, breadth)


def signal_records(frame: pd.DataFrame, signals: pd.Series) -> list[dict]:
    """生成不可回写的信号事实；事后评价不混入日期、类型和价格。"""
    return [{"date": frame["date"].iat[position].strftime("%Y-%m-%d"),
             "type": str(signals.iat[position]), "price": float(frame["close"].iat[position])}
            for position in np.flatnonzero(signals.isin(KINDS).to_numpy())]


def detail_records(frame: pd.DataFrame, details: list[dict]) -> list[dict]:
    """对外信号记录：在信号事实上附加等级、得分、因子与确认日志。"""
    fields = ("date", "type", "price", "candidate_date", "grade", "score",
              "core_hits", "factors", "breadth", "confirmation")
    records = []
    for detail in details:
        missing = [key for key in fields if key not in detail]
        if missing:
            raise ValueError(f"信号明细缺少字段：{missing}")
        records.append({key: detail[key] for key in fields})
    return records


def _window(frame: pd.DataFrame, position: int, stop: int) -> pd.DataFrame:
    end = min(position + stop, len(frame) - 1)
    return frame.iloc[position + 1:end + 1]


def drawdown_fields(frame: pd.DataFrame, position: int, price: float, kind: str,
                    resolved: int | None) -> dict:
    """以信号日收盘价为基准的事后回撤与前瞻收益；不参与胜率判定。"""
    last = len(frame) - 1
    end = resolved if resolved is not None else last
    full = frame.iloc[position + 1:end + 1]
    twenty = _window(frame, position, DRAWDOWN_WINDOW)

    def adverse(part: pd.DataFrame) -> float | None:
        if part.empty:
            return None
        if kind == "bottom":
            return float(part["low"].min() / price - 1)
        return float(-(part["high"].max() / price - 1))

    def close_drawdown(part: pd.DataFrame) -> float | None:
        if part.empty:
            return None
        if kind == "bottom":
            return float(part["close"].min() / price - 1)
        return float(-(part["close"].max() / price - 1))

    def forward(days: int) -> float | None:
        target = position + days
        if target > last:
            return None
        return float(frame["close"].iat[target] / price - 1)

    return {
        "max_adverse_excursion": adverse(full),
        "max_adverse_excursion_20": adverse(twenty),
        "max_drawdown_20": close_drawdown(twenty),
        "forward_return_5": forward(5),
        "forward_return_10": forward(10),
        "forward_return_20": forward(20),
    }


def _mean(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None and np.isfinite(value)]
    return float(np.mean(clean)) if clean else None


def summarize_events(events: list[dict]) -> dict:
    result = {}
    for kind in KINDS:
        part = [event for event in events if event["type"] == kind]
        resolved_events = [event for event in part if event.get("status") in {"success", "failure"}]
        successes = sum(event["status"] == "success" for event in part)
        failures = sum(event["status"] == "failure" for event in part)
        resolved = successes + failures
        drawdowns = [event.get("max_adverse_excursion_20") for event in resolved_events]
        returns = [event.get("forward_return_20") for event in resolved_events]
        avg_drawdown = _mean(drawdowns)
        avg_return = _mean(returns)
        result[kind] = {
            "total": len(part), "resolved": resolved, "successes": successes,
            "failures": failures, "pending": len(part) - resolved,
            "rate": successes / resolved if resolved else None,
            "wilson95": wilson_interval(successes, resolved),
            "small_sample": resolved < 30,
            "note": SMALL_SAMPLE_NOTE if resolved < 30 else None,
            "avg_max_adverse_excursion": avg_drawdown,
            "worst_max_adverse_excursion": (
                min(value for value in drawdowns if value is not None)
                if any(value is not None for value in drawdowns) else None
            ),
            "avg_forward_return_20": avg_return,
            "return_drawdown_ratio": (
                avg_return / abs(avg_drawdown)
                if avg_return is not None and avg_drawdown not in (None, 0) else None
            ),
        }
    return result


def _bounded_success(mask: pd.Series, position: int, horizon: int, limit: int) -> bool:
    stop = min(position + horizon, limit)
    if stop <= position:
        return False
    return bool(mask.iloc[position + 1:stop + 1].any())


def baseline_block(mask: pd.Series, positions: list[int], start: int, end: int) -> dict:
    """同区间随机取等量交易日的基准成功率（60 日窗口），与信号同窗口成功率并列。"""
    available = [position for position in range(start + 1, end + 1)]
    if not available or not positions:
        return {"horizon_days": BASELINE_HORIZON, "samples": 0, "successes": 0,
                "rate": None, "signal_rate_same_horizon": None, "seed": BASELINE_SEED}
    sample_size = min(len(positions), len(available))
    generator = np.random.default_rng(BASELINE_SEED)
    sample = generator.choice(available, size=sample_size, replace=False)
    baseline_successes = sum(
        _bounded_success(mask, int(position), BASELINE_HORIZON, end) for position in sample
    )
    signal_successes = sum(
        _bounded_success(mask, position, BASELINE_HORIZON, end) for position in positions
    )
    return {
        "horizon_days": BASELINE_HORIZON,
        "samples": int(sample_size),
        "successes": int(baseline_successes),
        "rate": float(baseline_successes / sample_size) if sample_size else None,
        "signal_rate_same_horizon": float(signal_successes / len(positions)) if positions else None,
        "seed": BASELINE_SEED,
    }


def evaluate_signals(frame: pd.DataFrame, signals: pd.Series) -> dict:
    """仅作事后标签；研究段严格截止2024年末，数据末日信号也计入未决。"""
    validate_history(frame)
    if not signals.index.equals(frame.index) or not signals.isin(("", *KINDS)).all():
        raise ValueError("信号必须与行情逐行对齐，且只能为空、bottom、top")
    bull, bear = ma_reversal_masks(frame)
    dates = frame["date"]
    positions = np.flatnonzero(signals.isin(KINDS).to_numpy())
    by_kind = {kind: np.flatnonzero(signals.eq(kind).to_numpy()) for kind in KINDS}
    segments = []
    for key, start, end in (
        ("research", RESEARCH_START, RESEARCH_END),
        ("exploratory_validation", RESEARCH_END + pd.Timedelta(days=1),
         dates.iat[-1] if len(frame) else RESEARCH_END + pd.Timedelta(days=1)),
    ):
        available = np.flatnonzero(((dates >= start) & (dates <= end)).to_numpy())
        events = []
        if len(available):
            last = int(available[-1])
            for position in positions:
                if dates.iat[position] < start or position > last:
                    continue
                kind = str(signals.iat[position])
                opposite = by_kind["top" if kind == "bottom" else "bottom"]
                later = opposite[opposite > position]
                next_opposite = int(later[0]) if len(later) else None
                stop = min(last + 1, next_opposite) if next_opposite is not None else last + 1
                mask = bull if kind == "bottom" else bear
                matches = np.flatnonzero(mask.iloc[position + 1:stop].to_numpy())
                resolved_position = None
                if len(matches):
                    resolved_position = int(position + 1 + matches[0])
                    status, reason = "success", "ma_reversal"
                elif next_opposite is not None and next_opposite <= last:
                    resolved_position = next_opposite
                    status, reason = "failure", "opposite_signal"
                else:
                    status = "pending"
                    reason = "segment_boundary" if key == "research" and dates.iat[-1] > end else "data_end"
                price = float(frame["close"].iat[position])
                event = {
                    "date": dates.iat[position].strftime("%Y-%m-%d"), "type": kind,
                    "price": price, "status": status,
                    "confirmation_date": dates.iat[resolved_position].strftime("%Y-%m-%d")
                    if resolved_position is not None else None,
                    "confirmation_days": int(resolved_position - position)
                    if resolved_position is not None else None,
                    "reason": reason,
                    "observed_trading_days": int((resolved_position if resolved_position is not None else last) - position),
                }
                event.update(drawdown_fields(frame, position, price, kind, resolved_position))
                events.append(event)
        summary = summarize_events(events)
        segment = {
            "segment": key, "independent": False,
            "start": dates.iat[int(available[0])].strftime("%Y-%m-%d") if len(available) else None,
            "end": dates.iat[int(available[-1])].strftime("%Y-%m-%d") if len(available) else None,
            "summary": summary, "events": events, "baseline": {},
        }
        if len(available):
            first, last = int(available[0]), int(available[-1])
            for kind in KINDS:
                mask = bull if kind == "bottom" else bear
                inside = [int(position) for position in by_kind[kind] if first <= position <= last]
                segment["baseline"][kind] = baseline_block(mask, inside, first, last)
        segments.append(segment)
    return {
        "algorithm_version": ALGORITHM_VERSION, "research_status": RESEARCH_STATUS,
        "rate_unit": "fraction", "boundary_policy": "strict_segment_end_inclusive",
        "note": "既有数据上的探索性结果，不是独立未见验证或交易收益率；未决不进入胜率分母。",
        "confirmation_date_note": "成功时为首次均线反转日；失败时为反向信号日；未决为空。",
        "drawdown_note": "回撤以信号日收盘价为基准，底部取最低价、顶部取最高价；不参与胜率计算。",
        "segments": segments,
    }


def legacy_accuracy(evaluation: dict) -> dict:
    """兼容旧页面的百分数和字段形状；无已判定样本时的0仅为兼容占位。"""
    available = [part for part in evaluation["segments"] if part["start"] is not None]
    result = {
        "start": min((part["start"] for part in available), default=RESEARCH_START.strftime("%Y-%m-%d")),
        "end": max((part["end"] for part in available), default=RESEARCH_START.strftime("%Y-%m-%d")),
    }
    events = [event for part in available for event in part["events"]]
    for kind, summary in summarize_events(events).items():
        result[kind] = {
            "triggers": summary["total"], "successes": summary["successes"],
            "rate": 100 * summary["rate"] if summary["rate"] is not None else 0.0,
            "avg_max_adverse_excursion": summary["avg_max_adverse_excursion"],
            "worst_max_adverse_excursion": summary["worst_max_adverse_excursion"],
            "avg_forward_return_20": summary["avg_forward_return_20"],
            "return_drawdown_ratio": summary["return_drawdown_ratio"],
        }
    return result


def build_index_result(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                       code: str | None = None) -> dict:
    if ACTIVE_ALGORITHM == "bigwave":
        details = signal_details(frame, breadth, code)
        signals = pd.Series("", index=frame.index, dtype="string")
        lookup = {row["date"]: row["type"] for row in details}
        for position, day in enumerate(frame["date"]):
            label = day.strftime("%Y-%m-%d")
            if label in lookup:
                signals.iat[position] = lookup[label]
        records = detail_records(frame, details)
    elif ACTIVE_ALGORITHM == "swing":
        details = signal_details(frame, breadth)
        signals = pd.Series("", index=frame.index, dtype="string")
        lookup = {row["date"]: row["type"] for row in details}
        for position, day in enumerate(frame["date"]):
            label = day.strftime("%Y-%m-%d")
            if label in lookup:
                signals.iat[position] = lookup[label]
        records = detail_records(frame, details)
    else:
        signals = confirmed_signals(frame)
        records = signal_records(frame, signals)
    evaluation = evaluate_signals(frame, signals)
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "status": "insufficient_history" if len(frame) < 60 else "calculated",
        "signals": records, "evaluation": evaluation,
        "accuracy": legacy_accuracy(evaluation),
    }
