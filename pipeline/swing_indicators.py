"""波段顶底信号的正交因子计算；纯计算，不做 I/O，不使用任何未来数据。

六个方向（每个方向输出 0-1 得分，得分越高表示越接近该方向的极端）：
position / divergence / volume / volatility / trend / breadth

所有滚动窗口只向前看：T 日的得分只依赖 T 日及之前的数据。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_algorithms import dmi, macd, rsi

DIRECTIONS = ("position", "divergence", "volume", "volatility", "trend", "breadth")
CORE_DIRECTIONS = ("position", "breadth", "volume")
KINDS = ("bottom", "top")
RANK_WINDOW = 250
RANK_MIN_PERIODS = 60
CORE_EXTREME = 0.85
MIN_BREADTH_SECTORS = 20


def rolling_percentile(series: pd.Series, window: int = RANK_WINDOW,
                       min_periods: int = RANK_MIN_PERIODS) -> pd.Series:
    """当前值在"自身及之前 window 个观测"中的分位（0-1），严格向前看。"""
    return series.rolling(window, min_periods=min_periods).apply(
        lambda values: float((values <= values[-1]).mean()), raw=True
    )


def _extremeness(rank: pd.Series) -> pd.Series:
    """把分位映射为"离中位数多远"（0-1），用于天量/地量、高波动/低波动。"""
    return (2 * rank - 1).abs()


def _true_range(frame: pd.DataFrame) -> pd.Series:
    previous = frame["close"].shift()
    return pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous).abs(),
        (frame["low"] - previous).abs(),
    ], axis=1).max(axis=1)


def atr(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    return _true_range(frame).ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def adx(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    plus, minus = dmi(frame, period)
    total = (plus + minus).replace(0, np.nan)
    dx = 100 * (plus - minus).abs() / total
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def obv(frame: pd.DataFrame) -> pd.Series:
    return (np.sign(frame["close"].diff()).fillna(0) * frame["volume"]).cumsum()


def mfi(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    raw = typical * frame["volume"]
    direction = typical.diff()
    positive = raw.where(direction > 0, 0.0).rolling(period).sum()
    negative = raw.where(direction < 0, 0.0).rolling(period).sum()
    value = 100 - 100 / (1 + positive / negative.replace(0, np.nan))
    return value.mask((negative == 0) & (positive > 0), 100)


def price_position(frame: pd.DataFrame) -> pd.DataFrame:
    """价格位置方向：回撤/涨幅分位与超买超卖指标合并为同一个方向。"""
    close = frame["close"]
    drawdown = close / close.rolling(250, min_periods=120).max() - 1
    runup = close / close.rolling(250, min_periods=120).min() - 1
    rsi14 = rsi(close)
    ma20 = close.rolling(20).mean()
    deviation = close.rolling(20).std(ddof=0)
    percent_b = (close - (ma20 - 2 * deviation)) / (4 * deviation).replace(0, np.nan)
    zscore = (close - ma20) / deviation.replace(0, np.nan)

    bottom = pd.concat([
        1 - rolling_percentile(drawdown),
        1 - rolling_percentile(rsi14),
        1 - rolling_percentile(percent_b),
        1 - rolling_percentile(zscore),
    ], axis=1).mean(axis=1, skipna=False)
    top = pd.concat([
        rolling_percentile(runup),
        rolling_percentile(rsi14),
        rolling_percentile(percent_b),
        rolling_percentile(zscore),
    ], axis=1).mean(axis=1, skipna=False)
    return pd.DataFrame({"position_bottom": bottom, "position_top": top}, index=frame.index)


def divergence(frame: pd.DataFrame) -> pd.DataFrame:
    """动量背离：价格创新低（高），但 RSI / MACD 柱 / OBV 未同步创新低（高）。"""
    close = frame["close"].to_numpy(dtype=float)
    dif, dea = macd(frame["close"])
    series = {
        "rsi": rsi(frame["close"]).to_numpy(dtype=float),
        "hist": (2 * (dif - dea)).to_numpy(dtype=float),
        "obv": obv(frame).to_numpy(dtype=float),
    }
    total = len(close)
    bottom = np.zeros(total)
    top = np.zeros(total)
    last_low = None
    last_high = None
    for position in range(20, total):
        window = close[position - 19:position + 1]
        is_low = close[position] <= window.min() + 1e-12
        is_high = close[position] >= window.max() - 1e-12
        if is_low:
            if last_low is not None and position - last_low <= 60:
                hits = sum(
                    np.isfinite(series[key][position]) and np.isfinite(series[key][last_low])
                    and series[key][position] > series[key][last_low]
                    for key in series
                )
                bottom[position] = hits / 3
            last_low = position
        if is_high:
            if last_high is not None and position - last_high <= 60:
                hits = sum(
                    np.isfinite(series[key][position]) and np.isfinite(series[key][last_high])
                    and series[key][position] < series[key][last_high]
                    for key in series
                )
                top[position] = hits / 3
            last_high = position
    return pd.DataFrame({"divergence_bottom": bottom, "divergence_top": top}, index=frame.index)


def volume_state(frame: pd.DataFrame) -> pd.DataFrame:
    """量能极值：量比离中位数越远越极端（天量或地量），叠加 OBV 方向。"""
    ratio = frame["volume"] / frame["volume"].rolling(20).mean()
    extreme = _extremeness(rolling_percentile(ratio))
    slope = obv(frame).diff(5)
    align_bottom = (slope > 0).astype(float).where(slope.notna())
    align_top = (slope < 0).astype(float).where(slope.notna())
    bottom = 0.7 * extreme + 0.3 * align_bottom
    top = 0.7 * extreme + 0.3 * align_top
    return pd.DataFrame({"volume_bottom": bottom, "volume_top": top}, index=frame.index)


def volatility_state(frame: pd.DataFrame) -> pd.DataFrame:
    """波动状态：ATR 与布林带宽的极端程度（恐慌放大或极度收敛）。"""
    atr_rank = rolling_percentile(atr(frame) / frame["close"])
    ma20 = frame["close"].rolling(20).mean()
    bandwidth = 4 * frame["close"].rolling(20).std(ddof=0) / ma20
    width_rank = rolling_percentile(bandwidth)
    score = 0.6 * _extremeness(atr_rank) + 0.4 * _extremeness(width_rank)
    return pd.DataFrame({"volatility_bottom": score, "volatility_top": score}, index=frame.index)


def trend_state(frame: pd.DataFrame) -> pd.DataFrame:
    """趋势状态：长期均线位置 + 中期斜率是否放缓 + ADX 强度，并输出否决信号。"""
    close = frame["close"]
    ma60 = close.rolling(60).mean()
    ma250 = close.rolling(250, min_periods=120).mean()
    slope = ma60.diff(5) / ma60.shift(5)
    previous = slope.shift(5)
    adx14 = adx(frame)
    adx_rank = rolling_percentile(adx14)

    below_long = (close < ma250).astype(float).where(ma250.notna())
    above_long = (close > ma250).astype(float).where(ma250.notna())
    decel_down = (slope > previous).astype(float).where(slope.notna())
    decel_up = (slope < previous).astype(float).where(slope.notna())
    weak_trend = 1 - adx_rank

    bottom = 0.45 * below_long + 0.35 * decel_down + 0.20 * weak_trend
    top = 0.45 * above_long + 0.35 * decel_up + 0.20 * weak_trend
    veto_bottom = (slope < 0) & (slope < previous) & (adx_rank > 0.7)
    veto_top = (slope > 0) & (slope > previous) & (adx_rank > 0.7)
    return pd.DataFrame({
        "trend_bottom": bottom, "trend_top": top,
        "veto_bottom": veto_bottom.fillna(False), "veto_top": veto_top.fillna(False),
    }, index=frame.index)


def breadth_table(sector_frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """按交易日计算行业板块（东财行业板块单层清单）的横截面宽度；只使用当日及此前数据。"""
    closes = pd.concat(
        {code: frame.set_index("date")["close"] for code, frame in sector_frames.items()},
        axis=1, sort=True,
    ).sort_index()
    ma20 = closes.rolling(20, min_periods=20).mean()
    ma60 = closes.rolling(60, min_periods=60).mean()
    valid20 = ma20.notna().sum(axis=1)
    valid60 = ma60.notna().sum(axis=1)
    breadth20 = (closes > ma20).sum(axis=1) / valid20.replace(0, np.nan)
    breadth60 = (closes > ma60).sum(axis=1) / valid60.replace(0, np.nan)
    result = pd.DataFrame({
        "breadth20": breadth20.where(valid20 >= MIN_BREADTH_SECTORS),
        "breadth60": breadth60.where(valid60 >= MIN_BREADTH_SECTORS),
        "sector_count": valid20,
    })
    result["breadth_delta5"] = result["breadth20"].diff(5)
    return result


def breadth_scores(breadth: pd.DataFrame | None, dates: pd.Series,
                   index: pd.Index) -> pd.DataFrame:
    """把宽度表对齐到指数交易日并转换为方向得分；缺失时整列为 NaN。"""
    if breadth is None or breadth.empty:
        empty = pd.Series(np.nan, index=index)
        return pd.DataFrame({"breadth_bottom": empty, "breadth_top": empty.copy(),
                             "breadth20": empty.copy(), "breadth_delta5": empty.copy()})
    aligned = breadth.reindex(pd.DatetimeIndex(dates))
    aligned.index = index
    level = rolling_percentile(aligned["breadth20"])
    delta = aligned["breadth_delta5"]
    bottom = 0.7 * (1 - level) + 0.3 * (delta / 0.30).clip(0, 1)
    top = 0.7 * level + 0.3 * (-delta / 0.30).clip(0, 1)
    return pd.DataFrame({
        "breadth_bottom": bottom.where(aligned["breadth20"].notna()),
        "breadth_top": top.where(aligned["breadth20"].notna()),
        "breadth20": aligned["breadth20"],
        "breadth_delta5": delta,
    }, index=index)


def factor_table(frame: pd.DataFrame, breadth: pd.DataFrame | None = None) -> pd.DataFrame:
    """合成六方向得分、合成分数与核心闸门；缺失方向按可用权重归一。"""
    parts = [
        price_position(frame), divergence(frame), volume_state(frame),
        volatility_state(frame), trend_state(frame),
        breadth_scores(breadth, frame["date"], frame.index),
    ]
    result = pd.concat(parts, axis=1)
    for kind in KINDS:
        columns = [f"{name}_{kind}" for name in DIRECTIONS]
        weights = pd.DataFrame({column: result[column].notna().astype(float) for column in columns})
        denominator = weights.sum(axis=1)
        weighted = sum(result[column].fillna(0.0) * weights[column] for column in columns)
        result[f"score_{kind}"] = (weighted / denominator.replace(0, np.nan)).clip(0, 1)
        core_columns = [f"{name}_{kind}" for name in CORE_DIRECTIONS]
        core_available = weights[core_columns].sum(axis=1)
        core_hits = sum((result[column] >= CORE_EXTREME).astype(int) for column in core_columns)
        result[f"core_{kind}"] = core_hits
        result[f"core_ok_{kind}"] = (core_hits >= 2) & (core_available >= 2)
    return result
