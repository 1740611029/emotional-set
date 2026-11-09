"""多因子后续对照研究：固定少量因果变体，不改写第二轮旧结果。"""
from __future__ import annotations

import pandas as pd

from multifactor_research import (
    BASELINES, EMBARGO_DAYS, FOLD_YEARS, apply_embargo, evaluate_window,
    multifactor_signals, training_score,
)
from research_algorithms import (
    RESEARCH_START, index_signals, summarize_index_events,
)

CANDIDATES = {
    "mf4_original": {"threshold": 4, "trigger_mode": "original", "participation_mode": "or"},
    "mf4_reclaim": {"threshold": 4, "trigger_mode": "ma5", "participation_mode": "or"},
    "mf4_reclaim_slope": {"threshold": 4, "trigger_mode": "ma5_slope", "participation_mode": "or"},
    "mf4_participation": {"threshold": 4, "trigger_mode": "original", "participation_mode": "and"},
    "mf4_reclaim_participation": {"threshold": 4, "trigger_mode": "ma5", "participation_mode": "and"},
}


def aggregated(events: list[dict]) -> list[dict]:
    return [{**event, "segment": "walk_forward_all"} for event in events]


def run_followup(frames: dict, names: dict) -> dict:
    signal_cache = {}
    for method, spec in CANDIDATES.items():
        for code, frame in frames.items():
            signal_cache[method, code] = multifactor_signals(frame, **spec)
    for method in BASELINES:
        for code, frame in frames.items():
            signal_cache[method, code] = index_signals(frame, method)

    candidate_events = {method: [] for method in CANDIDATES}
    continuous_candidate_events = {method: [] for method in CANDIDATES}
    selected_events = []
    continuous_selected_events = []
    baseline_events = []
    continuous_baseline_events = []
    global_end = max(frame["date"].max() for frame in frames.values())
    folds = []
    for year in FOLD_YEARS:
        fold_start = pd.Timestamp(f"{year}-01-01")
        fold_end = pd.Timestamp(f"{year}-12-31")
        training = {method: [] for method in CANDIDATES}
        validation = {method: [] for method in CANDIDATES}
        continuous_validation = {method: [] for method in CANDIDATES}
        for method in CANDIDATES:
            for code, frame in frames.items():
                common = {
                    "code": code, "name": names[code], "method": method,
                }
                training[method].extend(evaluate_window(
                    frame, signal_cache[method, code], **common,
                    signal_start=RESEARCH_START,
                    signal_end=fold_start - pd.Timedelta(days=1),
                    label_end=fold_start - pd.Timedelta(days=1),
                ))
                validation[method].extend(evaluate_window(
                    frame, signal_cache[method, code], **common,
                    signal_start=fold_start, signal_end=fold_end, label_end=fold_end,
                ))
                continuous_validation[method].extend(evaluate_window(
                    frame, signal_cache[method, code], **common,
                    signal_start=fold_start, signal_end=fold_end, label_end=global_end,
                ))
        scores = {
            method: training_score(apply_embargo(training[method], frames, fold_start))
            for method in CANDIDATES
        }
        selected = max(CANDIDATES, key=lambda method: (scores[method], method))
        if scores[selected] < 0:
            selected = None
        for method in CANDIDATES:
            candidate_events[method].extend(validation[method])
            continuous_candidate_events[method].extend(continuous_validation[method])
        if selected is not None:
            for event in validation[selected]:
                event["selected_for_fold"] = year
                selected_events.append(event)
            for event in continuous_validation[selected]:
                event["selected_for_fold"] = year
                continuous_selected_events.append(event)
        for method in BASELINES:
            for code, frame in frames.items():
                baseline_events.extend(evaluate_window(
                    frame, signal_cache[method, code], code=code, name=names[code], method=method,
                    signal_start=fold_start, signal_end=fold_end, label_end=fold_end,
                ))
                continuous_baseline_events.extend(evaluate_window(
                    frame, signal_cache[method, code], code=code, name=names[code], method=method,
                    signal_start=fold_start, signal_end=fold_end, label_end=global_end,
                ))
        folds.append({
            "year": year, "selected": selected, "training_scores": scores,
            "embargo_days": EMBARGO_DAYS,
            "pending_training_samples_excluded_from_score": True,
            "validation_status": "post_hoc_exploratory_not_unseen",
        })
        print(f"后续前向折完成：{year}，选择 {selected}")
    return {
        "candidate_events": candidate_events,
        "continuous_candidate_events": continuous_candidate_events,
        "selected_events": selected_events,
        "continuous_selected_events": continuous_selected_events,
        "baseline_events": baseline_events,
        "continuous_baseline_events": continuous_baseline_events,
        "folds": folds,
    }


def summarize_followup(result: dict) -> dict:
    candidates = {}
    for method, events in result["candidate_events"].items():
        candidates[method] = {
            "overall": summarize_index_events(aggregated(events), []),
            "by_year": summarize_index_events(events, ["year"]),
        }
    continuous_candidates = {}
    for method, events in result["continuous_candidate_events"].items():
        continuous_candidates[method] = {
            "overall": summarize_index_events(aggregated(events), []),
            "by_year": summarize_index_events(events, ["year"]),
        }
    selected = result["selected_events"]
    continuous_selected = result["continuous_selected_events"]
    baselines = result["baseline_events"]
    continuous_baselines = result["continuous_baseline_events"]
    return {
        "candidates": candidates,
        "continuous_candidates": continuous_candidates,
        "selected": {
            "overall": summarize_index_events(aggregated(selected), []),
            "by_fold": summarize_index_events(selected, []),
            "by_index": summarize_index_events(aggregated(selected), ["code", "name"]),
            "by_year": summarize_index_events(selected, ["year"]),
        },
        "continuous_selected": {
            "overall": summarize_index_events(aggregated(continuous_selected), []),
            "by_fold": summarize_index_events(continuous_selected, []),
            "by_index": summarize_index_events(aggregated(continuous_selected), ["code", "name"]),
            "by_year": summarize_index_events(continuous_selected, ["year"]),
        },
        "baselines": {
            "overall": summarize_index_events(aggregated(baselines), ["method"]),
            "by_year": summarize_index_events(baselines, ["method", "year"]),
        },
        "continuous_baselines": {
            "overall": summarize_index_events(aggregated(continuous_baselines), ["method"]),
            "by_year": summarize_index_events(continuous_baselines, ["method", "year"]),
        },
        "folds": result["folds"],
    }
