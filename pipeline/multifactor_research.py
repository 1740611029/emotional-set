"""第二轮探索：独立多因子、弃权与嵌套前向验证；不产生生产信号。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_algorithms import (
    RESEARCH_START, deduplicate, index_signals, ma_reversal_masks, macd, rsi,
    summarize_index_events, wilson_interval,
)

CANDIDATES = ["mf_score_4", "mf_score_3"]
BASELINES = ["RSI14", "MACD+RSI"]
FOLD_YEARS = [2023, 2024, 2025, 2026]
EMBARGO_DAYS = 20


def money_flow_index(frame: pd.DataFrame, period: int = 14) -> pd.Series:
    typical = (frame["high"] + frame["low"] + frame["close"]) / 3
    raw = typical * frame["volume"]
    direction = typical.diff()
    positive = raw.where(direction > 0, 0.0).rolling(period).sum()
    negative = raw.where(direction < 0, 0.0).rolling(period).sum()
    value = 100 - 100 / (1 + positive / negative.replace(0, np.nan))
    return value.mask((negative == 0) & (positive > 0), 100)


def multifactor_indicators(frame: pd.DataFrame) -> pd.DataFrame:
    """暴露既有回测的逐日指标供抽样核对；只读取当日及此前数据。"""
    close = frame["close"]
    returns = close.pct_change()
    ma5 = close.rolling(5).mean()
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    deviation20 = close.rolling(20).std(ddof=0)
    zscore20 = (close - ma20) / deviation20.replace(0, np.nan)
    percent_b = (close - (ma20 - 2 * deviation20)) / (4 * deviation20).replace(0, np.nan)
    rsi14 = rsi(close)
    realized20 = returns.rolling(20).std(ddof=0)
    volatility_baseline = realized20.rolling(60).median()
    obv = (np.sign(close.diff()).fillna(0) * frame["volume"]).cumsum()
    mfi = money_flow_index(frame)
    return pd.DataFrame({
        "ma5": ma5, "ma10": close.rolling(10).mean(), "ma20": ma20,
        "ma30": close.rolling(30).mean(), "ma60": ma60,
        "std20": deviation20, "zscore20": zscore20, "percent_b": percent_b,
        "rsi14": rsi14, "realized20": realized20,
        "volatility_baseline": volatility_baseline,
        "drawdown20": close / close.rolling(20).max() - 1,
        "runup20": close / close.rolling(20).min() - 1,
        "obv": obv, "obv_change3": obv.diff(3), "mfi14": mfi,
        "mfi_change1": mfi.diff(),
    }, index=frame.index)


def _candidate_signals(
    frame: pd.DataFrame, threshold: int, *, trigger_mode: str = "original",
    participation_mode: str = "or",
) -> pd.Series:
    """生成候选信号（预测层），内部使用，不直接对外展示。"""
    if threshold not in {3, 4}:
        raise ValueError("多因子研究阈值只允许 3 或 4")
    if trigger_mode not in {"original", "ma5", "ma5_slope"}:
        raise ValueError(f"未知触发模式：{trigger_mode}")
    if participation_mode not in {"or", "and"}:
        raise ValueError(f"未知参与层模式：{participation_mode}")
    indicators = multifactor_indicators(frame)
    close = frame["close"]
    ma5, ma20, ma60 = (indicators[key] for key in ("ma5", "ma20", "ma60"))
    rsi14 = indicators["rsi14"]
    percent_b = indicators["percent_b"]
    zscore20 = indicators["zscore20"]
    high_volatility = indicators["realized20"] > indicators["volatility_baseline"]

    state_bottom = (close < ma60) & ((ma20 < ma60) | high_volatility)
    state_top = (close > ma60) & ((ma20 > ma60) | high_volatility)

    drawdown20 = indicators["drawdown20"]
    runup20 = indicators["runup20"]
    exhaustion_bottom = (
        (rsi14 < 35).astype(int) + (percent_b < 0.2).astype(int)
        + (drawdown20 < -0.08).astype(int) + (zscore20 < -1.5).astype(int)
    ) >= 2
    exhaustion_top = (
        (rsi14 > 65).astype(int) + (percent_b > 0.8).astype(int)
        + (runup20 > 0.08).astype(int) + (zscore20 > 1.5).astype(int)
    ) >= 2

    obv_slope = indicators["obv_change3"]
    mfi_slope = indicators["mfi_change1"]
    if participation_mode == "and":
        participation_bottom = (obv_slope > 0) & (mfi_slope > 0)
        participation_top = (obv_slope < 0) & (mfi_slope < 0)
    else:
        participation_bottom = (obv_slope > 0) | (mfi_slope > 0)
        participation_top = (obv_slope < 0) | (mfi_slope < 0)
    participation_bottom_opposed = (obv_slope < 0) & (mfi_slope < 0)
    participation_top_opposed = (obv_slope > 0) & (mfi_slope > 0)

    if trigger_mode == "original":
        trigger_bottom = (close > close.shift()) & ((close > ma5) | (close > frame["open"]))
        trigger_top = (close < close.shift()) & ((close < ma5) | (close < frame["open"]))
    else:
        trigger_bottom = (close > close.shift()) & (close > ma5)
        trigger_top = (close < close.shift()) & (close < ma5)
        if trigger_mode == "ma5_slope":
            trigger_bottom &= ma5 > ma5.shift()
            trigger_top &= ma5 < ma5.shift()

    bottom_score = (
        state_bottom.astype(int) + exhaustion_bottom.astype(int)
        + participation_bottom.astype(int) + trigger_bottom.astype(int)
    )
    top_score = (
        state_top.astype(int) + exhaustion_top.astype(int)
        + participation_top.astype(int) + trigger_top.astype(int)
    )
    bottom = (bottom_score >= threshold) & ~participation_bottom_opposed & trigger_bottom
    top = (top_score >= threshold) & ~participation_top_opposed & trigger_top
    signals = pd.Series("", index=frame.index, dtype="string")
    signals[deduplicate(bottom)] = "bottom"
    signals[deduplicate(top)] = "top"
    return signals


def _check_confirmation(
    frame: pd.DataFrame, candidate_idx: int, kind: str,
    confirmation_window: int = 3,
) -> int | None:
    """检查候选信号是否在确认窗口期内满足确认条件。

    返回确认日的索引，如果未确认则返回 None。
    """
    close = frame["close"]
    open_ = frame["open"]
    indicators = multifactor_indicators(frame)
    ma5 = indicators["ma5"]
    obv_slope = indicators["obv_change3"]
    mfi_slope = indicators["mfi_change1"]

    for offset in range(1, confirmation_window + 1):
        idx = candidate_idx + offset
        if idx >= len(frame):
            break

        if kind == "bottom":
            # 价格确认：收盘价 > MA5
            price_ok = close.iloc[idx] > ma5.iloc[idx]
            # 动量确认：阳线
            momentum_ok = close.iloc[idx] > open_.iloc[idx]
            # 量能确认：OBV三日变化 > 0 或 MFI14一日变化 > 0
            volume_ok = (obv_slope.iloc[idx] > 0) | (mfi_slope.iloc[idx] > 0)
        else:  # top
            price_ok = close.iloc[idx] < ma5.iloc[idx]
            momentum_ok = close.iloc[idx] < open_.iloc[idx]
            volume_ok = (obv_slope.iloc[idx] < 0) | (mfi_slope.iloc[idx] < 0)

        if price_ok and momentum_ok and volume_ok:
            return idx

    return None


def candidate_signals(
    frame: pd.DataFrame, threshold: int, *, trigger_mode: str = "original",
    participation_mode: str = "or",
) -> pd.Series:
    """公开只读别名：供候选状态导出复用，不改变既有调用方与确认结果。"""
    return _candidate_signals(
        frame, threshold,
        trigger_mode=trigger_mode, participation_mode=participation_mode,
    )


def multifactor_signals(
    frame: pd.DataFrame, threshold: int, *, trigger_mode: str = "original",
    participation_mode: str = "or",
) -> pd.Series:
    """保留原函数签名，作为候选信号生成器（向后兼容）。"""
    return _candidate_signals(
        frame, threshold,
        trigger_mode=trigger_mode, participation_mode=participation_mode,
    )


def confirmed_multifactor_signals(
    frame: pd.DataFrame, threshold: int, *, trigger_mode: str = "original",
    participation_mode: str = "or",
    confirmation_window: int = 3,
) -> pd.Series:
    """确认型多因子信号：候选信号后需经确认条件验证才生效。"""
    # 生成候选信号
    candidates = _candidate_signals(
        frame, threshold,
        trigger_mode=trigger_mode, participation_mode=participation_mode,
    )

    # 逐一对候选信号进行确认
    confirmed = pd.Series("", index=frame.index, dtype="string")
    candidate_positions = {
        "bottom": np.flatnonzero(candidates == "bottom"),
        "top": np.flatnonzero(candidates == "top"),
    }

    for kind in ("bottom", "top"):
        for pos in candidate_positions[kind]:
            confirmation_idx = _check_confirmation(
                frame, pos, kind, confirmation_window=confirmation_window,
            )
            if confirmation_idx is not None:
                confirmed.iloc[confirmation_idx] = kind

    return confirmed


def evaluate_window(
    frame: pd.DataFrame, signals: pd.Series, *, code: str, name: str, method: str,
    signal_start: pd.Timestamp, signal_end: pd.Timestamp, label_end: pd.Timestamp,
) -> list[dict]:
    bull_success, bear_success = ma_reversal_masks(frame)
    label_positions = np.flatnonzero((frame["date"] <= label_end).to_numpy())
    if not len(label_positions):
        return []
    label_position = int(label_positions[-1])
    positions = {kind: np.flatnonzero((signals == kind).to_numpy()) for kind in ("bottom", "top")}
    events = []
    for kind in ("bottom", "top"):
        opposite = positions["top" if kind == "bottom" else "bottom"]
        success_mask = bull_success if kind == "bottom" else bear_success
        for position in positions[kind]:
            day = frame["date"].iat[position]
            if day < max(RESEARCH_START, signal_start) or day > signal_end or position >= label_position:
                continue
            later = opposite[opposite > position]
            opposite_position = int(later[0]) if len(later) else None
            search_end = min(opposite_position, label_position + 1) if opposite_position is not None else label_position + 1
            confirmation = None
            for candidate in range(position + 1, search_end):
                if bool(success_mask.iat[candidate]):
                    confirmation = candidate
                    break
            if confirmation is not None:
                status, resolved = "success", confirmation
            elif opposite_position is not None and opposite_position <= label_position:
                status, resolved = "failure", opposite_position
            else:
                status, resolved = "pending", None
            events.append({
                "method": method, "code": code, "name": name, "type": kind,
                "date": day.strftime("%Y-%m-%d"), "year": int(day.year),
                "segment": f"walk_forward_{day.year}", "status": status,
                "confirmation_date": frame["date"].iat[resolved].strftime("%Y-%m-%d") if resolved is not None else None,
                "confirmation_days": resolved - position if resolved is not None else None,
            })
    return events


def training_score(events: list[dict]) -> float:
    if not events:
        return -1.0
    frame = pd.DataFrame(events)
    resolved = frame[frame["status"].isin(["success", "failure"])]
    if len(resolved) < 20:
        return -1.0
    scores = []
    for kind in ("bottom", "top"):
        part = resolved[resolved["type"] == kind]
        if len(part) < 5:
            return -1.0
        successes = int((part["status"] == "success").sum())
        scores.append(wilson_interval(successes, len(part))[0])
    return float(min(scores))


def apply_embargo(events: list[dict], frames: dict, fold_start: pd.Timestamp) -> list[dict]:
    cutoffs = {}
    for code, frame in frames.items():
        start_positions = np.flatnonzero((frame["date"] >= fold_start).to_numpy())
        if len(start_positions):
            embargo_position = max(0, int(start_positions[0]) - EMBARGO_DAYS)
            cutoffs[code] = frame["date"].iat[embargo_position]
    return [
        event for event in events
        if pd.Timestamp(event["date"]) < cutoffs.get(event["code"], fold_start)
    ]


def run_walk_forward(frames: dict, names: dict) -> tuple[list[dict], list[dict], list[dict]]:
    signal_cache = {}
    for method in CANDIDATES + BASELINES:
        for code, frame in frames.items():
            if method.startswith("mf_score_"):
                threshold = int(method.rsplit("_", 1)[1])
                signal_cache[method, code] = multifactor_signals(frame, threshold)
            else:
                signal_cache[method, code] = index_signals(frame, method)

    selected_events = []
    baseline_events = []
    fold_rows = []
    for year in FOLD_YEARS:
        fold_start = pd.Timestamp(f"{year}-01-01")
        fold_end = pd.Timestamp(f"{year}-12-31")
        training = {}
        validation = {}
        for method in CANDIDATES:
            training[method] = []
            validation[method] = []
            for code, frame in frames.items():
                training[method].extend(evaluate_window(
                    frame, signal_cache[method, code], code=code, name=names[code], method=method,
                    signal_start=RESEARCH_START, signal_end=fold_start - pd.Timedelta(days=1),
                    label_end=fold_start - pd.Timedelta(days=1),
                ))
                validation[method].extend(evaluate_window(
                    frame, signal_cache[method, code], code=code, name=names[code], method=method,
                    signal_start=fold_start, signal_end=fold_end, label_end=fold_end,
                ))
        scores = {}
        for method in CANDIDATES:
            embargoes = apply_embargo(training[method], frames, fold_start)
            scores[method] = training_score(embargoes)
        selected = max(CANDIDATES, key=lambda method: (scores[method], int(method.rsplit("_", 1)[1])))
        if scores[selected] < 0:
            selected = None
        if selected is not None:
            for event in validation[selected]:
                event["selected_for_fold"] = year
                selected_events.append(event)
        fold_rows.append({
            "year": year, "selected": selected, "training_scores": scores,
            "embargo_days": EMBARGO_DAYS, "pending_training_samples_excluded_from_score": True,
            "validation_status": "exploratory_not_unseen",
        })
        for method in BASELINES:
            for code, frame in frames.items():
                baseline_events.extend(evaluate_window(
                    frame, signal_cache[method, code], code=code, name=names[code], method=method,
                    signal_start=fold_start, signal_end=fold_end, label_end=fold_end,
                ))
        print(f"第二轮前向折完成：{year}，选择 {selected}")
    return selected_events, baseline_events, fold_rows


def summarize_multifactor(events: list[dict], baselines: list[dict], folds: list[dict]) -> dict:
    def aggregated(rows: list[dict]) -> list[dict]:
        return [{**row, "segment": "walk_forward_all"} for row in rows]

    return {
        "multifactor": {
            "overall": summarize_index_events(aggregated(events), []),
            "by_fold": summarize_index_events(events, []),
            "by_index": summarize_index_events(aggregated(events), ["code", "name"]),
            "by_year": summarize_index_events(events, ["year"]),
        },
        "baselines": {
            "overall": summarize_index_events(aggregated(baselines), ["method"]),
            "by_year": summarize_index_events(baselines, ["method", "year"]),
        },
        "folds": folds,
    }


def advanced_data_review() -> dict:
    return {
        "user_decision": "允许由项目选择多因子方向并补充可靠的历史成分股和市场宽度数据",
        "breadth_ingested": False,
        "reason": "当前环境未提供已验证的逐日历史成分股序列；禁止用当前名单倒推历史",
        "installed_akshare_index_stock_hist_available": False,
        "required_controls": [
            "逐日生效的历史成分名单与调整日期", "包含后来退市证券的历史行情",
            "统一前复权或明确的原始价口径", "停牌日不伪造上涨或下跌",
            "宽度特征只能使用信号日收盘前已可获得的数据",
        ],
        "research_directions": {
            "multi_horizon_trend": "现有日线可做滚动状态过滤",
            "volume_price": "MFI/OBV 已纳入降级探索版",
            "market_breadth": "等待可靠 point-in-time 数据，不以当前成分替代",
            "volatility_change_point": "可后续比较 CUSUM/BOCPD，须在线估计",
            "hmm": "只能滚动拟合，不允许全样本回看状态",
            "machine_learning": "当前环境无 sklearn；先完成规则基线，不能为追求复杂度直接新增黑箱",
        },
    }
