"""离线顶底候选算法库；候选在检测日可知，统一接入三层确认。"""
from __future__ import annotations

import json

import numpy as np
import pandas as pd

from pivot_algorithms import donchian_signals, fractal_signals, zigzag_signals
from research_algorithms import dmi, kdj, macd, rolling_slope, rsi, supertrend_direction
from swing_signal_engine import layer_state

KINDS = ("bottom", "top")


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def cross_up(left: pd.Series, right: pd.Series | float) -> pd.Series:
    right_series = pd.Series(right, index=left.index) if np.isscalar(right) else right
    return ((left > right_series) & (left.shift() <= right_series.shift())).fillna(False)


def cross_down(left: pd.Series, right: pd.Series | float) -> pd.Series:
    right_series = pd.Series(right, index=left.index) if np.isscalar(right) else right
    return ((left < right_series) & (left.shift() >= right_series.shift())).fillna(False)


def true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift()
    return pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    return true_range(frame).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def stochastic(frame: pd.DataFrame, period: int) -> tuple[pd.Series, pd.Series]:
    low = frame["low"].rolling(period).min()
    high = frame["high"].rolling(period).max()
    raw = 100 * (frame["close"] - low) / (high - low).replace(0, np.nan)
    slow_k = raw.rolling(3).mean()
    return slow_k, slow_k.rolling(3).mean()


def kama(close: pd.Series, period: int = 10, fast: int = 2, slow: int = 30) -> pd.Series:
    change = close.diff(period).abs()
    volatility = close.diff().abs().rolling(period).sum()
    efficiency = change / volatility.replace(0, np.nan)
    smoothing = (efficiency * (2 / (fast + 1) - 2 / (slow + 1)) + 2 / (slow + 1)) ** 2
    values = close.to_numpy(dtype=float)
    factors = smoothing.to_numpy(dtype=float)
    output = np.full(len(close), np.nan)
    for i in range(period, len(close)):
        if not np.isfinite(factors[i]):
            continue
        output[i] = values[i] if i == period or not np.isfinite(output[i - 1]) else (
            output[i - 1] + factors[i] * (values[i] - output[i - 1])
        )
    return pd.Series(output, index=close.index)


def psar_direction(frame: pd.DataFrame, step: float = 0.02, maximum: float = 0.20) -> pd.Series:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    close = frame["close"].to_numpy(dtype=float)
    direction = np.ones(len(frame))
    if len(frame) < 2:
        return pd.Series(direction, index=frame.index)
    direction[0] = 1 if close[0] >= frame["open"].iat[0] else -1
    bull = direction[0] == 1
    sar = low[0] if bull else high[0]
    extreme = high[0] if bull else low[0]
    acceleration = step
    for i in range(1, len(frame)):
        sar = sar + acceleration * (extreme - sar)
        if bull:
            sar = min(sar, low[i - 1], low[i - 2] if i >= 2 else low[i - 1])
            if low[i] < sar:
                bull = False
                sar = extreme
                extreme = low[i]
                acceleration = step
            elif high[i] > extreme:
                extreme = high[i]
                acceleration = min(maximum, acceleration + step)
        else:
            sar = max(sar, high[i - 1], high[i - 2] if i >= 2 else high[i - 1])
            if high[i] > sar:
                bull = True
                sar = extreme
                extreme = high[i]
                acceleration = step
            elif low[i] < extreme:
                extreme = low[i]
                acceleration = min(maximum, acceleration + step)
        direction[i] = 1 if bull else -1
    return pd.Series(direction, index=frame.index)


def kst(close: pd.Series) -> tuple[pd.Series, pd.Series]:
    value = (
        close.pct_change(10).rolling(10).mean()
        + 2 * close.pct_change(15).rolling(10).mean()
        + 3 * close.pct_change(20).rolling(10).mean()
        + 4 * close.pct_change(30).rolling(15).mean()
    )
    return value, value.rolling(9).mean()


