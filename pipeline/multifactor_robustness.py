"""冻结候选的留一指数、同日聚类、未决账龄与市场状态研究。"""
from __future__ import annotations

import numpy as np
import pandas as pd

from multifactor_followup import CANDIDATES, aggregated
from multifactor_research import (
    FOLD_YEARS, apply_embargo, evaluate_window, multifactor_signals,
    training_score,
)
from research_algorithms import RESEARCH_START, summarize_index_events

FROZEN_CANDIDATE = "mf4_participation"


def market_regimes(frame: pd.DataFrame) -> pd.DataFrame:
    close = frame["close"]
    returns = close.pct_change()
    realized = returns.rolling(20).std(ddof=0)
    baseline = realized.rolling(60).median()
    high = realized > baseline
    volatility = pd.Series("unknown", index=frame.index, dtype="string")
    available = realized.notna() & baseline.notna()
    volatility[available & ~high] = "normal_volatility"
    volatility[available & high] = "high_volatility"
    ma20 = close.rolling(20).mean()
    ma60 = close.rolling(60).mean()
    trend = pd.Series("unknown", index=frame.index, dtype="string")
    trend[(close > ma60) & (ma20 > ma60)] = "uptrend"
    trend[(close <= ma60) & (ma20 <= ma60)] = "downtrend"
    trend[(close > ma60) & (ma20 <= ma60)] = "price_above_ma60_mixed"
    trend[(close <= ma60) & (ma20 > ma60)] = "price_below_ma60_mixed"
    result = pd.DataFrame({"volatility_regime": volatility, "trend_regime": trend})
    result.index = pd.DatetimeIndex(frame["date"])
    return result


def enrich_regimes(events: list[dict], frames: dict) -> list[dict]:
    regimes = {code: market_regimes(frame) for code, frame in frames.items()}
    enriched = []
    for event in events:
        row = regimes[event["code"]].loc[pd.Timestamp(event["date"])]
        enriched.append({
            **event,
            "volatility_regime": str(row["volatility_regime"]),
            "trend_regime": str(row["trend_regime"]),
        })
    return enriched


def cluster_diagnostics(events: list[dict]) -> list[dict]:
    rows = []
    years = [None] + sorted({event["year"] for event in events})
    for year in years:
        for kind in ("bottom", "top"):
            part = [
                event for event in events
                if event["type"] == kind and (year is None or event["year"] == year)
            ]
            by_date = {}
            for event in part:
                by_date.setdefault(event["date"], []).append(event)
            date_rates = []
            resolved_dates = 0
            for date_events in by_date.values():
                resolved = [event for event in date_events if event["status"] in {"success", "failure"}]
                if resolved:
                    resolved_dates += 1
                    date_rates.append(sum(event["status"] == "success" for event in resolved) / len(resolved))
            multi_dates = sum(len(date_events) > 1 for date_events in by_date.values())
            multi_events = sum(len(date_events) for date_events in by_date.values() if len(date_events) > 1)
            rows.append({
                "year": year, "type": kind, "total": len(part),
                "unique_signal_dates": len(by_date),
                "resolved_signal_dates": resolved_dates,
                "multi_index_signal_dates": multi_dates,
                "multi_index_event_share": multi_events / len(part) if part else None,
                "max_same_day_indices": max((len(value) for value in by_date.values()), default=0),
                "date_equal_weighted_success_rate": float(np.mean(date_rates)) if date_rates else None,
                "note": "同日指数信号可能来自同一波行情，不能当作完全独立样本",
            })
    return rows


def pending_diagnostics(events: list[dict], frames: dict) -> list[dict]:
    positions = {
        code: {day: position for position, day in enumerate(frame["date"])}
        for code, frame in frames.items()
    }
    rows = []
    for kind in ("bottom", "top"):
        part = [event for event in events if event["type"] == kind]
        pending = [event for event in part if event["status"] == "pending"]
        ages = []
        for event in pending:
            frame_positions = positions[event["code"]]
            signal_day = pd.Timestamp(event["date"])
            if signal_day in frame_positions:
                ages.append(len(frame_positions) - 1 - frame_positions[signal_day])
        rows.append({
            "type": kind, "total": len(part), "pending": len(pending),
            "pending_share": len(pending) / len(part) if part else None,
            "pending_older_than_20_trading_days": sum(age > 20 for age in ages),
            "pending_older_than_60_trading_days": sum(age > 60 for age in ages),
            "median_pending_trading_days": float(np.median(ages)) if ages else None,
            "max_pending_trading_days": max(ages, default=0),
            "failures_caused_by_opposite_signal": sum(event["status"] == "failure" for event in part),
        })
    return rows


