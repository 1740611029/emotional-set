"""只读算法研究：指标、候选信号、波段标签与方向验证，不接入生产输出。"""
from __future__ import annotations

import math
from pathlib import Path

import numpy as np
import pandas as pd

INDEX_METHODS = [
    "RSI14", "KDJ9", "WilliamsR14", "CCI20", "BOLL20", "Zscore60",
    "MACD12_26_9", "ROC5 reversal", "20d extreme", "MA5_60 pullback",
    "RSI+BOLL", "MACD+RSI",
]
SECTOR_METHODS = [
    "MA10_30", "MA20_60", "MACD", "ROC20_60", "Regression20_60",
    "DMI14_MA60", "Donchian20_MA60", "Aroon25_MA60",
    "Supertrend10_3_MA60", "RelativeMomentum20_60", "CompositeVote",
]
RESEARCH_START = pd.Timestamp("2020-10-30")
RESEARCH_END = pd.Timestamp("2024-12-31")


def ema(series: pd.Series, span: int) -> pd.Series:
    return series.ewm(span=span, adjust=False).mean()


def rsi(series: pd.Series, period: int = 14) -> pd.Series:
    delta = series.diff()
    gains = delta.clip(lower=0).ewm(alpha=1 / period, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1 / period, adjust=False).mean()
    return 100 * gains / (gains + losses)


def kdj(frame: pd.DataFrame, period: int = 9) -> tuple[pd.Series, pd.Series, pd.Series]:
    low = frame["low"].rolling(period).min()
    high = frame["high"].rolling(period).max()
    spread = (high - low).replace(0, np.nan)
    rsv = ((frame["close"] - low) / spread * 100).fillna(50)
    k_values = []
    d_values = []
    k_value = 50.0
    d_value = 50.0
    for value in rsv:
        k_value = 2 / 3 * k_value + 1 / 3 * value
        d_value = 2 / 3 * d_value + 1 / 3 * k_value
        k_values.append(k_value)
        d_values.append(d_value)
    k = pd.Series(k_values, index=frame.index).where(high.notna())
    d = pd.Series(d_values, index=frame.index).where(high.notna())
    return k, d, 3 * k - 2 * d


def macd(series: pd.Series) -> tuple[pd.Series, pd.Series]:
    dif = ema(series, 12) - ema(series, 26)
    return dif, ema(dif, 9)


def rolling_slope(series: pd.Series, window: int) -> pd.Series:
    values = np.log(series)
    x = np.arange(window, dtype=float)
    centered = x - x.mean()
    denominator = float((centered ** 2).sum())
    numerator = values.rolling(window).apply(lambda y: float(np.dot(y, centered)), raw=True)
    return numerator / denominator


def dmi(frame: pd.DataFrame, period: int = 14) -> tuple[pd.Series, pd.Series]:
    up = frame["high"].diff()
    down = -frame["low"].diff()
    plus_dm = pd.Series(np.where((up > down) & (up > 0), up, 0.0), index=frame.index)
    minus_dm = pd.Series(np.where((down > up) & (down > 0), down, 0.0), index=frame.index)
    previous_close = frame["close"].shift()
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    plus = 100 * plus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    minus = 100 * minus_dm.ewm(alpha=1 / period, adjust=False, min_periods=period).mean() / atr
    return plus, minus


def supertrend_direction(frame: pd.DataFrame, period: int = 10, multiplier: float = 3) -> pd.Series:
    previous_close = frame["close"].shift()
    true_range = pd.concat([
        frame["high"] - frame["low"],
        (frame["high"] - previous_close).abs(),
        (frame["low"] - previous_close).abs(),
    ], axis=1).max(axis=1)
    atr = true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()
    midpoint = (frame["high"] + frame["low"]) / 2
    basic_upper = midpoint + multiplier * atr
    basic_lower = midpoint - multiplier * atr
    final_upper = basic_upper.copy()
    final_lower = basic_lower.copy()
    direction = pd.Series(np.nan, index=frame.index)
    for i in range(1, len(frame)):
        if not np.isfinite(atr.iat[i]):
            continue
        final_upper.iat[i] = basic_upper.iat[i] if (
            basic_upper.iat[i] < final_upper.iat[i - 1] or frame["close"].iat[i - 1] > final_upper.iat[i - 1]
        ) else final_upper.iat[i - 1]
        final_lower.iat[i] = basic_lower.iat[i] if (
            basic_lower.iat[i] > final_lower.iat[i - 1] or frame["close"].iat[i - 1] < final_lower.iat[i - 1]
        ) else final_lower.iat[i - 1]
        if not np.isfinite(direction.iat[i - 1]):
            direction.iat[i] = float(frame["close"].iat[i] >= final_lower.iat[i])
        elif direction.iat[i - 1] == 0:
            direction.iat[i] = float(frame["close"].iat[i] > final_upper.iat[i])
        else:
            direction.iat[i] = float(frame["close"].iat[i] >= final_lower.iat[i])
    return direction


