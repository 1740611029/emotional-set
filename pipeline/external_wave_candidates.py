"""外部低滞后波段候选；仅用于离线研究，不改变生产信号。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from top_bottom_candidate_algorithms import KINDS, cross_down, cross_up

EXTERNAL_FAMILIES = {
    "HMA方向反转",
    "DEMA方向反转",
    "TEMA方向反转",
    "ZLEMA方向反转",
    "Ehlers瞬时趋势线",
}


def wma(series: pd.Series, period: int) -> pd.Series:
    weights = np.arange(1, period + 1, dtype=float)
    return series.rolling(period).apply(lambda x: float(np.dot(x, weights) / weights.sum()), raw=True)


def hma(series: pd.Series, period: int) -> pd.Series:
    half = max(1, period // 2)
    root = max(1, int(np.sqrt(period)))
    return wma(2 * wma(series, half) - wma(series, period), root)


def dema(series: pd.Series, period: int) -> pd.Series:
    first = series.ewm(span=period, adjust=False).mean()
    second = first.ewm(span=period, adjust=False).mean()
    return 2 * first - second


def tema(series: pd.Series, period: int) -> pd.Series:
    first = series.ewm(span=period, adjust=False).mean()
    second = first.ewm(span=period, adjust=False).mean()
    third = second.ewm(span=period, adjust=False).mean()
    return 3 * first - 3 * second + third


def zlema(series: pd.Series, period: int) -> pd.Series:
    lag = max(1, (period - 1) // 2)
    adjusted = series + (series - series.shift(lag))
    return adjusted.ewm(span=period, adjust=False).mean()


def ehlers_itrend(series: pd.Series, period: int = 20) -> tuple[pd.Series, pd.Series]:
    period = max(2, int(period))
    values = series.to_numpy(dtype=float)
    trend = np.full(len(values), np.nan)
    if len(values) == 0:
        return pd.Series(trend, index=series.index), pd.Series(trend, index=series.index)
    theta = np.sqrt(2) * np.pi / period
    a1 = np.exp(-theta)
    c2 = 2 * a1 * np.cos(theta)
    c3 = -(a1 ** 2)
    c1 = (1 - c2 - c3) / 4
    trend[0] = values[0]
    if len(values) > 1:
        trend[1] = values[1]
    for i in range(2, len(values)):
        trend[i] = c1 * (values[i] + 2 * values[i - 1] + values[i - 2]) + c2 * trend[i - 1] + c3 * trend[i - 2]
    trigger = np.full(len(values), np.nan)
    trigger[2:] = 2 * trend[2:] - trend[:-2]
    warmup = max(2, period - 1)
    trend[:warmup] = np.nan
    trigger[:warmup + 2] = np.nan
    return pd.Series(trend, index=series.index), pd.Series(trigger, index=series.index)


def external_candidate_points(frame: pd.DataFrame, spec: dict) -> dict[str, list[tuple[int, int]]]:
    family = spec["family"]
    period = int(spec["params"].get("period", 20))
    close = frame["close"]
    if family == "HMA方向反转":
        line = hma(close, period)
    elif family == "DEMA方向反转":
        line = dema(close, period)
    elif family == "TEMA方向反转":
        line = tema(close, period)
    elif family == "ZLEMA方向反转":
        line = zlema(close, period)
    elif family == "Ehlers瞬时趋势线":
        line, trigger = ehlers_itrend(close, period)
        bottom = cross_up(trigger, line)
        top = cross_down(trigger, line)
    else:
        raise ValueError(f"未知外部候选算法：{family}")
    if family != "Ehlers瞬时趋势线":
        slope = line.diff()
        bottom = (slope > 0) & (slope.shift() <= 0)
        top = (slope < 0) & (slope.shift() >= 0)
    return {
        kind: [(int(i), int(i)) for i in np.flatnonzero(mask.fillna(False).to_numpy())]
        for kind, mask in (("bottom", bottom), ("top", top))
    }


def external_specs() -> list[dict]:
    specs = [
        {"family": "HMA方向反转", "params": {"period": period}}
        for period in (16, 25, 36, 49)
    ]
    specs.extend([
        {"family": "DEMA方向反转", "params": {"period": 20}},
        {"family": "TEMA方向反转", "params": {"period": 20}},
        {"family": "ZLEMA方向反转", "params": {"period": 20}},
        {"family": "Ehlers瞬时趋势线", "params": {"period": 20}},
    ])
    return specs
