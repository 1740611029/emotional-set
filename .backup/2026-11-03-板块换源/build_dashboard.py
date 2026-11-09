"""由原始行情生成前端 JSON；指数和板块使用确认版算法。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from candidate_state import candidate_records
from confirmed_index_signals import ACTIVE_ALGORITHM, build_index_result
from confirmed_sector_trends import (
    ALGORITHM_VERSION,
    HISTORY_DAYS,
    STATE_CODES,
    build_sector_evaluation,
    build_subsector_evaluation,
    calculate_sector_universe,
    sector_history,
    spark_series,
)
from fetch_market import INDEXES, INDEX_RAW
from io_utils import protected_write, validate_file
from research_algorithms import read_index_csv, read_sector_csv
from swing_indicators import breadth_table

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SECTOR_RAW = RAW / "sectors"


def load_breadth() -> pd.DataFrame:
    """申万一级行业宽度；缺失行业文件时直接报错，不静默降级。"""
    files = sorted((SECTOR_RAW / "first").glob("*.csv"))
    if len(files) < 20:
        raise ValueError(f"申万一级行业文件不足 20 个，实际 {len(files)} 个")
    return breadth_table({path.stem: read_sector_csv(path) for path in files})


def current_signal(signals: list[dict], history: list[dict]) -> dict:
    active_dates = {row["date"] for row in history[-10:]}
    active = [row for row in signals if row["date"] in active_dates]
    if not active:
        return {"type": "none", "date": None}
    latest = active[-1]
    return {"type": latest["type"], "date": latest["date"]}


def build_indices() -> dict:
    indices = []
    recent = []
    pending = []
    ends = []
    breadth = load_breadth()
    for code, name in INDEXES.items():
        frame = read_index_csv(INDEX_RAW / f"{code}.csv")
        if len(frame) < 1250:
            raise ValueError(f"{code} 历史不足 1250 个交易日")
        result = build_index_result(frame, breadth)
        # 只读旁路：候选与三层状态，不参与上面的信号计算；旧算法无三层确认候选
        candidates = candidate_records(frame, breadth) if ACTIVE_ALGORITHM == "swing" else []
        drawdown_lookup = {
            (event["date"], event["type"]): event.get("max_drawdown_20")
            for segment in result["evaluation"]["segments"]
            for event in segment["events"]
        }
        history = [{"date": row.date.strftime("%Y-%m-%d"), "close": float(row.close)}
                   for row in frame.itertuples()]
        latest = history[-1]["close"]
        previous = history[-2]["close"]
        ends.append(history[-1]["date"])
        indices.append({
            "code": code, "name": name, "latest": latest,
            "change_pct": round((latest / previous - 1) * 100, 4),
            "current_signal": current_signal(result["signals"], history),
            "history": history, "signals": result["signals"],
            "evaluation": result["evaluation"], "accuracy": result["accuracy"],
            "candidates": candidates,
        })
        recent.extend({
            "date": signal["date"], "code": code, "name": name,
            "type": signal["type"], "price": signal["price"],
            "grade": signal.get("grade"),
            "max_drawdown_20": drawdown_lookup.get((signal["date"], signal["type"])),
            "change_since": round((latest / signal["price"] - 1) * 100, 4),
        } for signal in result["signals"])
        pending.extend({**record, "code": code, "name": name} for record in candidates)
    recent.sort(key=lambda row: (row["date"], row["code"]), reverse=True)
    pending.sort(key=lambda row: (row["date"], row["code"]), reverse=True)
    return {"as_of": min(ends), "is_mock": False, "indices": indices,
            "recent_signals": recent, "candidates": pending}


def build_sectors() -> dict:
    manifest = json.loads((RAW / "sectors-manifest.json").read_text(encoding="utf-8"))
    first = [row for row in manifest if row["level"] == "first"]
    second = [row for row in manifest if row["level"] == "second"]
    if len(first) != 31 or not second:
        raise ValueError("申万一级/二级板块清单不完整")

    frames = {}
    for item in manifest:
        if item["level"] not in {"first", "second"}:
            continue
        path = SECTOR_RAW / item["level"] / f"{item['code']}.csv"
        if not path.exists():
            raise ValueError(f"板块行情文件缺失：{item['code']}")
        frames[item["code"]] = read_sector_csv(path)

    records = calculate_sector_universe(manifest, frames)
    histories = {code: sector_history(frame) for code, frame in frames.items()}
    # 交易日全局共享一份，避免 162 个板块各存一遍日期
    dates = sorted({row["date"] for rows in histories.values() for row in rows})[-HISTORY_DAYS:]

    def pack(record: dict) -> dict:
        rows = {row["date"]: row for row in histories[record["code"]]}
        close = [rows[day]["close"] if day in rows else None for day in dates]
        states = [STATE_CODES[rows[day]["state"]] if day in rows and rows[day]["state"] else None
                  for day in dates]
        return {
            "code": record["code"],
            "name": record["name"],
            "level": record["level"],
            "parent": record["parent"],
            "date": record["date"],
            "state": record["state"],
            "strength": record["strength"],
            "strength_parts": record["strength_parts"],
            "strength_basis": record["strength_basis"],
            "days": record["days"],
            "data_anomaly": record["data_anomaly"],
            "data_lag": record["data_lag"],
            "spark": spark_series(frames[record["code"]]),
            "series": {"close": close, "state": states},
        }

    groups = {"bull": [], "warn": [], "bear": [], "rise": [], "unclassified": []}
    latest_dates = []
    for record in records:
        if record["date"]:
            latest_dates.append(record["date"])
        item = pack(record)
        item["subsectors"] = [pack(sub) for sub in record["subsectors"]]
        groups[record["state"] if record["state"] else "unclassified"].append(item)

    return {
        "as_of": max(latest_dates) if latest_dates else pd.Timestamp.today().strftime("%Y-%m-%d"),
        "is_mock": False,
        "algorithm_version": ALGORITHM_VERSION,
        "dates": dates,
        "groups": groups,
        "evaluation": build_sector_evaluation(manifest, frames),
        "subsector_evaluation": build_subsector_evaluation(manifest, frames),
    }


def main(validate_only: bool = False) -> None:
    names = ["index-signals.json", "sector-trends.json"]
    if validate_only:
        for name in names:
            validate_file(name)
        return
    protected_write("index-signals.json", build_indices())
    # 板块产物含 162 × 250 的序列，标量数组压成一行以控制体积
    protected_write("sector-trends.json", build_sectors(), compact_arrays=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    args = parser.parse_args()
    main(args.validate)
