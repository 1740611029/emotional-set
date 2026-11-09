"""拐点锚定候选引擎（研究阶段专用，纯计算，绝不写文件）。

结构（design D1/D2、index-signal-engine delta）：
1. 候选主干 = ZigZag 事后确认制价格拐点（复用已审计的 pivot_algorithms.zigzag_signals）；
2. 六因子作为**严格化闸门**而非候选源，判定日 = 拐点自然确认日；
3. 三层确认（价格 → 动量 → 量能）起点 MUST 绑定自然确认日，窗口自该日起最多 3 个交易日，
   MUST NOT 早于该日；逐日逐层日志含判定值、阈值与通过与否，覆盖全部候选（含作废者）。

因果性：任一日 T 的信号只依赖 T 及之前数据；无全量归一化、无未来 K 线。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from pivot_algorithms import zigzag_signals
from pivot_benchmark import annotate_pivots
from precision_config import ALGORITHM_VERSION, FROZEN_GATE, FROZEN_PIVOT
from swing_indicators import CORE_DIRECTIONS, DIRECTIONS, factor_table
from swing_signal_engine import DEFAULT_PARAMS, layer_state, prepare

LAYER_KEYS = ("price", "momentum", "volume")


def normalize(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"])
    return work.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def pivot_benchmark_set(frame: pd.DataFrame, *, amplitude: float = FROZEN_PIVOT["amplitude"],
                        min_gap: int = FROZEN_PIVOT["min_gap"],
                        input_hash: str) -> dict:
    """事后确认制拐点基准集（只用于评价，MUST NOT 参与信号产生）。附输入数据哈希。"""
    work = normalize(frame)
    pivots = annotate_pivots(work, amplitude=amplitude, min_gap=min_gap)
    return {
        "input_hash": input_hash,
        "amplitude": float(amplitude),
        "min_gap": int(min_gap),
        "rule": "某日仅在其后价格反向运动达阈值且满足最小间隔时被事后标注为拐点",
        "used_for": "评价标尺，不参与信号产生时点的判定",
        "pivot_count": len(pivots),
        "pivots": pivots,
    }


def gate_state(factors: pd.DataFrame, work: pd.DataFrame, position: int,
               kind: str, gate: dict) -> dict:
    """六方向得分严格闸门；判定日 = 拐点极值日（因子数据当日已可知）。

    注意（design D11）：候选在拐点**自然确认日**才产生，但六因子只读极值日及之前的
    行情，不依赖"该日是拐点"这一事后事实，因此仍然严格因果。若改在自然确认日取值，
    divergence 维度在本数据集 173/173 个候选上恒为 0，六维闸门将退化为不可能满足。
    核心命中数采用与生产 candidate_masks 一致的 0.60 阈值口径，而非 factor_table
    内部用于分级的 0.85 口径。
    """
    scores: dict[str, float | None] = {}
    for name in DIRECTIONS:
        value = factors[f"{name}_{kind}"].iat[position]
        scores[name] = float(value) if np.isfinite(value) else None
    available = {name: value for name, value in scores.items() if value is not None}
    score = float(factors[f"score_{kind}"].iat[position])
    core_extreme = float(gate.get("core_extreme", 0.60))
    core_hits = sum(
        1 for name in CORE_DIRECTIONS
        if scores.get(name) is not None and scores[name] >= core_extreme
    )
    veto = bool(factors[f"veto_{kind}"].iat[position])
    each_ok = all(value + 1e-12 >= gate["direction_min"] for value in available.values())
    passed = bool(
        np.isfinite(score)
        and score + 1e-12 >= gate["score_threshold"]
        and core_hits >= gate["core_hits_min"]
        and len(available) >= gate["min_available_directions"]
        and each_ok
        and not veto
    )
    return {
        "date": work["date"].iat[position].strftime("%Y-%m-%d"),
        "direction_scores": scores,
        "available_directions": len(available),
        "composite_score": score if np.isfinite(score) else None,
        "core_hits": core_hits,
        "veto": veto,
        "thresholds": {
            "score_threshold": gate["score_threshold"],
            "core_hits_min": gate["core_hits_min"],
            "core_extreme": core_extreme,
            "direction_min": gate["direction_min"],
            "min_available_directions": gate["min_available_directions"],
        },
        "factor_evaluation_date": work["date"].iat[position].strftime("%Y-%m-%d"),
        "causality_note": "因子取拐点极值日已可知数据；候选在自然确认日才产生",
        "each_direction_passed": each_ok,
        "passed": passed,
    }


def _layer_log(frame: pd.DataFrame, state: dict, position: int, kind: str,
               candidate: int, config: dict, offset: int) -> dict:
    """逐日逐层判定日志：布尔判定复用生产 layer_state，判定值与阈值逐项列全。"""
    context, delta = state["context"], state["breadth_delta5"]
    layers = layer_state(frame, context, delta, position, kind, candidate, config)
    close = float(frame["close"].iat[position])
    open_ = float(frame["open"].iat[position])
    reference = float(frame["high"].iat[candidate] if kind == "bottom"
                      else frame["low"].iat[candidate])
    rsi_value = float(context["rsi"].iat[position]) if np.isfinite(context["rsi"].iat[position]) else None
    rsi_previous = (float(context["rsi"].iat[position - 1])
                    if position >= 1 and np.isfinite(context["rsi"].iat[position - 1]) else None)
    ma5 = float(context["ma5"].iat[position]) if np.isfinite(context["ma5"].iat[position]) else None
    ma5_previous = (float(context["ma5"].iat[position - 1])
                    if position >= 1 and np.isfinite(context["ma5"].iat[position - 1]) else None)
    volume = float(frame["volume"].iat[position])
    volume_ma5 = context["volume_ma5"].iat[position]
    volume_ratio = (float(volume / volume_ma5)
                    if np.isfinite(volume_ma5) and volume_ma5 > 0 else None)
    breadth_delta5 = (float(delta.iat[position]) if delta is not None
                      and np.isfinite(delta.iat[position]) else None)
    return {
        "date": frame["date"].iat[position].strftime("%Y-%m-%d"),
        "offset": int(offset),
        "price": {
            "close": close, "candidate_reference": reference,
            "threshold": reference,
            "rule": "bottom: close > 候选日最高价；top: close < 候选日最低价",
            "passed": bool(layers["price"]),
        },
        "momentum": {
            "close": close, "open": open_, "rsi14": rsi_value, "rsi14_previous": rsi_previous,
            "ma5": ma5, "ma5_previous": ma5_previous,
            "rule": "bottom: 阳线且 RSI14 上行；top: 阴线且 RSI14 下行且 MA5 下行",
            "passed": bool(layers["momentum"]),
        },
        "volume": {
            "volume_ratio": volume_ratio, "volume_ratio_threshold": config["volume_ratio"],
            "breadth_delta5": breadth_delta5,
            "breadth_threshold": config["breadth_improve"],
            "rule": "量比达阈值 或 宽度 5 日变化达阈值",
            "passed": bool(layers["volume"]),
        },
        "passed_count": int(sum(bool(layers[key]) for key in LAYER_KEYS)),
        "passed": all(bool(layers[key]) for key in LAYER_KEYS),
    }


def three_layer_confirm(frame: pd.DataFrame, state: dict, candidate: int,
                        kind: str, start: int, window: int) -> tuple[int | None, list[dict]]:
    """从 start（拐点自然确认日，含）起最多 window 个交易日找三层全通过日。

    只读 ≤ 确认日的数据；start 之前 MUST NOT 使用任何拐点信息。
    """
    config = state["params"]
    log: list[dict] = []
    for offset in range(0, window):
        position = start + offset
        if position >= len(frame):
            break
        entry = _layer_log(frame, state, position, kind, candidate, config, offset)
        log.append(entry)
        if entry["passed"]:
            return position, log
    return None, log


def prepare_candidate_state(work: pd.DataFrame, breadth: pd.DataFrame | None,
                            window: int) -> dict:
    """一次性算好六因子表与三层确认上下文，供同指数的多个参数组合复用。"""
    return {
        "work": work,
        "factors": factor_table(work, breadth),
        # prepare 复用生产引擎的三层上下文，参数仅覆盖确认窗口，判定逻辑保持一致。
        "state": prepare(work, breadth, {"confirm_window": window}),
        "lookup": {day.strftime("%Y-%m-%d"): position
                   for position, day in enumerate(work["date"])},
    }


def precision_signals(frame: pd.DataFrame, *, params: dict,
                      breadth: pd.DataFrame | None = None,
                      prepared: dict | None = None
                      ) -> tuple[list[dict], list[dict]]:
    """返回 (已确认信号, 全部作废候选日志)。信号日 = 三层确认通过日。"""
    gate_config = params.get("six_factor_gate", FROZEN_GATE)
    window = int(gate_config["confirm_window"])
    pivot_amplitude = float(params["pivot_amplitude"])
    pivot_min_gap = int(params["pivot_min_gap"])

    if prepared is None:
        prepared = prepare_candidate_state(normalize(frame), breadth, window)
    work = prepared["work"]
    factors, state, lookup = prepared["factors"], prepared["state"], prepared["lookup"]

    signals: list[dict] = []
    dropped: list[dict] = []
    for pivot in zigzag_signals(work, amplitude=pivot_amplitude, min_gap=pivot_min_gap):
        kind = pivot["type"]
        candidate = lookup.get(pivot["pivot_date"])
        natural = lookup.get(pivot["date"])
        if candidate is None or natural is None:
            continue
        gate = gate_state(factors, work, candidate, kind, gate_config)
        base = {
            "type": kind,
            "pivot_date": pivot["pivot_date"],
            "pivot_price": pivot["pivot_price"],
            "natural_confirm_date": pivot["date"],
            "natural_confirm_offset": int(natural - candidate),
            "pivot_amplitude": pivot_amplitude,
            "pivot_min_gap": pivot_min_gap,
            "gate": gate,
            "confirmation": [],
        }
        if not gate["passed"]:
            dropped.append({**base, "reason": "six_factor_gate_failed"})
            continue
        position, log = three_layer_confirm(work, state, candidate, kind, natural, window)
        base["confirmation"] = log
        if position is None:
            dropped.append({**base, "reason": "three_layer_unconfirmed"})
            continue
        if position < natural:  # 防御性断言：确认不得早于拐点可知日
            raise AssertionError("三层确认早于拐点自然确认日，违反无未来函数约束")
        signals.append({
            "date": work["date"].iat[position].strftime("%Y-%m-%d"),
            "type": kind,
            "price": float(work["close"].iat[position]),
            "algorithm": params.get("algorithm_version", ALGORITHM_VERSION),
            **{key: base[key] for key in (
                "pivot_date", "pivot_price", "natural_confirm_date",
                "natural_confirm_offset", "pivot_amplitude", "pivot_min_gap",
                "gate", "confirmation")},
            "signal_confirm_offset_from_pivot": int(position - candidate),
            "signal_confirm_offset_from_natural": int(position - natural),
        })
    return signals, dropped