def tsi(close: pd.Series, long: int = 25, short: int = 13, signal: int = 7) -> tuple[pd.Series, pd.Series]:
    change = close.diff()
    numerator = ema(ema(change, long), short)
    denominator = ema(ema(change.abs(), long), short)
    value = 100 * numerator / denominator.replace(0, np.nan)
    return value, ema(value, signal)


def ultimate(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift()
    buying = frame["close"] - pd.concat([frame["low"], previous], axis=1).min(axis=1)
    spread = pd.concat([frame["high"], previous], axis=1).max(axis=1) - pd.concat(
        [frame["low"], previous], axis=1
    ).min(axis=1)

    def average(period: int) -> pd.Series:
        return buying.rolling(period).sum() / spread.rolling(period).sum().replace(0, np.nan)

    return 100 * (4 * average(7) + 2 * average(14) + average(28)) / 7


def money_flow(frame: pd.DataFrame, period: int) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    raw = typical * frame["volume"]
    positive = raw.where(typical.diff() > 0, 0.0).rolling(period).sum()
    negative = raw.where(typical.diff() < 0, 0.0).rolling(period).sum()
    ratio = positive / negative.replace(0, np.nan)
    value = 100 - 100 / (1 + ratio)
    return value.mask((negative == 0) & (positive > 0), 100.0)


def adl(frame: pd.DataFrame) -> pd.Series:
    spread = (frame["high"] - frame["low"]).replace(0, np.nan)
    multiplier = (2 * frame["close"] - frame["high"] - frame["low"]) / spread
    return (multiplier.fillna(0) * frame["volume"]).cumsum()


def rsrs(frame: pd.DataFrame, period: int, normalization: int) -> pd.Series:
    high = frame["high"]
    low = frame["low"]
    covariance = high.rolling(period).cov(low)
    beta = covariance / low.rolling(period).var().replace(0, np.nan)
    return (beta - beta.rolling(normalization).mean()) / beta.rolling(normalization).std(ddof=0).replace(0, np.nan)


def divergence_masks(frame: pd.DataFrame, indicator: pd.Series, lookback: int) -> tuple[pd.Series, pd.Series]:
    high = frame["high"].to_numpy(dtype=float)
    low = frame["low"].to_numpy(dtype=float)
    values = indicator.to_numpy(dtype=float)
    bottom = np.zeros(len(frame), dtype=bool)
    top = np.zeros(len(frame), dtype=bool)
    recent = 20
    for i in range(lookback, len(frame)):
        previous_start = i - lookback
        previous_end = i - recent
        if previous_end <= previous_start or not np.isfinite(values[i]):
            continue
        previous_high = previous_start + int(np.nanargmax(high[previous_start:previous_end]))
        previous_low = previous_start + int(np.nanargmin(low[previous_start:previous_end]))
        if not np.isfinite(values[previous_high]) or not np.isfinite(values[previous_low]):
            continue
        if high[i] >= np.nanmax(high[i - recent + 1:i + 1]) and high[i] > high[previous_high] and values[i] < values[previous_high]:
            top[i] = True
        if low[i] <= np.nanmin(low[i - recent + 1:i + 1]) and low[i] < low[previous_low] and values[i] > values[previous_low]:
            bottom[i] = True
    return pd.Series(bottom, index=frame.index), pd.Series(top, index=frame.index)


def candidate_masks(frame: pd.DataFrame, spec: dict) -> tuple[pd.Series, pd.Series]:
    family = spec["family"]
    p = spec["params"]
    close = frame["close"]
    open_ = frame["open"]
    high = frame["high"]
    low = frame["low"]
    volume = frame["volume"]
    bottom = pd.Series(False, index=frame.index)
    top = pd.Series(False, index=frame.index)

    if family == "RSI退出极值":
        value = rsi(close, p["period"])
        bottom = cross_up(value, p["lower"]) & (value.shift() < p["lower"])
        top = cross_down(value, p["upper"]) & (value.shift() > p["upper"])
    elif family == "随机指标交叉":
        k, d = stochastic(frame, p["period"])
        bottom = cross_up(k, d) & (d.shift() < 20)
        top = cross_down(k, d) & (d.shift() > 80)
    elif family == "KDJ交叉":
        k, d, _ = kdj(frame, p["period"])
        bottom = cross_up(k, d) & (d.shift() < 20)
        top = cross_down(k, d) & (d.shift() > 80)
    elif family == "WilliamsR退出极值":
        highest = high.rolling(p["period"]).max()
        lowest = low.rolling(p["period"]).min()
        value = -100 * (highest - close) / (highest - lowest).replace(0, np.nan)
        bottom = cross_up(value, -80) & (value.shift() < -80)
        top = cross_down(value, -20) & (value.shift() > -20)
    elif family == "CCI退出极值":
        typical = (high + low + close) / 3
        mean = typical.rolling(p["period"]).mean()
        deviation = typical.rolling(p["period"]).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        value = (typical - mean) / (0.015 * deviation.replace(0, np.nan))
        bottom = cross_up(value, -p["level"]) & (value.shift() < -p["level"])
        top = cross_down(value, p["level"]) & (value.shift() > p["level"])
    elif family in {"布林回归", "RSI布林共振"}:
        mean = close.rolling(p["period"]).mean()
        deviation = close.rolling(p["period"]).std(ddof=0)
        upper = mean + p["width"] * deviation
        lower = mean - p["width"] * deviation
        bottom = (close >= lower) & (close.shift() < lower.shift())
        top = (close <= upper) & (close.shift() > upper.shift())
        if family == "RSI布林共振":
            value = rsi(close)
            bottom &= value < p["rsi"]
            top &= value > 100 - p["rsi"]
    elif family == "ZScore回归":
        value = (close - close.rolling(p["period"]).mean()) / close.rolling(p["period"]).std(ddof=0).replace(0, np.nan)
        bottom = cross_up(value, -p["level"]) & (value.shift() < -p["level"])
        top = cross_down(value, p["level"]) & (value.shift() > p["level"])
    elif family in {"MACD交叉", "MACD与RSI"}:
        dif, dea = macd(close)
        bottom = cross_up(dif, dea)
        top = cross_down(dif, dea)
        if p.get("zero_filter"):
            bottom &= dea.shift() < 0
            top &= dea.shift() > 0
        if family == "MACD与RSI":
            value = rsi(close)
            bottom &= value < p["rsi"]
            top &= value > 100 - p["rsi"]
    elif family == "ROC极值反转":
        value = close.pct_change(p["period"])
        lower = value.rolling(252, min_periods=60).quantile(0.05)
        upper = value.rolling(252, min_periods=60).quantile(0.95)
        bottom = (value.shift() <= lower.shift()) & (close > close.shift())
        top = (value.shift() >= upper.shift()) & (close < close.shift())
    elif family == "N日极值反转":
        bottom = (close <= close.rolling(p["period"]).min()) & (close > open_)
        top = (close >= close.rolling(p["period"]).max()) & (close < open_)
    elif family == "均线回抽":
        short = close.rolling(p["short"]).mean()
        long = close.rolling(p["long"]).mean()
        bottom = cross_up(close, short) & (close < long)
        top = cross_down(close, short) & (close > long)
    elif family == "双均线交叉":
        short = close.rolling(p["short"]).mean()
        long = close.rolling(p["long"]).mean()
        bottom = cross_up(short, long)
        top = cross_down(short, long)
    elif family == "DMI与ADX":
        plus, minus = dmi(frame, p["period"])
        dx = 100 * (plus - minus).abs() / (plus + minus).replace(0, np.nan)
        adx = dx.ewm(alpha=1 / p["period"], adjust=False, min_periods=p["period"]).mean()
        bottom = cross_up(plus, minus) & (adx >= p["adx"])
        top = cross_down(plus, minus) & (adx >= p["adx"])
    elif family == "抛物线SAR":
        direction = psar_direction(frame, p["step"], p["maximum"])
        bottom = (direction > 0) & (direction.shift() <= 0)
        top = (direction < 0) & (direction.shift() >= 0)
    elif family == "KAMA反转":
        value = kama(close, p["period"], p["fast"], p["slow"])
        bottom = cross_up(close, value) & (value >= value.shift())
        top = cross_down(close, value) & (value <= value.shift())
    elif family == "Donchian反转":
        upper = high.rolling(p["period"]).max().shift()
        lower = low.rolling(p["period"]).min().shift()
        bottom = cross_up(close, upper)
        top = cross_down(close, lower)
    elif family == "Aroon交叉":
        period = p["period"]
        since_high = high.rolling(period).apply(lambda x: len(x) - 1 - np.argmax(x), raw=True)
        since_low = low.rolling(period).apply(lambda x: len(x) - 1 - np.argmin(x), raw=True)
        up = 100 * (period - since_high) / period
        down = 100 * (period - since_low) / period
        bottom = cross_up(up, down)
        top = cross_down(up, down)
    elif family == "一目均衡表":
        conversion = (high.rolling(9).max() + low.rolling(9).min()) / 2
        base = (high.rolling(26).max() + low.rolling(26).min()) / 2
        span_a = ((conversion + base) / 2).shift(26)
        span_b = ((high.rolling(52).max() + low.rolling(52).min()) / 2).shift(26)
        cloud_high = pd.concat([span_a, span_b], axis=1).max(axis=1)
        cloud_low = pd.concat([span_a, span_b], axis=1).min(axis=1)
        bottom = cross_up(conversion, base) & (close > cloud_high)
        top = cross_down(conversion, base) & (close < cloud_low)
    elif family == "回归斜率反转":
        slope = rolling_slope(close, p["period"])
        bottom = cross_up(slope, 0.0)
        top = cross_down(slope, 0.0)
    elif family == "KST交叉":
        value, signal = kst(close)
        bottom = cross_up(value, signal)
        top = cross_down(value, signal)
    elif family == "TSI交叉":
        value, signal = tsi(close, p["long"], p["short"], p["signal"])
        bottom = cross_up(value, signal)
        top = cross_down(value, signal)
    elif family == "终极振荡器":
        value = ultimate(frame)
        bottom = cross_up(value, 30) & (value.shift() < 30)
        top = cross_down(value, 70) & (value.shift() > 70)
    elif family == "BIAS分位回归":
        value = close / close.rolling(p["period"]).mean() - 1
        lower = value.rolling(252, min_periods=60).quantile(0.05)
        upper = value.rolling(252, min_periods=60).quantile(0.95)
        bottom = (value >= lower) & (value.shift() < lower.shift())
        top = (value <= upper) & (value.shift() > upper.shift())
    elif family == "Keltner回归":
        middle = ema(close, p["period"])
        channel = p["width"] * atr(frame, p["atr_period"])
        upper = middle + channel
        lower = middle - channel
        bottom = (close >= lower) & (close.shift() < lower.shift())
        top = (close <= upper) & (close.shift() > upper.shift())
    elif family == "ATR冲击反转":
        value = 100 * atr(frame) / close
        extreme = value.rolling(252, min_periods=60).quantile(0.90)
        bottom = (value >= extreme) & cross_up(close, high.rolling(p["breakout"]).max().shift())
        top = (value >= extreme) & cross_down(close, low.rolling(p["breakout"]).min().shift())
    elif family == "MassIndex反转":
        ratio = ema(high - low, 9) / ema(ema(high - low, 9), 9).replace(0, np.nan)
        value = ratio.rolling(25).sum()
        trend = ema(close, 9).diff()
        bottom = cross_down(value, 26.5) & (value.rolling(10).max() >= 27) & (trend < 0)
        top = cross_down(value, 26.5) & (value.rolling(10).max() >= 27) & (trend > 0)
    elif family == "历史波动率跃迁":
        value = np.log(close / close.shift()).rolling(p["period"]).std(ddof=0) * np.sqrt(252)
        extreme = value.rolling(252, min_periods=60).quantile(0.90)
        bottom = (value >= extreme) & (close.pct_change(5) > 0) & cross_up(close, close.rolling(10).max().shift())
        top = (value >= extreme) & (close.pct_change(5) < 0) & cross_down(close, close.rolling(10).min().shift())
    elif family == "Ulcer修复":
        peak = close.rolling(p["period"]).max()
        drawdown = 100 * (close - peak) / peak
        value = np.sqrt((drawdown ** 2).rolling(p["period"]).mean())
        average = close.rolling(20).mean()
        bottom = cross_up(close, average) & (value < value.shift())
        top = cross_down(close, average) & (value > value.shift())
    elif family == "OBV趋势反转":
        signed = np.sign(close.diff()).fillna(0) * volume
        obv = signed.cumsum()
        signal = ema(obv, p["period"])
        average = close.rolling(60).mean()
        bottom = cross_up(obv, signal) & (close < average)
        top = cross_down(obv, signal) & (close > average)
    elif family == "CMF零轴":
        period = p["period"]
        spread = (high - low).replace(0, np.nan)
        flow = ((2 * close - high - low) / spread).fillna(0) * volume
        value = flow.rolling(period).sum() / volume.rolling(period).sum().replace(0, np.nan)
        bottom = cross_up(value, 0.0) & (close < close.rolling(60).mean())
        top = cross_down(value, 0.0) & (close > close.rolling(60).mean())
    elif family == "Chaikin零轴":
        value = ema(adl(frame), 3) - ema(adl(frame), 10)
        bottom = cross_up(value, 0.0) & (close < close.rolling(60).mean())
        top = cross_down(value, 0.0) & (close > close.rolling(60).mean())
    elif family == "MFI退出极值":
        value = money_flow(frame, p["period"])
        bottom = cross_up(value, 20) & (value.shift() < 20)
        top = cross_down(value, 80) & (value.shift() > 80)
    elif family == "Elder力量零轴":
        value = ema(close.diff() * volume, p["period"])
        bottom = cross_up(value, 0.0) & (close < close.rolling(60).mean())
        top = cross_down(value, 0.0) & (close > close.rolling(60).mean())
    elif family == "ElderRay转折":
        average = ema(close, p["period"])
        bull = high - average
        bear = low - average
        bottom = (average > average.shift()) & (bear < 0) & (bear > bear.shift()) & (bear.shift() <= bear.shift(2))
        top = (average < average.shift()) & (bull > 0) & (bull < bull.shift()) & (bull.shift() >= bull.shift(2))
    elif family == "吞没形态":
        body = (close - open_).abs()
        long_body = body > body.rolling(20).mean()
        rising = close > close.rolling(20).mean()
        falling = close < close.rolling(20).mean()
        bottom = falling & long_body & (close > open_) & (close.shift() < open_.shift()) & (open_ <= close.shift()) & (close >= open_.shift())
        top = rising & long_body & (close < open_) & (close.shift() > open_.shift()) & (open_ >= close.shift()) & (close <= open_.shift())
    elif family == "锤子与流星":
        body = (close - open_).abs()
        lower_shadow = pd.concat([open_, close], axis=1).min(axis=1) - low
        upper_shadow = high - pd.concat([open_, close], axis=1).max(axis=1)
        average = close.rolling(20).mean()
        bottom = (close < average) & (lower_shadow >= 2 * body) & (upper_shadow <= body)
        top = (close > average) & (upper_shadow >= 2 * body) & (lower_shadow <= body)
    elif family == "晨星与昏星":
        body = (close - open_).abs()
        average_body = body.rolling(20).mean()
        first_long = body.shift(2) > average_body.shift(2)
        small_middle = body.shift(1) < 0.3 * average_body.shift(1)
        midpoint = (open_.shift(2) + close.shift(2)) / 2
        bottom = first_long & small_middle & (close.shift(2) < open_.shift(2)) & (close > open_) & (close > midpoint)
        top = first_long & small_middle & (close.shift(2) > open_.shift(2)) & (close < open_) & (close < midpoint)
    elif family == "刺透与乌云":
        body = (close - open_).abs()
        long_body = body.shift() > body.rolling(20).mean().shift()
        midpoint = (open_.shift() + close.shift()) / 2
        bottom = long_body & (close.shift() < open_.shift()) & (open_ < low.shift()) & (close > midpoint) & (close < open_.shift())
        top = long_body & (close.shift() > open_.shift()) & (open_ > high.shift()) & (close < midpoint) & (close > open_.shift())
    elif family == "十字突破":
        body = (close - open_).abs()
        spread = high - low
        doji = (body <= 0.1 * spread) & (spread > 0.5 * atr(frame))
        bottom = (close > high.shift()) & doji.shift().fillna(False) & (close < close.rolling(20).mean())
        top = (close < low.shift()) & doji.shift().fillna(False) & (close > close.rolling(20).mean())
    elif family == "RSI背离":
        bottom, top = divergence_masks(frame, rsi(close), p["lookback"])
    elif family == "MACD背离":
        dif, _ = macd(close)
        bottom, top = divergence_masks(frame, dif, p["lookback"])
    elif family == "RSRS择时":
        value = rsrs(frame, p["period"], p["normalization"])
        bottom = cross_up(value, p["threshold"])
        top = cross_down(value, -p["threshold"])
    elif family == "PSY心理线":
        value = 100 * (close > close.shift()).rolling(p["period"]).mean()
        bottom = cross_up(value, 25) & (value.shift() < 25)
        top = cross_down(value, 75) & (value.shift() > 75)
    elif family == "ARBR情绪":
        period = p["period"]
        ar = 100 * (high - open_).rolling(period).sum() / (open_ - low).rolling(period).sum().replace(0, np.nan)
        br = 100 * (high - close.shift()).clip(lower=0).rolling(period).sum() / (close.shift() - low).clip(lower=0).rolling(period).sum().replace(0, np.nan)
        ar_high = ar.rolling(252, min_periods=60).quantile(0.95)
        ar_low = ar.rolling(252, min_periods=60).quantile(0.05)
        signal = br.rolling(5).mean()
        bottom = (ar <= ar_low) & cross_up(br, signal)
        top = (ar >= ar_high) & cross_down(br, signal)
    elif family == "Supertrend":
        direction = supertrend_direction(frame, p["period"], p["multiplier"])
        bottom = (direction > 0) & (direction.shift() <= 0)
        top = (direction <= 0) & (direction.shift() > 0)
    elif family == "DMA趋势":
        dma = close.rolling(p["short"]).mean() - close.rolling(p["long"]).mean()
        ama = dma.rolling(10).mean()
        bull = (dma > 0) & (ama > 0) & (dma > dma.shift()) & (ama > ama.shift())
        bear = (dma < 0) & (ama < 0) & (dma < dma.shift()) & (ama < ama.shift())
        bottom = bull & ~bull.shift().fillna(False)
        top = bear & ~bear.shift().fillna(False)
    elif family == "长期均线阶段":
        average = close.rolling(p["period"]).mean()
        slope = average.diff(5)
        bottom = cross_up(close, average) & (slope > 0)
        top = cross_down(close, average) & (slope < 0)
    elif family == "多指标极值投票":
        average = close.rolling(20).mean()
        deviation = close.rolling(20).std(ddof=0)
        percent_b = (close - (average - 2 * deviation)) / (4 * deviation).replace(0, np.nan)
        bias = close / average - 1
        bias_low = bias.rolling(252, min_periods=60).quantile(0.05)
        bias_high = bias.rolling(252, min_periods=60).quantile(0.95)
        value = rsi(close)
        bottom_score = (value < 30).astype(int) + (bias < bias_low).astype(int) + (percent_b < 0).astype(int) + (close < close.rolling(60).mean()).astype(int)
        top_score = (value > 70).astype(int) + (bias > bias_high).astype(int) + (percent_b > 1).astype(int) + (close > close.rolling(60).mean()).astype(int)
        bottom = (bottom_score >= p["votes"]) & (bottom_score.shift() < p["votes"])
        top = (top_score >= p["votes"]) & (top_score.shift() < p["votes"])
    elif family == "追随日":
        days_since_low = low.rolling(20).apply(lambda x: len(x) - 1 - np.argmin(x), raw=True)
        bottom = (days_since_low >= 4) & (days_since_low <= 7) & (close.pct_change() >= 0.015) & (volume > volume.shift())
    else:
        raise ValueError(f"未知候选算法：{family}")
    return bottom.fillna(False), top.fillna(False)


def candidate_points(frame: pd.DataFrame, spec: dict) -> dict[str, list[tuple[int, int]]]:
    family = spec["family"]
    p = spec["params"]
    lookup = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(frame["date"])}
    records = None
    if family == "因果ZigZag":
        records = zigzag_signals(frame, amplitude=p["amplitude"], min_gap=p["min_gap"])
    elif family == "Donchian拐点":
        records = donchian_signals(frame, window=p["window"], confirm_days=p["confirm_days"])
    elif family == "分形拐点":
        records = fractal_signals(frame, k=p["k"])
    if records is not None:
        result = {kind: [] for kind in KINDS}
        for record in records:
            detection = lookup[record["date"]]
            reference = lookup[record["pivot_date"]]
            result[record["type"]].append((detection, reference))
        return result
    bottom, top = candidate_masks(frame, spec)
    return {
        kind: [(int(i), int(i)) for i in np.flatnonzero(mask.to_numpy())]
        for kind, mask in (("bottom", bottom), ("top", top))
    }


