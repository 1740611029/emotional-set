"""运行只读算法回测并输出独立研究 JSON，不改生产 JSON 或原始行情。"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import tempfile
from pathlib import Path

import numpy as np

from fetch_market import INDEXES, INDEX_RAW
from research_algorithms import (
    INDEX_METHODS, SECTOR_METHODS, evaluate_index_signals, evaluate_sector_states,
    index_signals, read_index_csv, read_sector_csv, relative_momentum_medians,
    sector_state, summarize_index_events, summarize_sector_events,
)

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
OUTPUT = ROOT / "data" / "research"


def hash_raw_files() -> str:
    digest = hashlib.sha256()
    for path in sorted(item for item in RAW.rglob("*") if item.is_file()):
        digest.update(str(path.relative_to(RAW)).encode("utf-8"))
        with path.open("rb") as handle:
            for block in iter(lambda: handle.read(1024 * 1024), b""):
                digest.update(block)
    return digest.hexdigest()


def json_default(value):
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(f"不支持的 JSON 类型：{type(value).__name__}")


def write_json(name: str, payload: dict) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=name + ".", suffix=".tmp", dir=OUTPUT)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False, default=json_default)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, OUTPUT / name)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def run_index_research() -> tuple[list[dict], dict]:
    events = []
    for method in INDEX_METHODS:
        for code, name in INDEXES.items():
            frame = read_index_csv(INDEX_RAW / f"{code}.csv")
            signals = index_signals(frame, method)
            events.extend(evaluate_index_signals(frame, signals, code=code, name=name, method=method))
        print(f"指数回测完成：{method}")
    return events, {
        "overall": summarize_index_events(events, ["method"]),
        "by_index": summarize_index_events(events, ["method", "code", "name"]),
        "by_year": summarize_index_events(events, ["method", "year"]),
    }


def run_sector_research(level: str) -> tuple[list[dict], dict]:
    manifest = json.loads((RAW / "sectors-manifest.json").read_text(encoding="utf-8"))
    items = [item for item in manifest if item["level"] == level]
    frames = {item["code"]: read_sector_csv(RAW / "sectors" / level / f"{item['code']}.csv") for item in items}
    names = {item["code"]: item["name"] for item in items}
    relatives = relative_momentum_medians(frames)
    events = []
    for method in SECTOR_METHODS:
        for code, frame in frames.items():
            state = sector_state(frame, method, relatives)
            events.extend(evaluate_sector_states(frame, state, code=code, name=names[code], method=method))
        print(f"{level} 板块回测完成：{method}")
    return events, {
        "overall": summarize_sector_events(events, ["method"]),
        "by_state": summarize_sector_events(events, ["method", "type"]),
        "by_sector": summarize_sector_events(events, ["method", "code", "name"]),
        "by_year": summarize_sector_events(events, ["method", "year"]),
    }


def metadata(before: str, after: str) -> dict:
    return {
        "research_start": "2020-10-30", "research_end": "2024-12-31",
        "validation_label": "exploratory_validation",
        "index_cooldown": 20, "ma_windows": [5, 10, 20, 30],
        "raw_sha256_before": before, "raw_sha256_after": after,
        "raw_files_unchanged": before == after,
    }


def main(include_second: bool) -> None:
    before = hash_raw_files()
    index_events, index_summary = run_index_research()
    first_events, first_summary = run_sector_research("first")
    after = hash_raw_files()
    if before != after:
        raise RuntimeError("回测期间原始行情哈希发生变化，已停止输出")
    write_json("first-round.json", {
        "metadata": metadata(before, after),
        "index": {"events": index_events, "summary": index_summary},
        "sectors_first": {"events": first_events, "summary": first_summary},
    })
    if include_second:
        second_events, second_summary = run_sector_research("second")
        final_hash = hash_raw_files()
        if before != final_hash:
            raise RuntimeError("二级板块复验期间原始行情哈希发生变化，已停止输出")
        write_json("sector-second-review.json", {
            "metadata": metadata(before, final_hash),
            "sectors_second": {"events": second_events, "summary": second_summary},
        })
    print(f"研究输出完成：{OUTPUT}；原始行情未修改")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--include-second", action="store_true")
    arguments = parser.parse_args()
    main(arguments.include_second)
