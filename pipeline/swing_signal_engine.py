"""波段顶底信号引擎：候选闸门 → 波段交替 → 三层确认 → 分级。

信号日期记为**确认日**，候选日只出现在明细与日志中，不对外展示为信号。
所有判定只使用确认日及之前的数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_algorithms import rsi
from swing_indicators import CORE_DIRECTIONS, CORE_EXTREME, factor_table

ALGORITHM_VERSION = "index-swing6-v2.0"
KINDS = ("bottom", "top")
LAYER_KEYS = ("price", "momentum", "volume")

DEFAULT_PARAMS = {
    "score_threshold": 0.52,
    "core_extreme": 0.60,
    "confirm_window": 3,
    "min_swing_days": 8,
    "min_swing_amplitude": 0.03,
    "grade_quantile": 0.5,
    "volume_ratio": 1.2,
    "breadth_improve": 0.05,
}


def candidate_masks(factors: pd.DataFrame, params: dict | None = None) -> dict[str, pd.Series]:
    """候选 = 合成分数达标 + 核心闸门 + 未被趋势否决。"""
    config = {**DEFAULT_PARAMS, **(params or {})}
    masks = {}
    for kind in KINDS:
        core_columns = [f"{name}_{kind}" for name in CORE_DIRECTIONS]
        hits = sum((factors[column] >= config["core_extreme"]).astype(int) for column in core_columns)
        available = factors[core_columns].notna().sum(axis=1)
        factors[f"core_hits_{kind}"] = hits
        score = factors[f"score_{kind}"]
        masks[kind] = (
            (score >= config["score_threshold"])
            & (hits >= 2) & (available >= 2)
            & ~factors[f"veto_{kind}"].fillna(False)
            & score.notna()
        )
    return masks


def _confirmation_context(frame: pd.DataFrame) -> dict:
    close = frame["close"]
    return {
        "rsi": rsi(close),
        "ma5": close.rolling(5).mean(),
        "volume_ma5": frame["volume"].rolling(5).mean(),
        "open": frame["open"],
        "close": close,
    }


def layer_state(frame: pd.DataFrame, context: dict, breadth_delta5: pd.Series,
                position: int, kind: str, candidate: int, params: dict | None = None) -> dict:
    """判定某交易日三层确认各自的通过情况；缺失指标一律视为未通过。"""
    config = {**DEFAULT_PARAMS, **(params or {})}
    close = float(frame["close"].iat[position])
    open_ = float(frame["open"].iat[position])
    rsi_value = context["rsi"].iat[position]
    rsi_previous = context["rsi"].iat[position - 1] if position >= 1 else np.nan
    volume = float(frame["volume"].iat[position])
    volume_ma5 = context["volume_ma5"].iat[position]
    delta = breadth_delta5.iat[position] if breadth_delta5 is not None else np.nan

    if kind == "bottom":
        threshold = float(frame["high"].iat[candidate])
        price_ok = bool(close > threshold)
        momentum_ok = bool(close > open_ and np.isfinite(rsi_value) and np.isfinite(rsi_previous)
                           and rsi_value > rsi_previous)
        volume_ok = bool(
            (np.isfinite(volume_ma5) and volume_ma5 > 0
             and volume >= config["volume_ratio"] * volume_ma5)
            or (np.isfinite(delta) and delta >= config["breadth_improve"])
        )
    else:
        threshold = float(frame["low"].iat[candidate])
        price_ok = bool(close < threshold)
        ma5 = context["ma5"].iat[position]
        ma5_previous = context["ma5"].iat[position - 1] if position >= 1 else np.nan
        momentum_ok = bool(
            close < open_ and np.isfinite(rsi_value) and np.isfinite(rsi_previous)
            and rsi_value < rsi_previous
            and np.isfinite(ma5) and np.isfinite(ma5_previous) and ma5 < ma5_previous
        )
        volume_ok = bool(
            (np.isfinite(volume_ma5) and volume_ma5 > 0
             and volume >= config["volume_ratio"] * volume_ma5)
            or (np.isfinite(delta) and delta <= -config["breadth_improve"])
        )
    return {"price": price_ok, "momentum": momentum_ok, "volume": volume_ok}


def _confirm(frame: pd.DataFrame, context: dict, breadth_delta5: pd.Series,
             candidate: int, kind: str, params: dict) -> tuple[int | None, list[dict]]:
    """在 1..window 个交易日内寻找三层同时通过的确认日。"""
    log = []
    for offset in range(1, params["confirm_window"] + 1):
        position = candidate + offset
        if position >= len(frame):
            break
        layers = layer_state(frame, context, breadth_delta5, position, kind, candidate, params)
        log.append({
            "date": frame["date"].iat[position].strftime("%Y-%m-%d"),
            "offset": offset,
            "price": layers["price"],
            "momentum": layers["momentum"],
            "volume": layers["volume"],
            "passed": int(sum(layers[key] for key in LAYER_KEYS)),
            "close": float(frame["close"].iat[position]),
            "candidate_reference": float(
                frame["high"].iat[candidate] if kind == "bottom" else frame["low"].iat[candidate]
            ),
            "rsi14": float(context["rsi"].iat[position]) if np.isfinite(context["rsi"].iat[position]) else None,
            "volume_ratio": (
                float(frame["volume"].iat[position] / context["volume_ma5"].iat[position])
                if np.isfinite(context["volume_ma5"].iat[position]) and context["volume_ma5"].iat[position] > 0
                else None
            ),
            "breadth_delta5": float(breadth_delta5.iat[position]) if np.isfinite(breadth_delta5.iat[position]) else None,
        })
        if layers["price"] and layers["momentum"] and layers["volume"]:
            return position, log
    return None, log


def prepare(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
            params: dict | None = None) -> dict:
    """一次性计算因子、候选与确认上下文，供信号与候选展示共用。"""
    config = {**DEFAULT_PARAMS, **(params or {})}
    factors = factor_table(frame, breadth)
    masks = candidate_masks(factors, config)
    delta = factors["breadth_delta5"] if "breadth_delta5" in factors else pd.Series(np.nan, index=frame.index)
    return {
        "frame": frame, "factors": factors, "masks": masks, "params": config,
        "context": _confirmation_context(frame), "breadth_delta5": delta,
    }


def _swing_gate(frame: pd.DataFrame, candidate: int, last_position: int | None,
                config: dict) -> bool:
    """最小波段约束：间隔足够且振幅足够，避免把噪声反弹当成波段。"""
    if last_position is None:
        return True
    if candidate - last_position < config["min_swing_days"]:
        return False
    amplitude = abs(float(frame["close"].iat[candidate]) / float(frame["close"].iat[last_position]) - 1)
    return amplitude >= config["min_swing_amplitude"]


def swing_details(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                  params: dict | None = None) -> list[dict]:
    """生成波段信号明细；同一波段只保留一个转折信号。"""
    state = prepare(frame, breadth, params)
    config, factors = state["params"], state["factors"]
    context, delta = state["context"], state["breadth_delta5"]
    candidates = [
        (int(position), kind)
        for kind in KINDS
        for position in np.flatnonzero(state["masks"][kind].to_numpy())
    ]
    candidates.sort()
    records: list[dict] = []
    last_kind = None
    last_position = None
    for position, kind in candidates:
        if last_kind == kind or (last_position is not None and position <= last_position):
            continue
        if not _swing_gate(frame, position, last_position, config):
            continue
        confirmed, log = _confirm(frame, context, delta, position, kind, config)
        if confirmed is None:
            continue
        core_hits = int(factors[f"core_hits_{kind}"].iat[position])
        score = float(factors[f"score_{kind}"].iat[position])
        records.append({
            "date": frame["date"].iat[confirmed].strftime("%Y-%m-%d"),
            "candidate_date": frame["date"].iat[position].strftime("%Y-%m-%d"),
            "type": kind,
            "price": float(frame["close"].iat[confirmed]),
            "candidate_price": float(frame["close"].iat[position]),
            "score": round(score, 4),
            "core_hits": core_hits,
            "confirm_offset": confirmed - position,
            "factors": {
                name: (round(float(factors[f"{name}_{kind}"].iat[position]), 4)
                       if np.isfinite(factors[f"{name}_{kind}"].iat[position]) else None)
                for name in ("position", "divergence", "volume", "volatility", "trend", "breadth")
            },
            "breadth": {
                "breadth20": (round(float(factors["breadth20"].iat[position]), 4)
                              if np.isfinite(factors["breadth20"].iat[position]) else None),
                "delta5": (round(float(factors["breadth_delta5"].iat[position]), 4)
                           if np.isfinite(factors["breadth_delta5"].iat[position]) else None),
            },
            "confirmation": log,
        })
        last_kind = kind
        last_position = confirmed
    _assign_grades(records, config)
    return records


def _assign_grades(records: list[dict], config: dict) -> None:
    """A 级 = 核心三项（价格位置/宽度/量能）全部极端，且分数不低于本指数信号中位数。"""
    if not records:
        return
    scores = sorted(record["score"] for record in records)
    cutoff = float(np.quantile(scores, config["grade_quantile"]))
    for record in records:
        record["grade"] = "A" if (
            record["score"] >= cutoff and record["core_hits"] == 3
        ) else "B"


def swing_signals(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                  params: dict | None = None) -> pd.Series:
    """与既有接口对齐的信号序列：只有确认日带类型，其余为空。"""
    signals = pd.Series("", index=frame.index, dtype="string")
    lookup = {day.strftime("%Y-%m-%d"): position for position, day in enumerate(frame["date"])}
    for record in swing_details(frame, breadth, params):
        signals.iat[lookup[record["date"]]] = record["type"]
    return signals
