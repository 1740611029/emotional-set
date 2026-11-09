"""指数点位拐点「真实基准集」（ground truth）构建——离线调研专用，绝不进入信号生成。

设计要点（见 openspec/changes/index-pivot-signal-research/design.md D1）：
- 采用经典 ZigZag 规则做**逐日因果滚动**标注：某极值日只有在其后价格反向运动达到
  幅度阈值、且与上一拐点满足最小波段间隔时，才被**事后**写入基准集。
- 基准集是"事后事实"，标注允许使用未来数据（只用于评价信号是否落在真实拐点附近），
  因此它 MUST NOT 参与任何生产信号计算，只在 `pivot_eval` 里当标尺用。
- 幅度阈值与最小间隔在训练段（2020-10-30~2024-12-31）用网格 + "年均拐点 4–8 个"
  约束选定并冻结（见 `FROZEN_PARAMS`），验证段不得再调整。

本模块的计算部分只做纯计算与数据加载；只有显式运行本文件（`__main__`）时才会把
基准集产物写入独立研究目录 `data/research/`，绝不触碰任何生产信号文件。
"""
from __future__ import annotations

import hashlib
import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INDEX_RAW = ROOT / "data" / "raw" / "indices-ths"
RESEARCH_DIR = ROOT / "data" / "research"
BENCHMARK_PRODUCT = RESEARCH_DIR / "index-pivot-benchmark.json"
INDEXES = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000300": "沪深300", "sh000905": "中证500",
    "sh000852": "中证1000",
}

# 训练段（=研究段）与验证段边界，沿用 research_algorithms 的既有划分。
TRAIN_START = pd.Timestamp("2020-10-30")
TRAIN_END = pd.Timestamp("2024-12-31")
VALID_START = pd.Timestamp("2025-01-01")

# 阈值网格与目标年均拐点数区间（用于训练段择参）。
AMPLITUDE_GRID = (0.03, 0.05, 0.07)
MIN_GAP_GRID = (8, 13, 21)
TARGET_PIVOTS_PER_YEAR = (4, 8)

# 训练段网格选定后冻结的参数（见 select_frozen_params 的择参规则）：
# 网格 {3%,5%,7%}×{8,13,21}d 在训练段（2020-10-30~2024-12-31, 7 指数）上，仅
# (3%,8d) 年均 4.39 与 (5%,8d) 年均 4.76 落入 4–8 目标带；(5%,8d) 更贴近带中值 6
# 且逐指数分布更均衡（3.36~7.43），故选 amplitude=5%、min_gap=8 交易日。
# 该量级也与生产引擎 min_swing_amplitude=3%/min_swing_days=8 可比。
FROZEN_PARAMS = {
    "amplitude": 0.05,
    "min_gap": 8,
    "determined_on": "train 2020-10-30~2024-12-31, 7 indices, grid 3%/5%/7% x {8,13,21}d",
    "target_pivots_per_year": list(TARGET_PIVOTS_PER_YEAR),
    "train_mean_pivots_per_year": 4.76,
}


def _series(frame: pd.DataFrame) -> tuple[pd.Series, np.ndarray]:
    """取规范化后的日期序列与收盘价数组（升序、去重、丢弃无收盘价的行）。"""
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return work["date"], work["close"].to_numpy(dtype=float)


