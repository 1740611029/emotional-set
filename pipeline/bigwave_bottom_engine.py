"""大波段底部信号引擎（生产版）：自适应幅度 ZigZag 极值 → 1~3 日三层确认。

对应研究 `research_dual_bottom.py` 的「结构底」方案，参数在训练段
（2020-10-30~2024-12-31）按指数波动分 3 组冻结，验证段未参与择参。

本模块只产出**底部**信号（顶部按用户 2026-11-09 决定暂时关闭），并输出
「超卖 + 宽度冰点」背景带区间供前端做浅色底纹。

严格因果（AGENTS.md #1）：ZigZag 极值只在反向运动达幅度阈值、且与上一拐点
满足最小间隔时才被"事后确认"；信号日 = 三层确认通过日，绝早于该日；三层判定
只读信号日及之前数据。所有 ZigZag 逻辑在本模块内实现，不依赖任何研究模块。

纯计算，不做 I/O。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_algorithms import RESEARCH_END as TRAIN_END
from research_algorithms import RESEARCH_START as TRAIN_START
from swing_indicators import factor_table
from swing_signal_engine import layer_state, prepare

ALGORITHM_VERSION = "index-bigwave-bottom-v1.0"
KINDS = ("bottom", "top")

# 指数按波动分 3 组；每组共用一套冻结参数（防逐指数过拟合，见设计说明）。
GROUPS = {
    "steady": ("sh000001", "sh000300"),
    "mid": ("sz399001", "sh000905", "sh000852"),
    "active": ("sz399006", "sh000688"),
}
# 训练段冻结参数：幅度线 = k × 该指数训练段年化波动；min_gap 最小波段间隔；use_ma60 趋势结构确认。
FROZEN = {
    "steady": {"k": 0.45, "min_gap": 30, "use_ma60": True},
    "mid": {"k": 0.55, "min_gap": 30, "use_ma60": True},
    "active": {"k": 0.45, "min_gap": 20, "use_ma60": True},
}
FROZEN_NOTE = ("train 2020-10-30~2024-12-31, 7 indices grouped by vol; "
               "objective = train direction+big-band hit under per-year<=2.5")

CONFIRM_WINDOW = 3          # 三层确认窗口（自拐点自然确认日起 0~2 个交易日）
FACTOR_NAMES = ("position", "divergence", "volume", "volatility", "trend", "breadth")

# 情绪底背景带：「超卖(position 高) + 宽度冰点(breadth 高)」连续区间。
ZONE_POSITION = 0.80
ZONE_BREADTH = 0.55


def group_of(code: str) -> str:
    for name, codes in GROUPS.items():
        if code in codes:
            return name
    raise KeyError(f"未登记分组的指数：{code}")


def annual_vol(frame: pd.DataFrame) -> float:
    """训练段年化波动，冻结幅度线的唯一依据。"""
    part = frame[(frame["date"] >= TRAIN_START) & (frame["date"] <= TRAIN_END)]
    ret = part["close"].pct_change().dropna()
    return float(ret.std() * np.sqrt(252))


def amplitude_for(code: str | None, frame: pd.DataFrame) -> float:
    group = group_of(code) if code is not None else "mid"
    return float(FROZEN[group]["k"]) * annual_vol(frame)


def _zigzag_pivots(frame: pd.DataFrame, amplitude: float, min_gap: int) -> list[dict]:
    """滚动 ZigZag 极值确认（严格因果，仅用已发生数据）。返回原始极值记录。"""
    close = frame["close"].to_numpy(dtype=float)
    dates = frame["date"]
    n = close.size
    if n < 2 or amplitude <= 0 or min_gap < 1:
        return []

    def seek(start: int, want: str, prev_idx: int | None) -> tuple[int, int] | None:
        ext = close[start]
        ext_idx = start
        for i in range(start + 1, n):
            c = close[i]
            if want == "top":
                if c > ext:
                    ext, ext_idx = c, i
                reversal = (ext - c) / ext if ext > 0 else 0.0
            else:
                if c < ext:
                    ext, ext_idx = c, i
                reversal = (c - ext) / ext if ext > 0 else 0.0
            if reversal >= amplitude and (i - ext_idx) >= 1:
                if prev_idx is not None and ext_idx - prev_idx < min_gap:
                    continue
                return ext_idx, i
        return None

    hi = lo = close[0]
    hi_idx = lo_idx = 0
    seed = None
    for i in range(1, n):
        c = close[i]
        if c > hi:
            hi, hi_idx = c, i
        if c < lo:
            lo, lo_idx = c, i
        top_ready = hi > 0 and (hi - c) / hi >= amplitude and (i - hi_idx) >= 1
        bottom_ready = lo > 0 and (c - lo) / lo >= amplitude and (i - lo_idx) >= 1
        if top_ready and bottom_ready:
            seed = ((hi_idx, "top", i) if (hi_idx < lo_idx or (hi_idx == lo_idx and (hi - c) / hi >= (c - lo) / lo))
                    else (lo_idx, "bottom", i))
            break
        if top_ready:
            seed = (hi_idx, "top", i)
            break
        if bottom_ready:
            seed = (lo_idx, "bottom", i)
            break
    if seed is None:
        return []

    pivots = [seed]
    cur_idx, cur_type, _ = seed
    while True:
        want = "bottom" if cur_type == "top" else "top"
        nxt = seek(cur_idx, want, cur_idx)
        if nxt is None:
            break
        ext_idx, confirm_idx = nxt
        if ext_idx <= cur_idx:
            break
        pivots.append((ext_idx, want, confirm_idx))
        cur_idx, cur_type = ext_idx, want

    return [{
        "index": int(idx),
        "type": kind,
        "price": float(close[idx]),
        "date": dates.iat[idx].strftime("%Y-%m-%d"),
        "confirm_index": int(conf),
        "confirm_date": dates.iat[conf].strftime("%Y-%m-%d"),
    } for idx, kind, conf in pivots]


def _confirm_bottom(frame: pd.DataFrame, context: dict, delta: pd.Series,
                    candidate: int, natural: int) -> tuple[int | None, list[dict]]:
    """自拐点自然确认日起 0~2 个交易日内找三层全通过日；只读 ≤ 确认日数据。"""
    log: list[dict] = []
    for offset in range(0, CONFIRM_WINDOW):
        position = natural + offset
        if position >= len(frame):
            break
        layers = layer_state(frame, context, delta, position, "bottom", candidate, None)
        passed = bool(layers["price"] and layers["momentum"] and layers["volume"])
        close = float(frame["close"].iat[position])
        volume_ma5 = context["volume_ma5"].iat[position]
        log.append({
            "date": frame["date"].iat[position].strftime("%Y-%m-%d"),
            "offset": offset,
            "price": bool(layers["price"]),
            "momentum": bool(layers["momentum"]),
            "volume": bool(layers["volume"]),
            "passed": int(bool(layers["price"])) + int(bool(layers["momentum"])) + int(bool(layers["volume"])),
            "close": close,
            "candidate_reference": float(frame["high"].iat[candidate]),
            "rsi14": float(context["rsi"].iat[position]) if np.isfinite(context["rsi"].iat[position]) else None,
            "volume_ratio": (float(frame["volume"].iat[position] / volume_ma5)
                             if np.isfinite(volume_ma5) and volume_ma5 > 0 else None),
            "breadth_delta5": (float(delta.iat[position]) if delta is not None and np.isfinite(delta.iat[position]) else None),
        })
        if passed:
            return position, log
    return None, log


def _assign_grades(records: list[dict]) -> None:
    if not records:
        return
    scores = sorted(record["score"] for record in records)
    cutoff = float(np.quantile(scores, 0.5))
    for record in records:
        record["grade"] = "A" if (record["score"] >= cutoff and record["core_hits"] == 3) else "B"


def bottom_details(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                   code: str | None = None) -> list[dict]:
    """产出底部信号明细（swing 兼容字段结构）：只为 bottom，顶部不产出。

    code 缺省时按中波动组参数兜底（仅为兼容无指数上下文的公开调用）。
    """
    params = FROZEN[group_of(code) if code is not None else "mid"]
    amplitude = amplitude_for(code, frame)
    state = prepare(frame, breadth, {"confirm_window": CONFIRM_WINDOW})
    factors, context, delta = state["factors"], state["context"], state["breadth_delta5"]
    ma60 = frame["close"].rolling(60).mean()

    records: list[dict] = []
    for pivot in _zigzag_pivots(frame, amplitude, int(params["min_gap"])):
        if pivot["type"] != "bottom":
            continue
        candidate = pivot["index"]
        natural = pivot["confirm_index"]
        position, log = _confirm_bottom(frame, context, delta, candidate, natural)
        if position is None:
            continue
        if params["use_ma60"]:
            level = ma60.iat[position]
            if not (np.isfinite(level) and float(frame["close"].iat[position]) > float(level)):
                continue
        score = float(factors["score_bottom"].iat[candidate])
        core_hits = int(factors.get("core_hits_bottom", pd.Series(0, index=frame.index)).iat[candidate])
        records.append({
            "date": frame["date"].iat[position].strftime("%Y-%m-%d"),
            "candidate_date": pivot["date"],
            "type": "bottom",
            "price": float(frame["close"].iat[position]),
            "candidate_price": pivot["price"],
            "score": round(score, 4) if np.isfinite(score) else None,
            "core_hits": core_hits,
            "confirm_offset": int(position - candidate),
            "factors": {
                name: (round(float(factors[f"{name}_bottom"].iat[candidate]), 4)
                       if np.isfinite(factors[f"{name}_bottom"].iat[candidate]) else None)
                for name in FACTOR_NAMES
            },
            "breadth": {
                "breadth20": (round(float(factors["breadth20"].iat[candidate]), 4)
                              if np.isfinite(factors["breadth20"].iat[candidate]) else None),
                "delta5": (round(float(factors["breadth_delta5"].iat[candidate]), 4)
                           if np.isfinite(factors["breadth_delta5"].iat[candidate]) else None),
            },
            "confirmation": log,
            # 内部参考字段（不进入对外 SIGNAL 白名单，仅引擎内部与调试使用）
            "_pivot_price": pivot["price"],
            "_natural_confirm_date": pivot["confirm_date"],
            "_amplitude": round(amplitude, 5),
        })
    _assign_grades(records)
    return records


def bottom_zones(frame: pd.DataFrame, breadth: pd.DataFrame | None = None) -> list[dict]:
    """情绪底背景带：超卖 + 宽度冰点 的连续区间（浅色底纹用）。"""
    if len(frame) == 0:
        return []
    factors = factor_table(frame, breadth)
    over_sold = ((factors["position_bottom"] >= ZONE_POSITION)
                 & (factors["breadth_bottom"] >= ZONE_BREADTH)).fillna(False).to_numpy()
    divergence = (factors["divergence_bottom"] >= 2 / 3).fillna(False).to_numpy()
    zones: list[dict] = []
    start = None
    for i, flag in enumerate(over_sold):
        if flag and start is None:
            start = i
        if not flag and start is not None:
            zones.append(_zone(frame, start, i - 1, divergence))
            start = None
    if start is not None:
        zones.append(_zone(frame, start, len(over_sold) - 1, divergence))
    return zones


def _zone(frame: pd.DataFrame, start: int, end: int, divergence: np.ndarray) -> dict:
    return {
        "start_date": frame["date"].iat[start].strftime("%Y-%m-%d"),
        "end_date": frame["date"].iat[end].strftime("%Y-%m-%d"),
        "days": int(end - start + 1),
        "has_divergence": bool(divergence[start:end + 1].any()),
    }
