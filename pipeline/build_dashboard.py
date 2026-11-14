"""由原始行情生成前端 JSON；指数和板块使用确认版算法。"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from candidate_state import candidate_records
from confirmed_index_signals import ACTIVE_ALGORITHM, build_index_result
from bigwave_bottom_engine import bottom_zones
from confirmed_sector_trends import (
    ALGORITHM_VERSION,
    HISTORY_DAYS,
    STATE_CODES,
    build_sector_evaluation,
    calculate_sector_universe,
    sector_history,
    spark_series,
)
from fetch_market import (
    EM_SECTOR_MANIFEST, EM_SECTOR_RAW, EM_SECTOR_UNIVERSE, INDEXES, INDEX_RAW, SECTOR_SOURCE,
)
from etf_volume_events import (
    ALGORITHM_VERSION as ETF_VOLUME_VERSION, CLUSTER_GAP_DAYS, STATE_BASIS_INDEX,
    THRESHOLD_AMOUNT, identify as identify_etf_volume,
)
from fetch_etf import ETF_DAILY_RAW, ETF_UNIVERSE, read_etf_csv
from io_utils import protected_write, validate_file
from research_algorithms import read_index_csv, read_sector_csv
from swing_indicators import breadth_table

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"


def load_breadth() -> pd.DataFrame:
    """行业横截面宽度输入；与板块产物读同一份东财行业板块日线，缺失时直接报错不降级。"""
    files = sorted(EM_SECTOR_RAW.glob("*.csv"))
    if len(files) < 20:
        raise ValueError(f"东财行业板块文件不足 20 个，实际 {len(files)} 个")
    return breadth_table({path.stem: read_sector_csv(path) for path in files})


def load_precision_research() -> tuple[dict, dict, dict]:
    """读取研究阶段的冻结产物；缺失或结构不全时构建 MUST 失败，不静默降级。"""
    research = ROOT / "data" / "research"
    matrix = json.loads((research / "precision-matrix.json").read_text(encoding="utf-8"))
    decision = json.loads((research / "precision-decision.json").read_text(encoding="utf-8"))
    frozen = json.loads((research / "precision-frozen-params.json").read_text(encoding="utf-8"))
    for name, payload in (("precision-matrix.json", matrix),
                          ("precision-decision.json", decision),
                          ("precision-frozen-params.json", frozen)):
        if not payload:
            raise ValueError(f"研究产物为空：{name}")
    return matrix, decision, frozen


def compact_matrix(matrix: dict) -> dict:
    """保留矩阵聚合字段，剔除逐事件明细以控制快照体积（明细留在 data/research/）。"""
    return {
        segment: {
            "statistical_interval": block["statistical_interval"],
            "triggers": block["triggers"],
            "horizons": {
                horizon: {
                    kind: {key: value for key, value in cell.items() if key != "events"}
                    for kind, cell in directions.items()
                }
                for horizon, directions in block["horizons"].items()
            },
        }
        for segment, block in matrix.items()
    }


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
    index_tails: dict[str, str] = {}
    breadth = load_breadth()
    # 生产信号：bigwave（大波段底部，顶部暂时关闭）。旧 swing/legacy 仍可经 ACTIVE_ALGORITHM 回滚。
    # 旧的 precision 多持有期矩阵基于原 swing 信号，与新底部信号口径不一致，本次不再输出，
    # 前端对 precision 缺失已做兼容（不展示矩阵入口）。
    for code, name in INDEXES.items():
        frame = read_index_csv(INDEX_RAW / f"{code}.csv")
        if len(frame) < 1250:
            raise ValueError(f"{code} 历史不足 1250 个交易日")
        result = build_index_result(frame, breadth, code)
        # 只读旁路：候选与三层状态，不参与上面的信号计算；bigwave 无候选
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
        index_tails[code] = history[-1]["date"]
        indices.append({
            "code": code, "name": name, "latest": latest,
            "change_pct": round((latest / previous - 1) * 100, 4),
            "current_signal": current_signal(result["signals"], history),
            "history": history, "signals": result["signals"],
            "evaluation": result["evaluation"], "accuracy": result["accuracy"],
            "candidates": candidates,
            # 情绪底背景带：超卖 + 宽度冰点的连续区间，前端画浅色底纹用
            "bottom_zones": bottom_zones(frame, breadth),
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
    as_of = min(ends)
    # 短板标的：尾日早于最晚尾日的指数（如 6 尾日 T、1 尾日 T-1 → 标出 T-1 那只）
    latest_tail = max(ends)
    lagging = [code for code, tail in index_tails.items() if tail < latest_tail]
    return {
        "as_of": as_of,
        # 聚合规则显式标注：as_of = 各指数尾日的最小值（短板标的各自尾日一并提供）
        "as_of_rule": "min(index_tails)",
        "index_tails": index_tails,
        "lagging_indexes": lagging,
        "is_mock": False, "indices": indices,
        "recent_signals": recent, "candidates": pending,
    }


def build_sectors() -> dict:
    manifest = json.loads(EM_SECTOR_MANIFEST.read_text(encoding="utf-8"))
    boards = manifest["boards"]
    if len(boards) != len(EM_SECTOR_UNIVERSE):
        raise ValueError(f"东财行业板块清单不完整：{len(boards)} 个，应为 {len(EM_SECTOR_UNIVERSE)} 个")

    frames = {}
    for item in boards:
        path = EM_SECTOR_RAW / f"{item['code']}.csv"
        if not path.exists():
            raise ValueError(f"板块行情文件缺失：{item['code']}")
        frames[item["code"]] = read_sector_csv(path)

    records = calculate_sector_universe(boards, frames)
    histories = {code: sector_history(frame) for code, frame in frames.items()}
    # 交易日全局共享一份，避免 31 个板块各存一遍日期
    dates = sorted({row["date"] for rows in histories.values() for row in rows})[-HISTORY_DAYS:]

    def pack(record: dict) -> dict:
        rows = {row["date"]: row for row in histories[record["code"]]}
        close = [rows[day]["close"] if day in rows else None for day in dates]
        states = [STATE_CODES[rows[day]["state"]] if day in rows and rows[day]["state"] else None
                  for day in dates]
        return {
            "code": record["code"],
            "name": record["name"],
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
        groups[record["state"] if record["state"] else "unclassified"].append(pack(record))

    return {
        "as_of": max(latest_dates) if latest_dates else pd.Timestamp.today().strftime("%Y-%m-%d"),
        "is_mock": False,
        "algorithm_version": ALGORITHM_VERSION,
        "data_source": SECTOR_SOURCE,
        "dates": dates,
        "groups": groups,
        "evaluation": build_sector_evaluation(boards, frames),
    }


def build_etf_volume_events() -> dict:
    """ETF 放量事件产物：与研究报告共用同一套识别纯函数，不参与任何信号计算。"""
    frames = {}
    for item in ETF_UNIVERSE:
        path = ETF_DAILY_RAW / f"{item['code']}.csv"
        if not path.exists():
            raise ValueError(f"ETF 行情文件缺失：{item['code']}")
        frames[item["code"]] = read_etf_csv(path)
    index_frame = read_index_csv(INDEX_RAW / f"{STATE_BASIS_INDEX}.csv")
    result = identify_etf_volume(frames, index_frame)
    as_of = min(frame["date"].iat[-1] for frame in frames.values())
    return {
        "as_of": as_of,
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "algorithm_version": ETF_VOLUME_VERSION,
        "threshold_amount": THRESHOLD_AMOUNT,
        "cluster_gap_days": CLUSTER_GAP_DAYS,
        "mode_basis_index": STATE_BASIS_INDEX,
        "pool": [{"code": item["code"], "name": item["name"], "index": item["index"]}
                 for item in ETF_UNIVERSE],
        "days": result["days"],
        "events": result["events"],
    }


def main(validate_only: bool = False, output_dir: Path | str | None = None) -> None:
    """重建产物；output_dir 用于自检或演练时把结果写到别处，缺省才写生产 data 目录。"""
    names = ["index-signals.json", "sector-trends.json", "etf-volume-events.json"]
    directory = None if output_dir is None else Path(output_dir)
    if validate_only:
        for name in names:
            validate_file(name, directory=directory)
        return
    protected_write("index-signals.json", build_indices(), directory=directory)
    # 板块产物含 31 × 250 的序列，标量数组压成一行以控制体积
    protected_write("sector-trends.json", build_sectors(), directory=directory, compact_arrays=True)
    protected_write("etf-volume-events.json", build_etf_volume_events(), directory=directory,
                    compact_arrays=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--validate", action="store_true")
    parser.add_argument("--output-dir", default=None,
                        help="把重建结果写入指定目录而非生产 data 目录")
    args = parser.parse_args()
    main(args.validate, args.output_dir)
