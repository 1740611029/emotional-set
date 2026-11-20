"""统一事后主波段收益与信号延迟评价；仅用于离线研究，不生成实时信号。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from bigwave_bottom_engine import FROZEN, _zigzag_pivots, amplitude_for, group_of
from top_bottom_closed_loop import path_metrics


def build_oracle_waves(frame: pd.DataFrame, code: str) -> tuple[list[dict], dict]:
    """用冻结的生产 ZigZag 参数建立统一事后标尺，不使用其确认日作实时信号。"""
    params = FROZEN[group_of(code)]
    amplitude = amplitude_for(code, frame)
    pivots = _zigzag_pivots(frame, amplitude, int(params["min_gap"]))
    waves = []
    for index, pivot in enumerate(pivots):
        following = pivots[index + 1] if index + 1 < len(pivots) else None
        if following is not None and following["type"] == pivot["type"]:
            raise ValueError("事后主波段标尺的顶底必须交替")
        waves.append({
            "kind": pivot["type"],
            "start": int(pivot["index"]),
            "end": int(following["index"]) if following is not None else None,
        })
    metadata = {
        "amplitude": amplitude,
        "min_gap": int(params["min_gap"]),
        "pivot_count": len(pivots),
        "wave_count": len(waves),
        "price_field_for_pivots": "close",
        "price_fields_for_ideal_return": "bottom low to subsequent highest high; top high to subsequent lowest low",
    }
    return waves, metadata


def match_oracle_wave(waves: list[dict], position: int, kind: str) -> dict | None:
    """上涨/下跌段内匹配已开始的波段；反向段内匹配下一同向极值，允许负延迟。"""
    same_kind = [wave for wave in waves if wave["kind"] == kind]
    previous = [wave for wave in same_kind if wave["start"] <= position]
    if previous:
        wave = previous[-1]
        if wave["end"] is None or position <= wave["end"]:
            return wave
    following = [wave for wave in same_kind if wave["start"] > position]
    if following:
        return following[0]
    return previous[-1] if previous else None


def ideal_wave_metrics(frame: pd.DataFrame, wave: dict) -> dict:
    start = wave["start"]
    end = wave["end"]
    kind = wave["kind"]
    if kind == "bottom":
        origin = float(frame["low"].iat[start])
        favorable_position = start + int(np.argmax(frame["high"].iloc[start:end + 1].to_numpy(dtype=float)))
        favorable = float(frame["high"].iat[favorable_position])
        ideal_return = favorable / origin - 1
    else:
        origin = float(frame["high"].iat[start])
        favorable_position = start + int(np.argmin(frame["low"].iloc[start:end + 1].to_numpy(dtype=float)))
        favorable = float(frame["low"].iat[favorable_position])
        ideal_return = 1 - favorable / origin
    return {
        "ideal_return": float(ideal_return),
        "ideal_extreme_date": frame["date"].iat[favorable_position].strftime("%Y-%m-%d"),
        "ideal_extreme_price": favorable,
    }


def oracle_metrics(frame: pd.DataFrame, position: int, wave: dict) -> dict:
    start = wave["start"]
    end = wave["end"]
    kind = wave["kind"]
    entry = float(frame["close"].iat[position])
    if kind == "bottom":
        origin = float(frame["low"].iat[start])
        lag_price_move = entry / origin - 1
    else:
        origin = float(frame["high"].iat[start])
        lag_price_move = 1 - entry / origin
    output = {
        "oracle_status": "closed",
        "oracle_wave_start_date": frame["date"].iat[start].strftime("%Y-%m-%d"),
        "oracle_wave_end_date": frame["date"].iat[end].strftime("%Y-%m-%d"),
        "oracle_delay_days": int(position - start),
        "oracle_lag_price_move": float(lag_price_move),
        "oracle_holding_days": int(end - position),
    }
    output.update(ideal_wave_metrics(frame, wave))
    output.update(path_metrics(frame, position, end, kind))
    output["oracle_exit_return"] = output.pop("outcome_return")
    output["oracle_mae"] = output.pop("mae")
    output["oracle_mfe"] = output.pop("mfe")
    output.pop("mfe_mae_ratio")
    output["oracle_capture_ratio"] = (
        output["oracle_exit_return"] / output["ideal_return"]
        if output["ideal_return"] > 0 else None
    )
    return output


def evaluate_oracle_segment(frame: pd.DataFrame, events: list[dict], waves: list[dict],
                            kind: str, segment: str, start: int, end: int) -> list[dict]:
    lookup = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(frame["date"])}
    output = []
    for event in sorted(events, key=lambda item: item["date"]):
        if event["type"] != kind or event["date"] not in lookup:
            continue
        position = lookup[event["date"]]
        if not start <= position <= end:
            continue
        record = {
            key: value for key, value in event.items() if key != "confirmation"
        }
        record["segment"] = segment
        wave = match_oracle_wave(waves, position, kind)
        if wave is None:
            record["oracle_status"] = "unmatched"
            output.append(record)
            continue
        record.update({
            "oracle_wave_start_date": frame["date"].iat[wave["start"]].strftime("%Y-%m-%d"),
            "oracle_delay_days": int(position - wave["start"]),
        })
        if wave["end"] is not None and position <= wave["end"] <= end:
            record.update(oracle_metrics(frame, position, wave))
        else:
            record["oracle_status"] = (
                "after_wave_end" if wave["end"] is not None and position > wave["end"] else "pending"
            )
            if wave["end"] is not None:
                record["oracle_wave_end_date"] = frame["date"].iat[wave["end"]].strftime("%Y-%m-%d")
        output.append(record)
    return output


def mean(values: list[float]) -> float | None:
    return float(np.mean(values)) if values else None


def median(values: list[float]) -> float | None:
    return float(np.median(values)) if values else None


def summarize_oracle(events: list[dict]) -> dict:
    closed = [event for event in events if event.get("oracle_status") == "closed"]

    def values(key: str) -> list[float]:
        return [event[key] for event in closed if event.get(key) is not None]

    profitable = sum(event["oracle_exit_return"] > 0 for event in closed)
    delays = values("oracle_delay_days")
    early_delays = [delay for delay in delays if delay < 0]
    late_delays = [delay for delay in delays if delay > 0]
    return {
        "total": len(events),
        "closed": len(closed),
        "pending": sum(event.get("oracle_status") == "pending" for event in events),
        "after_wave_end": sum(event.get("oracle_status") == "after_wave_end" for event in events),
        "unmatched": sum(event.get("oracle_status") == "unmatched" for event in events),
        "active_years": len({event["date"][:4] for event in events}),
        "profitable_exits": profitable,
        "profitable_exit_rate": profitable / len(closed) if closed else None,
        "avg_exit_return": mean(values("oracle_exit_return")),
        "median_exit_return": median(values("oracle_exit_return")),
        "avg_ideal_return": mean(values("ideal_return")),
        "avg_remaining_mfe": mean(values("oracle_mfe")),
        "avg_mae": mean(values("oracle_mae")),
        "avg_capture_ratio": mean(values("oracle_capture_ratio")),
        "avg_delay_days": mean(delays),
        "median_delay_days": median(delays),
        "avg_abs_delay_days": mean([abs(delay) for delay in delays]),
        "avg_early_days": mean(early_delays),
        "avg_late_delay_days": mean(late_delays),
        "avg_lag_price_move": mean(values("oracle_lag_price_move")),
        "early_signals": len(early_delays),
        "late_signals": len(late_delays),
        "early_signal_rate": len(early_delays) / len(delays) if delays else None,
        "avg_holding_days": mean(values("oracle_holding_days")),
    }


def evaluate_oracle(frame: pd.DataFrame, events: dict[str, list[dict]], waves: list[dict],
                    bounds: dict[str, tuple[int, int]]) -> dict:
    output = {kind: {} for kind in ("bottom", "top")}
    for segment, (start, end) in bounds.items():
        for kind in ("bottom", "top"):
            output[kind][segment] = evaluate_oracle_segment(
                frame, events.get(kind, []), waves, kind, segment, start, end,
            )
    return {
        "events": output,
        "summary": {
            kind: {segment: summarize_oracle(rows) for segment, rows in segments.items()}
            for kind, segments in output.items()
        },
    }