def deduplicate(mask: pd.Series, cooldown: int = 20) -> pd.Series:
    result = pd.Series(False, index=mask.index)
    last = None
    for position in np.flatnonzero(mask.fillna(False).to_numpy()):
        if last is None or position - last > cooldown:
            result.iat[position] = True
            last = position
    return result


def index_signals(frame: pd.DataFrame, method: str) -> pd.Series:
    close = frame["close"]
    result = pd.Series("", index=frame.index, dtype="string")
    bottom = pd.Series(False, index=frame.index)
    top = pd.Series(False, index=frame.index)
    if method == "RSI14":
        value = rsi(close)
        bottom = (value >= 30) & (value.shift() < 30)
        top = (value <= 70) & (value.shift() > 70)
    elif method == "KDJ9":
        k, d, _ = kdj(frame)
        bottom = (k > d) & (k.shift() <= d.shift()) & (d.shift() < 20)
        top = (k < d) & (k.shift() >= d.shift()) & (d.shift() > 80)
    elif method == "WilliamsR14":
        high = frame["high"].rolling(14).max()
        low = frame["low"].rolling(14).min()
        value = -100 * (high - close) / (high - low).replace(0, np.nan)
        bottom = (value >= -80) & (value.shift() < -80)
        top = (value <= -20) & (value.shift() > -20)
    elif method == "CCI20":
        typical = (frame["high"] + frame["low"] + close) / 3
        mean = typical.rolling(20).mean()
        deviation = typical.rolling(20).apply(lambda x: np.mean(np.abs(x - x.mean())), raw=True)
        value = (typical - mean) / (0.015 * deviation.replace(0, np.nan))
        bottom = (value >= -100) & (value.shift() < -100)
        top = (value <= 100) & (value.shift() > 100)
    elif method in {"BOLL20", "RSI+BOLL"}:
        mean = close.rolling(20).mean()
        deviation = close.rolling(20).std(ddof=0)
        lower = mean - 2 * deviation
        upper = mean + 2 * deviation
        bottom = (close >= lower) & (close.shift() < lower.shift())
        top = (close <= upper) & (close.shift() > upper.shift())
        if method == "RSI+BOLL":
            value = rsi(close)
            bottom &= value < 40
            top &= value > 60
    elif method == "Zscore60":
        value = (close - close.rolling(60).mean()) / close.rolling(60).std(ddof=0).replace(0, np.nan)
        bottom = (value >= -2) & (value.shift() < -2)
        top = (value <= 2) & (value.shift() > 2)
    elif method in {"MACD12_26_9", "MACD+RSI"}:
        dif, dea = macd(close)
        bottom = (dif > dea) & (dif.shift() <= dea.shift()) & ((dea.shift() if method == "MACD12_26_9" else dif) < 0)
        top = (dif < dea) & (dif.shift() >= dea.shift()) & ((dea.shift() if method == "MACD12_26_9" else dif) > 0)
        if method == "MACD+RSI":
            value = rsi(close)
            bottom &= value < 45
            top &= value > 55
    elif method == "ROC5 reversal":
        roc = close.pct_change(5) * 100
        bottom = (roc.shift() <= -8) & (close > close.shift())
        top = (roc.shift() >= 8) & (close < close.shift())
    elif method == "20d extreme":
        bottom = close <= close.rolling(20).min()
        top = close >= close.rolling(20).max()
    elif method == "MA5_60 pullback":
        short = close.rolling(5).mean()
        long = close.rolling(60).mean()
        bottom = (close > short) & (close.shift() <= short.shift()) & (close < long)
        top = (close < short) & (close.shift() >= short.shift()) & (close > long)
    else:
        raise ValueError(f"未知指数方法：{method}")
    bottom &= pd.Series(frame.index >= 60, index=frame.index)
    top &= pd.Series(frame.index >= 60, index=frame.index)
    result[deduplicate(bottom)] = "bottom"
    result[deduplicate(top)] = "top"
    return result