def confirm_candidates(frame: pd.DataFrame, state: dict, candidates: list[tuple[int, int]],
                       kind: str, config: dict, cooldown: int = 20,
                       audit: list[dict] | None = None) -> list[dict]:
    output = []
    last_candidate = None
    last_signal = None
    for detection, reference in sorted(candidates):
        if last_candidate is not None and detection - last_candidate <= cooldown:
            if audit is not None:
                audit.append({"detection_date": frame["date"].iat[detection].strftime("%Y-%m-%d"), "type": kind, "reason": "candidate_cooldown", "confirmation": []})
            continue
        last_candidate = detection
        log = []
        confirmed = None
        for offset in range(1, 4):
            position = detection + offset
            if position >= len(frame):
                break
            layers = layer_state(
                frame, state["context"], state["breadth_delta5"], position,
                kind, reference, {"confirm_window": 3, **config},
            )
            log.append({
                "date": frame["date"].iat[position].strftime("%Y-%m-%d"),
                "offset_from_detection": offset,
                "price": bool(layers["price"]),
                "momentum": bool(layers["momentum"]),
                "volume": bool(layers["volume"]),
                "passed_count": int(sum(bool(layers[key]) for key in ("price", "momentum", "volume"))),
            })
            if all(bool(layers[key]) for key in ("price", "momentum", "volume")):
                confirmed = position
                break
        if confirmed is None:
            if audit is not None:
                audit.append({"detection_date": frame["date"].iat[detection].strftime("%Y-%m-%d"), "candidate_date": frame["date"].iat[reference].strftime("%Y-%m-%d"), "type": kind, "reason": "three_layer_unconfirmed", "confirmation": log})
            continue
        if last_signal is not None and confirmed - last_signal <= cooldown:
            if audit is not None:
                audit.append({"detection_date": frame["date"].iat[detection].strftime("%Y-%m-%d"), "candidate_date": frame["date"].iat[reference].strftime("%Y-%m-%d"), "type": kind, "reason": "signal_cooldown", "confirmation": log})
            continue
        record = {
            "date": frame["date"].iat[confirmed].strftime("%Y-%m-%d"),
            "type": kind,
            "price": float(frame["close"].iat[confirmed]),
            "detection_date": frame["date"].iat[detection].strftime("%Y-%m-%d"),
            "candidate_date": frame["date"].iat[reference].strftime("%Y-%m-%d"),
            "confirm_offset": int(confirmed - reference),
            "confirmation": log,
        }
        output.append(record)
        if audit is not None:
            audit.append({"detection_date": frame["date"].iat[detection].strftime("%Y-%m-%d"), "candidate_date": frame["date"].iat[reference].strftime("%Y-%m-%d"), "type": kind, "reason": "confirmed", "signal_date": record["date"], "confirmation": log})
        last_signal = confirmed
    return output