def annotate_pivots(frame: pd.DataFrame, *, amplitude: float, min_gap: int) -> list[dict]:
    """因果滚动 ZigZag 标注真实拐点，返回逐拐点记录列表（date/type/price + 索引）。

    规则（严格事后确认）：
    - 顶部：自峰值价回落达到 `amplitude`（(peak-close)/peak>=amplitude）时在确认日写入；
    - 底部：自谷值价回升达到 `amplitude`（(close-trough)/trough>=amplitude）时写入；
    - 相邻两个拐点极值位置之间 MUST 至少间隔 `min_gap` 个交易日（波段间隔约束）；
    - 极值在确认前若被更极端价格刷新，则峰值/谷值右移，旧极值不单独成拐点；
    - 顶底严格交替：确认一个拐点后，只寻找反向拐点。

    返回每条：{index, date, type, price, confirm_index, confirm_date}，
    其中 index/date/type/price 是拐点事实（评价以此为基准日），
    confirm_index/confirm_date 是该拐点"变得可知"的日子。
    """
    dates, close = _series(frame)
    n = close.size
    if n < 2 or amplitude <= 0 or min_gap < 1:
        return []

    pivots: list[tuple[int, str, int]] = []  # (extreme_index, type, confirm_index)

    def seek_extreme(start: int, want: str, prev_idx: int | None) -> tuple[int, int] | None:
        """从 start 起寻找 want 类型拐点，返回 (极值索引, 确认索引)；找不到返回 None。

        prev_idx 为上一拐点极值索引，用于最小间隔约束（None 表示首个拐点，无间隔约束）。
        """
        ext = close[start]
        ext_idx = start
        for i in range(start + 1, n):
            c = close[i]
            if want == "top":
                if c > ext:
                    ext, ext_idx = c, i
                reversal = (ext - c) / ext if ext > 0 else 0.0
            else:
                if c < ext:
                    ext, ext_idx = c, i
                reversal = (c - ext) / ext if ext > 0 else 0.0
            if reversal >= amplitude and (i - ext_idx) >= 1:
                if prev_idx is not None and ext_idx - prev_idx < min_gap:
                    # 间隔不足：该极值不成独立拐点，继续跟踪（后续更极值会右移）
                    continue
                return ext_idx, i
        return None

    # 相位 1：定首个拐点——同时跟踪高/低点，取先被反向运动确认者。
    hi = lo = close[0]
    hi_idx = lo_idx = 0
    seed: tuple[int, str, int] | None = None
    for i in range(1, n):
        c = close[i]
        if c > hi:
            hi, hi_idx = c, i
        if c < lo:
            lo, lo_idx = c, i
        top_ready = hi > 0 and (hi - c) / hi >= amplitude and (i - hi_idx) >= 1
        bottom_ready = lo > 0 and (c - lo) / lo >= amplitude and (i - lo_idx) >= 1
        if top_ready and bottom_ready:
            # 同一天两向都可能确认时，取极值位置更靠前者；再并列取跌幅更大（顶部）者。
            if hi_idx < lo_idx or (hi_idx == lo_idx and (hi - c) / hi >= (c - lo) / lo):
                seed = (hi_idx, "top", i)
            else:
                seed = (lo_idx, "bottom", i)
            break
        if top_ready:
            seed = (hi_idx, "top", i)
            break
        if bottom_ready:
            seed = (lo_idx, "bottom", i)
            break
    if seed is None:
        return []
    pivots.append(seed)

    # 相位 2：顶底交替向后扫描。
    cur_idx, cur_type, _ = seed
    while True:
        want = "bottom" if cur_type == "top" else "top"
        nxt = seek_extreme(cur_idx, want, cur_idx)
        if nxt is None:
            break
        ext_idx, confirm_idx = nxt
        if ext_idx <= cur_idx:
            break
        pivots.append((ext_idx, want, confirm_idx))
        cur_idx, cur_type = ext_idx, want

    records = []
    for ext_idx, kind, confirm_idx in pivots:
        records.append({
            "index": int(ext_idx),
            "date": dates.iat[ext_idx].strftime("%Y-%m-%d"),
            "type": kind,
            "price": float(close[ext_idx]),
            "confirm_index": int(confirm_idx),
            "confirm_date": dates.iat[confirm_idx].strftime("%Y-%m-%d"),
        })
    return records