def ma_reversal_masks(frame: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    averages = [frame["close"].rolling(window).mean() for window in (5, 10, 20, 30)]
    bull_order = (averages[0] > averages[1]) & (averages[1] > averages[2]) & (averages[2] > averages[3])
    bear_order = (averages[0] < averages[1]) & (averages[1] < averages[2]) & (averages[2] < averages[3])
    all_up = pd.concat([average > average.shift() for average in averages], axis=1).all(axis=1)
    all_down = pd.concat([average < average.shift() for average in averages], axis=1).all(axis=1)
    return bull_order & all_up, bear_order & all_down


def evaluate_index_signals(
    frame: pd.DataFrame, signals: pd.Series, *, code: str, name: str, method: str,
) -> list[dict]:
    bull_success, bear_success = ma_reversal_masks(frame)
    positions = {kind: np.flatnonzero((signals == kind).to_numpy()) for kind in ("bottom", "top")}
    boundary = int(np.flatnonzero((frame["date"] <= RESEARCH_END).to_numpy())[-1])
    events = []
    for kind in ("bottom", "top"):
        opposite = positions["top" if kind == "bottom" else "bottom"]
        success_mask = bull_success if kind == "bottom" else bear_success
        for position in positions[kind]:
            day = frame["date"].iat[position]
            if day < RESEARCH_START:
                continue
            segment = "research" if day <= RESEARCH_END else "exploratory_validation"
            later_opposite = opposite[opposite > position]
            opposite_position = int(later_opposite[0]) if len(later_opposite) else None
            search_end = opposite_position if opposite_position is not None else len(frame)
            if segment == "research":
                search_end = min(search_end, boundary + 1)
            confirmation = None
            for candidate in range(position + 1, search_end):
                if bool(success_mask.iat[candidate]):
                    confirmation = candidate
                    break
            if confirmation is not None:
                status = "success"
                resolved_position = confirmation
            elif opposite_position is not None and (
                segment != "research" or opposite_position <= boundary
            ):
                status = "failure"
                resolved_position = opposite_position
            else:
                status = "pending"
                resolved_position = None
            events.append({
                "method": method, "code": code, "name": name, "type": kind,
                "date": day.strftime("%Y-%m-%d"), "year": int(day.year), "segment": segment,
                "status": status,
                "confirmation_date": frame["date"].iat[resolved_position].strftime("%Y-%m-%d") if resolved_position is not None else None,
                "confirmation_days": resolved_position - position if resolved_position is not None else None,
            })
    return events


def wilson_interval(successes: int, samples: int, z: float = 1.959963984540054) -> list[float | None]:
    if samples == 0:
        return [None, None]
    proportion = successes / samples
    denominator = 1 + z * z / samples
    centre = (proportion + z * z / (2 * samples)) / denominator
    margin = z * math.sqrt(proportion * (1 - proportion) / samples + z * z / (4 * samples * samples)) / denominator
    return [max(0.0, centre - margin), min(1.0, centre + margin)]


def summarize_index_events(events: list[dict], keys: list[str]) -> list[dict]:
    if not events:
        return []
    frame = pd.DataFrame(events)
    rows = []
    group_columns = keys + ["type", "segment"]
    for group, part in frame.groupby(group_columns, dropna=False):
        if not isinstance(group, tuple):
            group = (group,)
        successes = int((part["status"] == "success").sum())
        failures = int((part["status"] == "failure").sum())
        pending = int((part["status"] == "pending").sum())
        resolved = successes + failures
        row = dict(zip(group_columns, group))
        row.update({
            "total": len(part), "resolved": resolved, "successes": successes,
            "failures": failures, "pending": pending,
            "rate": successes / resolved if resolved else None,
            "wilson95": wilson_interval(successes, resolved),
            "small_sample": resolved < 30,
            "median_confirmation_days": float(part["confirmation_days"].dropna().median()) if resolved else None,
        })
        rows.append(row)
    return sorted(rows, key=lambda row: tuple(str(row[key]) for key in group_columns))


def sector_state(frame: pd.DataFrame, method: str, relative: dict[str, pd.Series] | None = None) -> pd.Series:
    close = frame["close"]
    dif, dea = macd(close)
    if method == "MA10_30":
        short, long = close > close.rolling(10).mean(), close.rolling(10).mean() > close.rolling(30).mean()
    elif method == "MA20_60":
        short, long = close > close.rolling(20).mean(), close.rolling(20).mean() > close.rolling(60).mean()
    elif method == "MACD":
        short, long = dif > dea, dif > 0
    elif method == "ROC20_60":
        short, long = close.pct_change(20) > 0, close.pct_change(60) > 0
    elif method == "Regression20_60":
        short, long = rolling_slope(close, 20) > 0, rolling_slope(close, 60) > 0
    elif method == "DMI14_MA60":
        plus, minus = dmi(frame)
        short, long = plus > minus, close > close.rolling(60).mean()
    elif method == "Donchian20_MA60":
        middle = (frame["high"].rolling(20).max() + frame["low"].rolling(20).min()) / 2
        short, long = close > middle, close > close.rolling(60).mean()
    elif method == "Aroon25_MA60":
        def since_high(values: np.ndarray) -> float:
            return len(values) - 1 - np.argmax(values)
        def since_low(values: np.ndarray) -> float:
            return len(values) - 1 - np.argmin(values)
        high_days = frame["high"].rolling(25).apply(since_high, raw=True)
        low_days = frame["low"].rolling(25).apply(since_low, raw=True)
        short, long = high_days < low_days, close > close.rolling(60).mean()
    elif method == "Supertrend10_3_MA60":
        short, long = supertrend_direction(frame) > 0, close > close.rolling(60).mean()
    elif method == "RelativeMomentum20_60":
        short = close.pct_change(20).to_numpy() > relative["20"].reindex(frame["date"]).to_numpy()
        long = close.pct_change(60).to_numpy() > relative["60"].reindex(frame["date"]).to_numpy()
    elif method == "CompositeVote":
        ma10, ma30 = close.rolling(10).mean(), close.rolling(30).mean()
        short = ((close > ma10).astype(int) + (close.pct_change(20) > 0).astype(int) + (dif > dea).astype(int)) >= 2
        long = ((ma10 > ma30).astype(int) + (close.pct_change(60) > 0).astype(int) + (dif > 0).astype(int)) >= 2
    else:
        raise ValueError(f"未知板块方法：{method}")
    valid = close.rolling(60).count() >= 60
    state = pd.Series(pd.NA, index=frame.index, dtype="string")
    state[valid & short & long] = "bull"
    state[valid & ~short & long] = "warn"
    state[valid & ~short & ~long] = "bear"
    state[valid & short & ~long] = "rise"
    return state


def relative_momentum_medians(frames: dict[str, pd.DataFrame]) -> dict[str, pd.DataFrame]:
    returns20 = pd.DataFrame({code: frame.set_index("date")["close"].pct_change(20) for code, frame in frames.items()})
    returns60 = pd.DataFrame({code: frame.set_index("date")["close"].pct_change(60) for code, frame in frames.items()})
    return {"20": returns20.median(axis=1), "60": returns60.median(axis=1)}


def evaluate_sector_states(
    frame: pd.DataFrame, state: pd.Series, *, code: str, name: str, method: str,
) -> list[dict]:
    entries = (state.notna() & state.ne(state.shift().fillna(""))).fillna(False)
    events = []
    for position in np.flatnonzero(entries.to_numpy()):
        day = frame["date"].iat[position]
        if day < RESEARCH_START:
            continue
        segment = "research" if day <= RESEARCH_END else "exploratory_validation"
        kind = state.iat[position]
        event = {
            "method": method, "code": code, "name": name, "type": kind,
            "date": day.strftime("%Y-%m-%d"), "year": int(day.year), "segment": segment,
        }
        for horizon in (10, 20):
            target = position + horizon
            allowed = target < len(frame)
            if segment == "research":
                allowed = allowed and frame["date"].iat[min(target, len(frame) - 1)] <= RESEARCH_END
            if not allowed:
                outcome = "pending"
            else:
                change = frame["close"].iat[target] / frame["close"].iat[position] - 1
                expected_up = kind in {"bull", "rise"}
                outcome = "success" if (change > 0 if expected_up else change < 0) else "failure"
            event[f"outcome_{horizon}"] = outcome
        events.append(event)
    return events


def summarize_sector_events(events: list[dict], keys: list[str]) -> list[dict]:
    if not events:
        return []
    frame = pd.DataFrame(events)
    rows = []
    for horizon in (10, 20):
        group_columns = keys + ["segment"]
        for group, part in frame.groupby(group_columns, dropna=False):
            if not isinstance(group, tuple):
                group = (group,)
            outcomes = part[f"outcome_{horizon}"]
            resolved = outcomes.isin(["success", "failure"])
            successes = int((outcomes == "success").sum())
            samples = int(resolved.sum())
            row = dict(zip(group_columns, group))
            row.update({
                "horizon": horizon, "entries": len(part), "samples": samples,
                "successes": successes, "pending": int((outcomes == "pending").sum()),
                "rate": successes / samples if samples else None,
                "wilson95": wilson_interval(successes, samples), "small_sample": samples < 30,
            })
            rows.append(row)
    return sorted(rows, key=lambda row: (row["horizon"],) + tuple(str(row[key]) for key in group_columns))


def read_index_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values("date").reset_index(drop=True)


def read_sector_csv(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path).rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
        "最低": "low", "成交量": "volume", "成交额": "amount",
    })
    frame["date"] = pd.to_datetime(frame["date"])
    return frame.sort_values("date").reset_index(drop=True)
