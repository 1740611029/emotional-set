"""固定持有期准确率矩阵（研究阶段专用，纯计算）。

口径（见 index-precision-pivot-signals 的 design D3/D4/D5 与 spec delta）：
- 主口径 = 5/10/20/30 交易日方向正确 + 20 日盘中极值最大不利偏移硬约束；
- |max_adverse_excursion_20| > 5% 一律判 failure，恰好 5% 不判失败；
- 双口径并列：方向正确率 / 有效幅度率（|收益| 达到该指数该持有期的冻结线）；
- 触发次数恒等于信号条数；未决显式列示但不进分母；
- 每格附 Wilson 95% 区间、基准对照与回撤聚合。

样本量门槛已按用户 2026-11-09 指令放宽：不再以样本数禁止给出结论，
但样本量、统计区间与 Wilson 区间一律如实披露（R4 保持不变）。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from precision_config import (
    BASELINE_SEED, HOLD_PERIODS, KINDS, MAE_LIMIT, MAE_WINDOW, SEGMENTS,
    amplitude_line,
)
from research_algorithms import wilson_interval

SMALL_SAMPLE_THRESHOLD = 30
EPS = 1e-12


def date_positions(frame: pd.DataFrame) -> dict[str, int]:
    return {day.strftime("%Y-%m-%d"): position
            for position, day in enumerate(pd.to_datetime(frame["date"]))}


def adverse_excursion_20(frame: pd.DataFrame, position: int, price: float,
                         kind: str) -> float | None:
    """20 个交易日内最大不利偏移，固定用盘中极值，带符号（负值表示不利）。

    底部取最低价相对信号价的跌幅；顶部取最高价相对信号价的涨幅并取负。
    """
    part = frame.iloc[position + 1:position + 1 + MAE_WINDOW]
    if part.empty:
        return None
    if kind == "bottom":
        return float(part["low"].min() / price - 1)
    if kind == "top":
        return float(-(part["high"].max() / price - 1))
    raise ValueError(f"未知信号方向：{kind}")


def forward_return(frame: pd.DataFrame, position: int, price: float,
                   horizon: int) -> float | None:
    target = position + horizon
    if target >= len(frame):
        return None
    return float(frame["close"].iat[target] / price - 1)


def mae_exceeded(value: float | None) -> bool:
    """严格大于 5% 才算超限；恰好 5%（含浮点误差）不算。"""
    return value is not None and abs(value) > MAE_LIMIT + EPS


def evaluate_event(frame: pd.DataFrame, position: int, price: float, kind: str,
                   horizon: int, line: float) -> dict:
    """单条信号在某个持有期格下的判定；两口径共用同一浮亏硬约束。"""
    mae = adverse_excursion_20(frame, position, price, kind)
    ret = forward_return(frame, position, price, horizon)
    exceeded = mae_exceeded(mae)
    record = {
        "date": pd.to_datetime(frame["date"]).iat[position].strftime("%Y-%m-%d"),
        "type": kind,
        "price": float(price),
        "horizon_days": int(horizon),
        "forward_return": ret,
        "max_adverse_excursion_20": mae,
        "mae_limit_exceeded": bool(exceeded),
        "amplitude_line": float(line),
        "direction_success": False,
        "effective_amplitude_success": False,
        "status": "pending",
        "reason": None,
    }
    if exceeded:
        # 浮亏超限：无论期末收益方向如何，全部持有期格一律失败。
        record["status"] = "failure"
        record["reason"] = "mae_limit_exceeded"
        return record
    if ret is None:
        record["status"] = "pending"
        record["reason"] = "holding_period_unfinished"
        return record
    direction = ret > 0 if kind == "bottom" else ret < 0
    effective = direction and (abs(ret) + EPS >= line)
    record["direction_success"] = bool(direction)
    record["effective_amplitude_success"] = bool(effective)
    record["status"] = "success" if direction else "failure"
    record["reason"] = "direction_correct" if direction else "direction_wrong"
    return record


def _mean(values: list[float | None]) -> float | None:
    clean = [value for value in values if value is not None and np.isfinite(value)]
    return float(np.mean(clean)) if clean else None


def drawdown_aggregate(events: list[dict], triggers: int) -> dict:
    """回撤聚合：无已判定样本时全部为 None，MUST NOT 用 0 代替。"""
    resolved_events = [event for event in events if event["status"] in {"success", "failure"}]
    if not resolved_events:
        return {
            "avg_max_adverse_excursion": None,
            "worst_max_adverse_excursion": None,
            "avg_forward_return_20": None,
            "avg_forward_return_30": None,
            "return_drawdown_ratio": None,
            "mae_exceeded_failures": 0,
            "mae_exceeded_failure_share": None if triggers == 0 else 0.0,
        }
    maes = [event["max_adverse_excursion_20"] for event in resolved_events]
    avg_mae = _mean(maes)
    excess_failures = sum(event["mae_limit_exceeded"] for event in events)
    return {
        "avg_max_adverse_excursion": avg_mae,
        "worst_max_adverse_excursion": (
            min(value for value in maes if value is not None)
            if any(value is not None for value in maes) else None
        ),
        "avg_forward_return_20": _mean([
            event["forward_return"] for event in resolved_events if event["horizon_days"] == 20
        ]),
        "avg_forward_return_30": _mean([
            event["forward_return"] for event in resolved_events if event["horizon_days"] == 30
        ]),
        "return_drawdown_ratio": None,
        "mae_exceeded_failures": int(excess_failures),
        "mae_exceeded_failure_share": (
            float(excess_failures / triggers) if triggers else None
        ),
    }


def baseline_cell(frame: pd.DataFrame, available: list[int], sample_size: int,
                  horizon: int, kind: str, line: float, seed: int) -> dict:
    """同期随机取相同数量日期，按同一持有期、同一浮亏约束计算基准成功率。"""
    result = {
        "baseline_samples": 0, "baseline_successes": 0, "baseline_rate": None,
        "signal_rate": None, "difference": None, "seed": int(seed),
        "horizon_days": int(horizon),
    }
    if sample_size <= 0 or not available:
        return result
    size = min(sample_size, len(available))
    generator = np.random.default_rng(seed)
    sample = generator.choice(np.array(available, dtype=int), size=size, replace=False)
    successes = 0
    for raw in sample:
        position = int(raw)
        price = float(frame["close"].iat[position])
        event = evaluate_event(frame, position, price, kind, horizon, line)
        successes += int(event["status"] == "success")
    result.update({
        "baseline_samples": int(size),
        "baseline_successes": int(successes),
        "baseline_rate": float(successes / size),
    })
    return result


def _rate(successes: int, resolved: int) -> float | None:
    return float(successes / resolved) if resolved else None


def build_cell(frame: pd.DataFrame, signals: list[dict], *, segment: str,
               interval: dict, horizon: int, kind: str, line: float,
               frozen: dict, seed: int) -> dict:
    """构建单个矩阵格（段 × 持有期 × 方向）。"""
    lookup = date_positions(frame)
    direction_signals = [record for record in signals if record["type"] == kind]
    triggers = len(direction_signals)
    events: list[dict] = []
    for record in direction_signals:
        position = lookup.get(record["date"])
        if position is None:
            raise ValueError(f"信号日期不在行情中：{record['date']}")
        price = float(record.get("price", frame["close"].iat[position]))
        events.append(evaluate_event(frame, position, price, kind, horizon, line))

    successes = sum(event["status"] == "success" for event in events)
    failures = sum(event["status"] == "failure" for event in events)
    pending = sum(event["status"] == "pending" for event in events)
    resolved = successes + failures
    amp_successes = sum(event["effective_amplitude_success"] for event in events)
    direction_rate = _rate(successes, resolved)
    amp_rate = _rate(amp_successes, resolved)

    # 基准可用日期：段内、且持有期与浮亏窗口均已走完的交易日。
    dates = pd.to_datetime(frame["date"])
    start, end = pd.Timestamp(interval["start"]), pd.Timestamp(interval["end"])
    needed = max(horizon, MAE_WINDOW)
    available = [position for position, day in enumerate(dates)
                 if start <= day <= end and position + needed < len(frame)]
    baseline = baseline_cell(frame, available, triggers, horizon, kind, line, seed)
    baseline["signal_rate"] = direction_rate
    baseline["difference"] = (
        None if direction_rate is None or baseline["baseline_rate"] is None
        else float(direction_rate - baseline["baseline_rate"])
    )

    # 回撤聚合需要跨持有期的收益，单独按 20/30 日重算均值。
    aggregate = drawdown_aggregate(events, triggers)
    r20 = [forward_return(frame, lookup[event["date"]], event["price"], 20)
           for event in events if event["status"] in {"success", "failure"}]
    r30 = [forward_return(frame, lookup[event["date"]], event["price"], 30)
           for event in events if event["status"] in {"success", "failure"}]
    aggregate["avg_forward_return_20"] = _mean(r20)
    aggregate["avg_forward_return_30"] = _mean(r30)
    if aggregate["avg_forward_return_20"] is not None and aggregate["avg_max_adverse_excursion"] not in (None, 0):
        aggregate["return_drawdown_ratio"] = float(
            aggregate["avg_forward_return_20"] / abs(aggregate["avg_max_adverse_excursion"])
        )

    return {
        "segment": segment,
        "horizon_days": int(horizon),
        "direction": kind,
        "statistical_interval": {"start": interval["start"], "end": interval["end"]},
        "triggers": int(triggers),
        "resolved": int(resolved),
        "n_resolved": int(resolved),
        "successes": int(successes),
        "failures": int(failures),
        "pending": int(pending),
        "direction_correct_rate": direction_rate,
        "direction_correct_wilson95": wilson_interval(successes, resolved),
        "effective_amplitude_successes": int(amp_successes),
        "effective_amplitude_rate": amp_rate,
        "effective_amplitude_wilson95": wilson_interval(amp_successes, resolved),
        "effective_amplitude_line": float(line),
        "small_sample": bool(resolved < SMALL_SAMPLE_THRESHOLD),
        "baseline": baseline,
        "drawdown": aggregate,
        "events": events,
    }


def _segment_signals(signals: list[dict], interval: dict) -> list[dict]:
    start, end = interval["start"], interval["end"]
    return [record for record in signals if start <= record["date"] <= end]


def _seed_for(segment: str, horizon: int, kind: str) -> int:
    segment_offset = 0 if segment == "train" else 100000
    return BASELINE_SEED + segment_offset + horizon * 10 + (0 if kind == "bottom" else 1)


def build_matrix(frame: pd.DataFrame, signals: list[dict], *, segments: dict,
                 frozen: dict, code: str) -> dict:
    """2 段 × 4 持有期 × 2 方向完整矩阵。训练段用 effective 区间排除隔离期。"""
    intervals = {
        "train": {"start": segments["train"]["effective_start"],
                  "end": segments["train"]["effective_end"]},
        "validation": {"start": segments["validation"]["start"],
                       "end": segments["validation"]["end"]},
    }
    matrix: dict[str, dict] = {}
    for segment in SEGMENTS:
        interval = intervals[segment]
        in_segment = _segment_signals(signals, interval)
        matrix[segment] = {"statistical_interval": interval, "triggers": len(in_segment),
                           "horizons": {}}
        for horizon in HOLD_PERIODS:
            line = amplitude_line(frozen["effective_amplitude"], code, horizon)
            matrix[segment]["horizons"][str(horizon)] = {
                kind: build_cell(frame, in_segment, segment=segment, interval=interval,
                                 horizon=horizon, kind=kind, line=line, frozen=frozen,
                                 seed=_seed_for(segment, horizon, kind))
                for kind in KINDS
            }
    return matrix


def trigger_conservation(matrix: dict, signals: list[dict], segments: dict) -> dict:
    """触发次数守恒校验：每格 triggers 恒等于该段该方向实际信号条数。"""
    intervals = {
        "train": (segments["train"]["effective_start"], segments["train"]["effective_end"]),
        "validation": (segments["validation"]["start"], segments["validation"]["end"]),
    }
    problems = []
    for segment, (start, end) in intervals.items():
        in_segment = [record for record in signals if start <= record["date"] <= end]
        for horizon in HOLD_PERIODS:
            for kind in KINDS:
                cell = matrix[segment]["horizons"][str(horizon)][kind]
                expected = sum(record["type"] == kind for record in in_segment)
                if cell["triggers"] != expected:
                    problems.append({
                        "segment": segment, "horizon": horizon, "direction": kind,
                        "cell_triggers": cell["triggers"], "expected": expected,
                    })
                if cell["resolved"] + cell["pending"] != cell["triggers"]:
                    problems.append({
                        "segment": segment, "horizon": horizon, "direction": kind,
                        "reason": "resolved + pending != triggers",
                    })
    return {"passed": not problems, "problems": problems,
            "rule": "triggers = 段内该方向实际信号条数；resolved + pending = triggers"}