def candidate_specs() -> list[dict]:
    specs = []

    def add(family: str, **params) -> None:
        specs.append({"family": family, "params": params})

    for period in (6, 14, 24):
        add("RSI退出极值", period=period, lower=30, upper=70)
    for period in (9, 14, 21):
        add("随机指标交叉", period=period)
        add("N日极值反转", period=period if period != 9 else 10)
    for period in (9, 14):
        add("KDJ交叉", period=period)
        add("MFI退出极值", period=period + 5)
    for period in (14, 28):
        add("WilliamsR退出极值", period=period)
    for period, level in ((20, 100), (20, 200), (40, 100)):
        add("CCI退出极值", period=period, level=level)
    for period, width in ((20, 2.0), (20, 2.5), (60, 2.0)):
        add("布林回归", period=period, width=width)
        add("Keltner回归", period=period, width=width, atr_period=10)
    for period, level in ((20, 2.0), (60, 2.0), (60, 2.5)):
        add("ZScore回归", period=period, level=level)
    for zero_filter in (False, True):
        add("MACD交叉", zero_filter=zero_filter)
        add("MACD与RSI", zero_filter=zero_filter, rsi=45)
    for period in (5, 10, 20):
        add("ROC极值反转", period=period)
    for short, long in ((5, 60), (10, 60), (20, 120)):
        add("均线回抽", short=short, long=long)
        add("双均线交叉", short=short, long=long)
        add("DMA趋势", short=short, long=long)
    for threshold in (20, 25):
        add("DMI与ADX", period=14, adx=threshold)
    for step, maximum in ((0.02, 0.20), (0.01, 0.10)):
        add("抛物线SAR", step=step, maximum=maximum)
    for period in (10, 20):
        add("KAMA反转", period=period, fast=2, slow=30)
        add("ATR冲击反转", breakout=period)
        add("历史波动率跃迁", period=period)
        add("OBV趋势反转", period=period)
        add("BIAS分位回归", period=period)
        add("CMF零轴", period=period)
        add("Elder力量零轴", period=period)
        add("ElderRay转折", period=period)
    add("追随日")
    for period in (10, 20, 60):
        add("Donchian反转", period=period)
    for period in (14, 25, 50):
        add("Aroon交叉", period=period)
    add("一目均衡表")
    for period in (20, 60):
        add("回归斜率反转", period=period)
        add("Ulcer修复", period=period)
        add("PSY心理线", period=period if period != 20 else 12)
        add("ARBR情绪", period=period if period != 20 else 26)
    add("KST交叉")
    for long, short in ((25, 13), (40, 20)):
        add("TSI交叉", long=long, short=short, signal=7)
    add("终极振荡器")
    add("MassIndex反转")
    add("Chaikin零轴")
    for family in ("吞没形态", "锤子与流星", "晨星与昏星", "刺透与乌云", "十字突破"):
        add(family)
    for lookback in (60, 120):
        add("RSI背离", lookback=lookback)
        add("MACD背离", lookback=lookback)
    for period, normalization in ((18, 600), (18, 300), (16, 300)):
        add("RSRS择时", period=period, normalization=normalization, threshold=0.7)
    for period, multiplier in ((10, 3.0), (14, 2.0), (14, 3.0)):
        add("Supertrend", period=period, multiplier=multiplier)
    for period in (60, 150):
        add("长期均线阶段", period=period)
    for votes in (3, 4):
        add("多指标极值投票", votes=votes)
    for amplitude, min_gap in ((0.03, 8), (0.05, 8), (0.05, 20), (0.08, 20), (0.08, 30), (0.12, 30)):
        add("因果ZigZag", amplitude=amplitude, min_gap=min_gap)
    for window, days in ((20, 3), (60, 5)):
        add("Donchian拐点", window=window, confirm_days=days)
    for k in (2, 3, 5):
        add("分形拐点", k=k)
    return specs


def spec_id(spec: dict) -> str:
    params = json.dumps(spec["params"], ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return f"{spec['family']}[{params}]"
