"""无固定期限、无固定收益目标的实际顶底闭环评价。"""
from __future__ import annotations

from bisect import bisect_right

import numpy as np
import pandas as pd

from research_algorithms import wilson_interval
from run_top_bottom_optimization import segment_bounds

RISK_LINES = {
    "sh000001": 0.045,
    "sz399001": 0.060,
    "sz399006": 0.080,
    "sh000688": 0.080,
    "sh000300": 0.050,
    "sh000905": 0.055,
    "sh000852": 0.065,
}
TRAIN_ANNUALIZED_VOLATILITY = {
    "sh000001": 0.16124856,
    "sz399001": 0.22261399,
    "sz399006": 0.29325295,
    "sh000688": 0.29013572,
    "sh000300": 0.18645889,
    "sh000905": 0.20644088,
    "sh000852": 0.24056803,
}


def prepare_direction(frame: pd.DataFrame, events: list[dict], kind: str,
                      risk_line: float) -> list[dict]:
    lookup = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(frame["date"])}
    close = frame["close"].to_numpy(dtype=float)
    extreme = frame["low" if kind == "bottom" else "high"].to_numpy(dtype=float)
    prepared = []
    for event in sorted(events, key=lambda item: item["date"]):
        if event["type"] != kind or event["date"] not in lookup:
            continue
        position = lookup[event["date"]]
        entry = close[position]
        changes = extreme[position + 1:] / entry - 1
        adverse = changes if kind == "bottom" else -changes
        breaches = np.flatnonzero(adverse < -risk_line)
        risk_position = position + 1 + int(breaches[0]) if len(breaches) else None
        prepared.append({**event, "_position": position, "_risk_position": risk_position})
    return prepared


def outcome(position: int, risk_position: int | None, opposite_positions: list[int],
            segment_end: int) -> tuple[str, int | None, bool]:
    opposite_index = bisect_right(opposite_positions, position)
    opposite = opposite_positions[opposite_index] if opposite_index < len(opposite_positions) else None
    if opposite is not None and opposite > segment_end:
        opposite = None
    if risk_position is not None and risk_position > segment_end:
        risk_position = None
    if opposite is not None and (risk_position is None or opposite < risk_position):
        return "closed_success", opposite, False
    if risk_position is not None:
        return "risk_failure", risk_position, opposite == risk_position
    return "pending", None, False


def path_metrics(frame: pd.DataFrame, position: int, end: int, kind: str) -> dict:
    entry = float(frame["close"].iat[position])
    part = frame.iloc[position + 1:end + 1]
    if part.empty:
        return {"mae": 0.0, "mfe": 0.0, "mfe_mae_ratio": None, "outcome_return": 0.0}
    if kind == "bottom":
        mae = float(part["low"].min() / entry - 1)
        mfe = float(part["high"].max() / entry - 1)
    else:
        mae = float(-(part["high"].max() / entry - 1))
        mfe = float(-(part["low"].min() / entry - 1))
    close_return = float(frame["close"].iat[end] / entry - 1)
    return {
        "mae": mae,
        "mfe": mfe,
        "mfe_mae_ratio": mfe / abs(mae) if mae < 0 else None,
        "outcome_return": close_return if kind == "bottom" else -close_return,
    }


def evaluate_segment(frame: pd.DataFrame, prepared: list[dict], opposite_positions: list[int],
                     kind: str, segment: str, start: int, end: int,
                     details: bool = True) -> list[dict]:
    output = []
    for event in prepared:
        position = event["_position"]
        if not start <= position <= end:
            continue
        status, outcome_position, ambiguous = outcome(
            position, event["_risk_position"], opposite_positions, end,
        )
        path_end = outcome_position if outcome_position is not None else end
        record = {
            key: value for key, value in event.items() if not key.startswith("_")
        }
        record.update({
            "status": status,
            "segment": segment,
            "outcome_date": frame["date"].iat[outcome_position].strftime("%Y-%m-%d") if outcome_position is not None else None,
            "holding_days": outcome_position - position if outcome_position is not None else None,
            "ambiguous_same_day": ambiguous,
        })
        if details:
            metrics = path_metrics(frame, position, path_end, kind)
            record.update(metrics)
            record["exit_return"] = metrics["outcome_return"] if status == "closed_success" else None
            record["mark_to_market_return"] = metrics["outcome_return"] if status == "pending" else None
        output.append(record)
    return output


def mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def summarize_closed_loop(events: list[dict]) -> dict:
    successes = sum(event["status"] == "closed_success" for event in events)
    failures = sum(event["status"] == "risk_failure" for event in events)
    pending = sum(event["status"] == "pending" for event in events)
    resolved = successes + failures
    interval = wilson_interval(successes, resolved)
    closed = [event for event in events if event["status"] == "closed_success"]
    profitable = sum(event.get("exit_return") is not None and event["exit_return"] > 0 for event in closed)
    losing = sum(event.get("exit_return") is not None and event["exit_return"] < 0 for event in closed)

    def values(key: str, rows: list[dict] | None = None) -> list[float]:
        source = events if rows is None else rows
        return [event[key] for event in source if event.get(key) is not None]

    maes = values("mae")
    mfes = values("mfe")
    holdings = values("holding_days")
    return {
        "total": len(events),
        "resolved": resolved,
        "successes": successes,
        "closed_successes": successes,
        "failures": failures,
        "risk_failures": failures,
        "pending": pending,
        "rate": successes / resolved if resolved else None,
        "wilson95": interval,
        "wilson_low": interval[0],
        "wilson_high": interval[1],
        "small_sample": resolved < 30,
        "active_years": len({event["date"][:4] for event in events}),
        "profitable_closed_exits": profitable,
        "loss_making_closed_exits": losing,
        "profitable_exit_rate": profitable / successes if successes else None,
        "avg_closed_exit_return": mean(values("exit_return", closed)),
        "avg_outcome_return": mean(values("outcome_return", [event for event in events if event["status"] != "pending"])),
        "avg_mae": mean(maes),
        "max_mae": min(maes) if maes else None,
        "avg_mfe": mean(mfes),
        "max_mfe": max(mfes) if mfes else None,
        "avg_mfe_mae_ratio": mean(values("mfe_mae_ratio")),
        "avg_holding_days": mean(holdings),
        "max_holding_days": max(holdings) if holdings else None,
        "ambiguous_same_day": sum(bool(event.get("ambiguous_same_day")) for event in events),
    }


def evaluate_closed_loop(frame: pd.DataFrame, events: dict[str, list[dict]],
                         risk_line: float, details: bool = True) -> dict:
    prepared = {
        kind: prepare_direction(frame, events.get(kind, []), kind, risk_line)
        for kind in ("bottom", "top")
    }
    positions = {kind: [event["_position"] for event in rows] for kind, rows in prepared.items()}
    output = {kind: {"train": [], "validation": []} for kind in ("bottom", "top")}
    for segment, (start, end) in segment_bounds(frame).items():
        for kind in ("bottom", "top"):
            opposite = "top" if kind == "bottom" else "bottom"
            output[kind][segment] = evaluate_segment(
                frame, prepared[kind], positions[opposite], kind, segment, start, end, details,
            )
    return {
        "events": output,
        "summary": {
            kind: {segment: summarize_closed_loop(rows) for segment, rows in segments.items()}
            for kind, segments in output.items()
        },
    }