def run_robustness(frames: dict, names: dict) -> dict:
    signal_cache = {
        (method, code): multifactor_signals(frame, **spec)
        for method, spec in CANDIDATES.items()
        for code, frame in frames.items()
    }
    fixed_strict = []
    fixed_continuous = []
    loio_strict = []
    loio_continuous = []
    selections = []
    global_end = max(frame["date"].max() for frame in frames.values())
    for year in FOLD_YEARS:
        fold_start = pd.Timestamp(f"{year}-01-01")
        fold_end = pd.Timestamp(f"{year}-12-31")
        training = {}
        strict = {}
        continuous = {}
        for method in CANDIDATES:
            for code, frame in frames.items():
                common = {"code": code, "name": names[code], "method": method}
                training[method, code] = evaluate_window(
                    frame, signal_cache[method, code], **common,
                    signal_start=RESEARCH_START,
                    signal_end=fold_start - pd.Timedelta(days=1),
                    label_end=fold_start - pd.Timedelta(days=1),
                )
                strict[method, code] = evaluate_window(
                    frame, signal_cache[method, code], **common,
                    signal_start=fold_start, signal_end=fold_end, label_end=fold_end,
                )
                continuous[method, code] = evaluate_window(
                    frame, signal_cache[method, code], **common,
                    signal_start=fold_start, signal_end=fold_end, label_end=global_end,
                )
        for code in frames:
            fixed_strict.extend(strict[FROZEN_CANDIDATE, code])
            fixed_continuous.extend(continuous[FROZEN_CANDIDATE, code])
        for heldout in frames:
            other_frames = {code: frame for code, frame in frames.items() if code != heldout}
            scores = {}
            for method in CANDIDATES:
                events = [
                    event for code in other_frames
                    for event in training[method, code]
                ]
                scores[method] = training_score(apply_embargo(events, other_frames, fold_start))
            selected = max(CANDIDATES, key=lambda method: (scores[method], method))
            if scores[selected] < 0:
                selected = None
            selections.append({
                "year": year, "heldout_code": heldout, "heldout_name": names[heldout],
                "selected": selected, "training_scores": scores,
                "validation_status": "post_hoc_exploratory_not_unseen",
            })
            if selected is not None:
                for event in strict[selected, heldout]:
                    loio_strict.append({**event, "heldout_code": heldout, "selected_for_fold": year})
                for event in continuous[selected, heldout]:
                    loio_continuous.append({**event, "heldout_code": heldout, "selected_for_fold": year})
        print(f"留一指数前向折完成：{year}")
    return {
        "fixed_strict": fixed_strict,
        "fixed_continuous": fixed_continuous,
        "loio_strict": loio_strict,
        "loio_continuous": loio_continuous,
        "selections": selections,
    }


def summarize_robustness(result: dict, frames: dict) -> dict:
    fixed_strict = enrich_regimes(result["fixed_strict"], frames)
    fixed_continuous = enrich_regimes(result["fixed_continuous"], frames)
    loio_strict = result["loio_strict"]
    loio_continuous = result["loio_continuous"]
    return {
        "fixed_candidate": {
            "method": FROZEN_CANDIDATE,
            "strict_overall": summarize_index_events(aggregated(fixed_strict), []),
            "continuous_overall": summarize_index_events(aggregated(fixed_continuous), []),
            "by_index": summarize_index_events(aggregated(fixed_continuous), ["code", "name"]),
            "by_year": summarize_index_events(fixed_continuous, ["year"]),
            "by_volatility": summarize_index_events(aggregated(fixed_continuous), ["volatility_regime"]),
            "by_trend": summarize_index_events(aggregated(fixed_continuous), ["trend_regime"]),
            "same_day_clusters": cluster_diagnostics(fixed_continuous),
            "pending": pending_diagnostics(fixed_continuous, frames),
        },
        "leave_one_index_out": {
            "strict_overall": summarize_index_events(aggregated(loio_strict), []),
            "continuous_overall": summarize_index_events(aggregated(loio_continuous), []),
            "by_index": summarize_index_events(aggregated(loio_continuous), ["code", "name"]),
            "by_year": summarize_index_events(loio_continuous, ["year"]),
            "selections": result["selections"],
        },
    }