def clip_frame(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    """按 [start, end] 闭区间裁剪日线（用于训练段择参，只喂训练段行情）。"""
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"])
    mask = (work["date"] >= start) & (work["date"] <= end)
    return work[mask].reset_index(drop=True)


def load_index_frames(codes: tuple[str, ...] | None = None) -> dict[str, pd.DataFrame]:
    """读取指数日线（同花顺前复权口径，与生产同源），键为指数代码。"""
    codes = codes or tuple(INDEXES)
    frames: dict[str, pd.DataFrame] = {}
    for code in codes:
        path = INDEX_RAW / f"{code}.csv"
        if not path.exists():
            continue
        frame = pd.read_csv(path)
        frame["date"] = pd.to_datetime(frame["date"])
        frames[code] = frame.sort_values("date").reset_index(drop=True)
    return frames


def pivots_per_year(count: int, span_days: float) -> float:
    """按自然年折算年均拐点数（span_days<=0 时返回 0）。"""
    years = span_days / 365.25
    return count / years if years > 0 else 0.0


def grid_report(frames: dict[str, pd.DataFrame], *, start: pd.Timestamp = TRAIN_START,
                end: pd.Timestamp = TRAIN_END, amplitudes=AMPLITUDE_GRID, gaps=MIN_GAP_GRID) -> list[dict]:
    """训练段网格：每个 (amplitude, min_gap) 组合下 7 指数的年均拐点数与达标情况。"""
    span_days = (end - start).days
    rows = []
    for amplitude in amplitudes:
        for min_gap in gaps:
            per_index = {}
            ratios = []
            for code, frame in frames.items():
                train = clip_frame(frame, start, end)
                count = len(annotate_pivots(train, amplitude=amplitude, min_gap=min_gap))
                ratio = pivots_per_year(count, span_days)
                per_index[code] = {"pivots": count, "per_year": round(ratio, 2)}
                ratios.append(ratio)
            mean_ratio = float(np.mean(ratios)) if ratios else 0.0
            lo, hi = TARGET_PIVOTS_PER_YEAR
            rows.append({
                "amplitude": amplitude, "min_gap": min_gap,
                "mean_per_year": round(mean_ratio, 2),
                "in_target_band": lo <= mean_ratio <= hi,
                "per_index": per_index,
            })
    return rows


def select_frozen_params(frames: dict[str, pd.DataFrame]) -> dict:
    """训练段网格选参：优先取年均拐点数落入 4–8 目标带、且最贴近带中值 (6) 的组合。

    仅用训练段行情，验证段不参与择参。返回可直接写入 FROZEN_PARAMS 的字典。
    """
    rows = grid_report(frames)
    candidates = [row for row in rows if row["in_target_band"]]
    pool = candidates or rows  # 无严格达标时退回到最接近目标的组合
    best = min(pool, key=lambda row: abs(row["mean_per_year"] - 6.0))
    return {
        "amplitude": best["amplitude"],
        "min_gap": best["min_gap"],
        "mean_per_year": best["mean_per_year"],
    }


def build_benchmark(frames: dict[str, pd.DataFrame], *, params: dict | None = None) -> dict[str, list[dict]]:
    """对每个指数的完整历史用冻结参数标注拐点，返回 {code: [pivot 记录]}。"""
    params = params or FROZEN_PARAMS
    return {
        code: annotate_pivots(frame, amplitude=params["amplitude"], min_gap=params["min_gap"])
        for code, frame in frames.items()
    }


def input_hash(codes: tuple[str, ...]) -> str:
    """对基准集所依赖的指数日线 CSV 取内容哈希；行情变化即可识别并需重建。"""
    digest = hashlib.sha256()
    for code in sorted(codes):
        path = INDEX_RAW / f"{code}.csv"
        if not path.exists():
            continue
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def _sample_check(frames: dict[str, pd.DataFrame], benchmark: dict[str, list[dict]], *, n: int = 3) -> list[dict]:
    """抽取跨指数的若干拐点，与日线逐值自动化交叉核对（代替人工目视，可复核）：

    每个抽样拐点校验：拐点价 == 当日收盘价；确认日严格晚于拐点日；
    拐点类型与其所处波段极值方向一致（top 为其左右腿的相对高点、bottom 为相对低点）。
    """
    flat = [(code, row) for code, rows in benchmark.items() for row in rows]
    if not flat:
        return []
    step = max(1, len(flat) // n)
    samples = flat[::step][:n]
    checks = []
    for code, row in samples:
        frame = frames[code].reset_index(drop=True)
        index = row["index"]
        same_price = abs(float(frame["close"].iat[index]) - row["price"]) < 1e-6
        confirm_after = row["confirm_index"] > index
        leg = frame["close"].iloc[max(0, index - 5):index + 6].to_numpy(dtype=float)
        extreme_ok = bool(
            (row["type"] == "top" and abs(leg.max() - row["price"]) < 1e-6)
            or (row["type"] == "bottom" and abs(leg.min() - row["price"]) < 1e-6)
        )
        checks.append({
            "code": code, "date": row["date"], "type": row["type"], "price": row["price"],
            "price_matches_close": same_price, "confirm_after_pivot": confirm_after,
            "local_extreme_consistent": extreme_ok,
            "passed": same_price and confirm_after and extreme_ok,
        })
    return checks


def build_product(frames: dict[str, pd.DataFrame] | None = None, *, params: dict | None = None) -> dict:
    """组装基准集产物（含冻结参数、逐指数区间、input_hash 与抽样核对）。"""
    frames = frames or load_index_frames()
    params = params or FROZEN_PARAMS
    benchmark = build_benchmark(frames, params=params)
    per_index = {}
    for code, rows in benchmark.items():
        if rows:
            span_days = (pd.Timestamp(rows[-1]["date"]) - pd.Timestamp(rows[0]["date"])).days
            per_year = pivots_per_year(len(rows), span_days) if span_days > 0 else 0.0
        else:
            per_year = 0.0
        per_index[code] = {
            "name": INDEXES.get(code, code),
            "start": frames[code]["date"].min().strftime("%Y-%m-%d") if len(frames[code]) else None,
            "end": frames[code]["date"].max().strftime("%Y-%m-%d") if len(frames[code]) else None,
            "pivot_count": len(rows),
            "pivots_per_year": round(per_year, 2),
            "pivots": rows,
        }
    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "purpose": "指数点位拐点真实基准集（ground truth），仅用于离线评价，绝不进入信号生成",
        "frozen_params": params,
        "train_segment": [TRAIN_START.strftime("%Y-%m-%d"), TRAIN_END.strftime("%Y-%m-%d")],
        "validation_segment_start": VALID_START.strftime("%Y-%m-%d"),
        "input_hash": input_hash(tuple(frames)),
        "total_pivots": sum(row["pivot_count"] for row in per_index.values()),
        "sample_check": _sample_check(frames, benchmark),
        "indices": per_index,
    }


def write_product(path: Path = BENCHMARK_PRODUCT, frames: dict[str, pd.DataFrame] | None = None) -> Path:
    """原子写入基准集产物到独立研究目录（不落生产路径）。"""
    product = build_product(frames)
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(product, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return path


if __name__ == "__main__":
    target = write_product()
    data = json.loads(target.read_text(encoding="utf-8"))
    print(f"已写入基准集：{target}")
    print(f"冻结参数：amplitude={data['frozen_params']['amplitude']} "
          f"min_gap={data['frozen_params']['min_gap']}，共 {data['total_pivots']} 个拐点")
    for item in data["sample_check"]:
        print(f"  抽样 {item['code']} {item['date']} {item['type']} "
              f"@{item['price']} 核对通过={item['passed']}")
