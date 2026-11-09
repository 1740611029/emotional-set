"""新旧算法对照与信号复核记录；只写研究目录，不改生产 JSON。"""
from __future__ import annotations

import json
from pathlib import Path

from confirmed_index_signals import (
    LEGACY_ALGORITHM_VERSION, evaluate_signals, legacy_accuracy, legacy_confirmed_signals,
)
from fetch_market import INDEXES, INDEX_RAW
from research_algorithms import read_index_csv, read_sector_csv
from swing_indicators import breadth_table
from swing_signal_engine import ALGORITHM_VERSION, swing_details, swing_signals

ROOT = Path(__file__).resolve().parents[1]
SECTOR_RAW = ROOT / "data" / "raw" / "sectors"
OUTPUT = ROOT / "data" / "research" / f"swing-vs-legacy-{ALGORITHM_VERSION}.json"


def load_breadth():
    files = sorted((SECTOR_RAW / "first").glob("*.csv"))
    return breadth_table({path.stem: read_sector_csv(path) for path in files})


def summarize(result: dict) -> dict:
    events = [event for part in result["segments"] for event in part["events"]]
    summary = {}
    for kind in ("bottom", "top"):
        part = [event for event in events if event["type"] == kind]
        resolved = [event for event in part if event["status"] in {"success", "failure"}]
        summary[kind] = {
            "triggers": len(part), "resolved": len(resolved),
            "successes": sum(event["status"] == "success" for event in resolved),
            "rate": (sum(event["status"] == "success" for event in resolved) / len(resolved))
            if resolved else None,
        }
    return summary


def main() -> None:
    breadth = load_breadth()
    report = {
        "algorithm_version": ALGORITHM_VERSION,
        "legacy_version": LEGACY_ALGORITHM_VERSION,
        "note": "同区间、同口径对照；旧算法不输出回撤字段，故仅并列胜率与样本量。",
        "indices": [],
        "review": [],
    }
    for code, name in INDEXES.items():
        frame = read_index_csv(INDEX_RAW / f"{code}.csv")
        legacy_signals = legacy_confirmed_signals(frame)
        new_signals = swing_signals(frame, breadth)
        legacy_result = evaluate_signals(frame, legacy_signals)
        new_result = evaluate_signals(frame, new_signals)
        details = swing_details(frame, breadth)
        report["indices"].append({
            "code": code, "name": name,
            "legacy": {"signals": int(legacy_signals.ne("").sum()), "summary": summarize(legacy_result),
                       "accuracy": legacy_accuracy(legacy_result)},
            "swing": {"signals": len(details), "summary": summarize(new_result),
                      "accuracy": legacy_accuracy(new_result)},
        })
        for detail in details[-3:]:
            report["review"].append({
                "code": code, "name": name, "signal_date": detail["date"],
                "candidate_date": detail["candidate_date"], "type": detail["type"],
                "grade": detail["grade"], "score": detail["score"],
                "factors": detail["factors"], "breadth": detail["breadth"],
                "confirmation": detail["confirmation"],
            })
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    total_legacy = sum(row["legacy"]["signals"] for row in report["indices"])
    total_swing = sum(row["swing"]["signals"] for row in report["indices"])
    print(f"旧算法信号 {total_legacy} 个，新算法 {total_swing} 个")
    print(f"已写入 {OUTPUT}")


if __name__ == "__main__":
    main()
