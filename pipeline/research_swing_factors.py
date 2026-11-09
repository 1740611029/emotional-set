"""离线因子研究：训练段选参、验证段评估，冻结参数后写入研究目录。

本脚本只写 `data/research/`，不触碰生产 JSON。选参只在训练段（2020-10-30~2024-12-31）
进行；验证段（2025-01-01 起）只用于报告，不参与任何参数选择。
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd

from confirmed_index_signals import evaluate_signals
from fetch_market import INDEXES, INDEX_RAW
from research_algorithms import read_index_csv, read_sector_csv, wilson_interval
from swing_indicators import breadth_table
from swing_signal_engine import ALGORITHM_VERSION, DEFAULT_PARAMS, swing_details, swing_signals

ROOT = Path(__file__).resolve().parents[1]
SECTOR_RAW = ROOT / "data" / "raw" / "sectors"
OUTPUT = ROOT / "data" / "research" / f"swing-factors-{ALGORITHM_VERSION}.json"
TRAIN_END = pd.Timestamp("2024-12-31")
MIN_RESOLVED = 20
MIN_INDEX_EDGE = 4

GRID = [
    {"core_extreme": core, "score_threshold": score}
    for core in (0.55, 0.60, 0.65)
    for score in (0.50, 0.52, 0.55)
]


def input_hash() -> str:
    digest = hashlib.sha256()
    for path in sorted(INDEX_RAW.glob("*.csv")) + sorted((SECTOR_RAW / "first").glob("*.csv")):
        digest.update(path.name.encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


def load_breadth() -> pd.DataFrame:
    files = sorted((SECTOR_RAW / "first").glob("*.csv"))
    if len(files) < 20:
        raise ValueError("申万一级行业文件不足 20 个")
    return breadth_table({path.stem: read_sector_csv(path) for path in files})


def segment_events(result: dict, segment: str) -> list[dict]:
    return [event for part in result["segments"] if part["segment"] == segment for event in part["events"]]


def summarize(events: list[dict]) -> dict:
    summary = {}
    for kind in ("bottom", "top"):
        part = [event for event in events if event["type"] == kind]
        resolved = [event for event in part if event["status"] in {"success", "failure"}]
        successes = sum(event["status"] == "success" for event in resolved)
        summary[kind] = {
            "triggers": len(part), "resolved": len(resolved), "successes": successes,
            "rate": successes / len(resolved) if resolved else None,
            "wilson95": list(wilson_interval(successes, len(resolved))),
            "avg_max_adverse_excursion": float(np.mean([event["max_adverse_excursion_20"]
                                                        for event in resolved]))
            if resolved and all(event["max_adverse_excursion_20"] is not None for event in resolved) else None,
            "avg_forward_return_20": float(np.mean([event["forward_return_20"] for event in resolved]))
            if resolved and all(event["forward_return_20"] is not None for event in resolved) else None,
        }
    return summary


def selection_score(summary: dict) -> float:
    """取底部与顶部 Wilson 下界的较小值；样本不足直接淘汰。"""
    bounds = []
    for kind in ("bottom", "top"):
        part = summary[kind]
        if part["resolved"] < MIN_RESOLVED:
            return -1.0
        low = part["wilson95"][0]
        if low is None:
            return -1.0
        bounds.append(float(low))
    return min(bounds)


def index_edge(result: dict, frame: pd.DataFrame, segment: str) -> bool:
    """信号 20 日前瞻收益是否高于同段全样本均值（至少一个方向成立）。"""
    part = next((row for row in result["segments"] if row["segment"] == segment), None)
    if part is None:
        return False
    forward = frame["close"].shift(-20) / frame["close"] - 1
    dates = frame["date"]
    mask = dates <= TRAIN_END if segment == "research" else dates > TRAIN_END
    baseline = float(forward[mask].mean())
    if not np.isfinite(baseline):
        return False
    for kind in ("bottom", "top"):
        values = [event["forward_return_20"] for event in part["events"]
                  if event["type"] == kind and event["forward_return_20"] is not None]
        if len(values) >= 2 and float(np.mean(values)) > baseline:
            return True
    return False


def run(grid: list[dict]) -> dict:
    breadth = load_breadth()
    frames = {code: read_index_csv(INDEX_RAW / f"{code}.csv") for code in INDEXES}
    rows = []
    for params in grid:
        train_events, validation_events = [], []
        edge_indices = 0
        per_index = []
        for code, frame in frames.items():
            result = evaluate_signals(frame, swing_signals(frame, breadth, params))
            train_events.extend(segment_events(result, "research"))
            validation_events.extend(segment_events(result, "exploratory_validation"))
            edge = index_edge(result, frame, "research")
            edge_indices += int(edge)
            per_index.append({"code": code, "edge": edge,
                              "signals": len(swing_details(frame, breadth, params))})
        train_summary = summarize(train_events)
        score = selection_score(train_summary)
        rows.append({
            "params": {**DEFAULT_PARAMS, **params},
            "train": train_summary,
            "validation": summarize(validation_events),
            "selection_score": round(score, 6),
            "edge_indices": edge_indices,
            "per_index": per_index,
            "selected": False,
            "eliminated": None if score >= 0 and edge_indices >= MIN_INDEX_EDGE else (
                "训练段样本不足" if score < 0 else f"跨指数一致性不足（{edge_indices}/7）"),        })
    eligible = [row for row in rows if row["eliminated"] is None]
    chosen = max(eligible, key=lambda row: (row["selection_score"], -row["params"]["score_threshold"])) \
        if eligible else max(rows, key=lambda row: row["selection_score"])
    chosen["selected"] = True
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "input_sha256": input_hash(),
        "train_range": ["2020-10-30", TRAIN_END.strftime("%Y-%m-%d")],
        "validation_range": ["2025-01-01", "最新交易日"],
        "embargo_days": 20,
        "selection_rule": f"min(底部/顶部 Wilson 下界)，已判定样本 <{MIN_RESOLVED} 淘汰；"
                          f"训练段至少 {MIN_INDEX_EDGE}/7 个指数的 20 日前瞻收益高于同期全样本均值",
        "edge_metric": "20 日前瞻收益相对同期全样本均值的超额，不是胜率差",
        "frozen_params": chosen["params"],
        "candidates": rows,
        "research_status": "post_hoc_exploratory_not_independent",
        "note": "参数只在训练段确定；验证段结果仅作报告，未回炉调参。",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=OUTPUT)
    args = parser.parse_args()
    payload = run(GRID)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False),
                           encoding="utf-8")
    chosen = payload["frozen_params"]
    print(f"选定参数：core_extreme={chosen['core_extreme']} score_threshold={chosen['score_threshold']}")
    for row in payload["candidates"]:
        mark = "*" if row["selected"] else " "
        print(f"{mark} core={row['params']['core_extreme']} score={row['params']['score_threshold']} "
              f"train={row['train']['bottom']['rate']} / {row['train']['top']['rate']} "
              f"score={row['selection_score']} edge={row['edge_indices']} {row['eliminated'] or ''}")
    print(f"已写入 {args.output}")


if __name__ == "__main__":
    main()
