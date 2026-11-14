"""固定口径配置（研究阶段专用）：标的清单、三段划分、28 条有效幅度线、冻结参数集。

设计依据见 openspec/changes/index-precision-pivot-signals/design.md：
- D6/D8：不扩展标的池，固定 7 大宽基指数。
- D7：训练段名义结束日固定 2024-12-31，末尾 30 个交易日划为隔离期排除带。
- D5：有效幅度线 = 训练段全部交易日 H 日绝对收益的 75 分位，按 7 指数 × 4 持有期冻结 28 条。

口径变更记录（stable-market-data-source-migration 换源）：
- 指数日线由同花顺公开日线换为新浪公开指数日线（不复权），复权口径由「前复权」改为「不复权」；
- 新浪源无 amount（成交额）列，COLUMN_ORDER 相应移除 amount，本模块计算不依赖成交额。

本模块只做纯计算与只读行情加载，不写任何文件，绝不触碰生产产物。
"""

from __future__ import annotations

import hashlib
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
INDEX_RAW = ROOT / "data" / "raw" / "indices-ths"
RESEARCH_DIR = ROOT / "data" / "research"
DATA_DIR = ROOT / "data"

# 生产与统计共用同一份固化清单（不扩展，不新增展示卡片）。
INDEXES: dict[str, str] = {
    "sh000001": "上证指数",
    "sz399001": "深证成指",
    "sz399006": "创业板指",
    "sh000688": "科创50",
    "sh000300": "沪深300",
    "sh000905": "中证500",
    "sh000852": "中证1000",
}
KINDS = ("bottom", "top")
HOLD_PERIODS = (5, 10, 20, 30)
SEGMENTS = ("train", "validation")

DATA_SOURCE = "新浪公开指数日线"
ADJUST = "不复权"
PRICE_UNIT = "点"
COLUMN_ORDER = ("date", "open", "close", "high", "low", "volume")
MIN_HISTORY_ROWS = 1250

TRAIN_START = pd.Timestamp("2020-10-30")
TRAIN_END = pd.Timestamp("2024-12-31")  # 名义结束日，固定不随数据延长
VALID_START = pd.Timestamp("2025-01-01")
EMBARGO_TRADING_DAYS = 30  # 覆盖 30 日前瞻 + 20 日浮亏窗口

MAE_WINDOW = 20
MAE_LIMIT = 0.05  # 恰好 5% 不判失败
AMPLITUDE_QUANTILE = 0.75
BASELINE_SEED = 20261109
RANDOM_SEED = 20261109

# 训练段冻结的候选参数（拐点主干沿用 pivot_benchmark.FROZEN_PARAMS 的 5%/8d）。
FROZEN_PIVOT = {"amplitude": 0.05, "min_gap": 8}
# 六因子严格闸门：旧算法 score>=0.52 / core_hits>=2 / 无单维下限。
# 网格内每一档都不低于旧算法（score>=0.52、core_hits>=3、六维全有值且每维>0），
# 最终取值 MUST 由训练段网格择参冻结（任务 2.4），不得在验证段调整。
FROZEN_GATE = {
    "score_threshold": 0.58,
    "core_hits_min": 3,
    "core_extreme": 0.60,  # 与生产 candidate_masks 一致；非 factor_table 分级的 0.85
    "direction_min": 0.05,
    "min_available_directions": 6,
    "confirm_window": 3,
}
# 训练段候选参数组合（全部为严格化方向，不允许放宽）。
GATE_GRID: tuple[dict, ...] = tuple(
    {
        "score_threshold": score,
        "core_hits_min": 3,
        "core_extreme": 0.60,
        "direction_min": dim,
        "min_available_directions": 6,
        "confirm_window": 3,
    }
    for score in (0.52, 0.55, 0.58)
    for dim in (0.05, 0.10)
)
ALGORITHM_VERSION = "index-precision-pivot-v1.0"
LEGACY_ALGORITHM_VERSION = "index-swing6-v2.0"


def read_index_csv(code: str) -> pd.DataFrame:
    """读取单个指数日线；保持与生产同源、同字段顺序、同价格单位。"""
    path = INDEX_RAW / f"{code}.csv"
    frame = pd.read_csv(path)
    missing = [column for column in COLUMN_ORDER if column not in frame.columns]
    if missing:
        raise ValueError(f"{code} 日线缺少字段：{missing}")
    frame["date"] = pd.to_datetime(frame["date"])
    return (
        frame[list(COLUMN_ORDER)]
        .sort_values("date")
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index(drop=True)
    )


def load_frames() -> dict[str, pd.DataFrame]:
    """按固化清单加载全部 7 个指数，缺文件直接报错（不静默丢弃）。"""
    frames: dict[str, pd.DataFrame] = {}
    for code in INDEXES:
        path = INDEX_RAW / f"{code}.csv"
        if not path.exists():
            raise FileNotFoundError(f"固化清单中的标的缺失：{code} ({path})")
        frames[code] = read_index_csv(code)
    return frames


def input_hash() -> str:
    """7 份原始 CSV 逐字节拼接后的 sha256，作为冻结集与基准集的输入指纹。"""
    digest = hashlib.sha256()
    for code in INDEXES:
        digest.update(code.encode("utf-8"))
        digest.update((INDEX_RAW / f"{code}.csv").read_bytes())
    return digest.hexdigest()


def build_manifest(frames: dict[str, pd.DataFrame]) -> dict:
    """逐项登记代码/名称/数据源/复权方式/行数/起止日期/相对路径；历史不足显式标记。"""
    entries = []
    for code, name in INDEXES.items():
        frame = frames[code]
        rows = int(len(frame))
        entries.append(
            {
                "code": code,
                "name": name,
                "source": DATA_SOURCE,
                "adjust": ADJUST,
                "price_unit": PRICE_UNIT,
                "columns": list(COLUMN_ORDER),
                "rows": rows,
                "start": frame["date"].min().strftime("%Y-%m-%d"),
                "end": frame["date"].max().strftime("%Y-%m-%d"),
                "relative_path": f"data/raw/indices-ths/{code}.csv",
                "history_sufficient": bool(rows >= MIN_HISTORY_ROWS),
                "insufficient_history_note": None
                if rows >= MIN_HISTORY_ROWS
                else f"历史 {rows} 行，不足 {MIN_HISTORY_ROWS} 个交易日；显式标记，不丢弃",
            }
        )
    caliber_differences = [
        {"code": item["code"], "field": field, "value": item[field]}
        for item in entries
        for field in ("source", "adjust", "price_unit")
        if item[field]
        != {"source": DATA_SOURCE, "adjust": ADJUST, "price_unit": PRICE_UNIT}[field]
    ]
    caliber_differences += [
        {"code": item["code"], "field": "columns", "value": item["columns"]}
        for item in entries
        if tuple(item["columns"]) != COLUMN_ORDER
    ]
    return {
        "universe_frozen": True,
        "universe_size": len(entries),
        "source": DATA_SOURCE,
        "adjust": ADJUST,
        "price_unit": PRICE_UNIT,
        "columns": list(COLUMN_ORDER),
        "caliber_consistent": not caliber_differences,
        "caliber_differences": caliber_differences,
        "min_history_rows": MIN_HISTORY_ROWS,
        "input_hash": input_hash(),
        "entries": entries,
    }


def segment_definition(frame: pd.DataFrame) -> dict:
    """三段起止：训练段（名义/可调参）、隔离期、独立验证段。

    隔离期为训练段末尾 30 个交易日的排除带：该带内的信号既不用于调参，
    也不计入训练段准确率，且不跨入验证段（design D7）。
    """
    dates = pd.to_datetime(frame["date"])
    train_positions = [
        i for i, day in enumerate(dates) if TRAIN_START <= day <= TRAIN_END
    ]
    if len(train_positions) < EMBARGO_TRADING_DAYS + 1:
        raise ValueError("训练段交易日不足，无法切出隔离期")
    embargo_first = train_positions[-EMBARGO_TRADING_DAYS]
    effective_last = train_positions[-EMBARGO_TRADING_DAYS - 1]
    valid_positions = [i for i, day in enumerate(dates) if day >= VALID_START]
    if not valid_positions:
        raise ValueError("独立验证段为空")
    embargo_days = int(train_positions[-1] - embargo_first + 1)
    return {
        "train": {
            "start": TRAIN_START.strftime("%Y-%m-%d"),
            "end": TRAIN_END.strftime("%Y-%m-%d"),
            "effective_start": dates.iat[train_positions[0]].strftime("%Y-%m-%d"),
            "effective_end": dates.iat[effective_last].strftime("%Y-%m-%d"),
        },
        "embargo": {
            "start": dates.iat[embargo_first].strftime("%Y-%m-%d"),
            "end": dates.iat[train_positions[-1]].strftime("%Y-%m-%d"),
            "trading_days": embargo_days,
            "excluded_from_tuning": True,
            "excluded_from_train_accuracy": True,
        },
        "validation": {
            "start": dates.iat[valid_positions[0]].strftime("%Y-%m-%d"),
            "end": dates.iat[valid_positions[-1]].strftime("%Y-%m-%d"),
        },
        "train_nominal_end": TRAIN_END.strftime("%Y-%m-%d"),
        "embargo_trading_days": embargo_days,
        "embargo_min_required": EMBARGO_TRADING_DAYS,
        "train_validation_no_overlap": bool(
            pd.Timestamp(TRAIN_END) < pd.Timestamp(dates.iat[valid_positions[0]])
        ),
    }


def freeze_amplitude_lines(frames: dict[str, pd.DataFrame]) -> dict:
    """训练段 H 日绝对收益的 75 分位 → 7 指数 × 4 持有期共 28 条有效幅度线。

    只用训练段全部交易日的收益分布（与信号无关），不触碰验证段，避免循环论证。
    """
    lines: dict[str, dict[str, float]] = {}
    evidence: dict[str, dict] = {}
    for code, frame in frames.items():
        train = frame[(frame["date"] >= TRAIN_START) & (frame["date"] <= TRAIN_END)]
        close = train["close"].to_numpy(dtype=float)
        lines[code] = {}
        per_holding = {}
        for horizon in HOLD_PERIODS:
            returns = (close[horizon:] - close[:-horizon]) / close[:-horizon]
            per_holding[str(horizon)] = int(returns.size)
            lines[code][str(horizon)] = float(
                np.percentile(np.abs(returns), AMPLITUDE_QUANTILE * 100)
            )
        evidence[code] = {
            "train_rows": int(len(train)),
            "train_start": train["date"].min().strftime("%Y-%m-%d"),
            "train_end": train["date"].max().strftime("%Y-%m-%d"),
            "quantile": AMPLITUDE_QUANTILE,
            "basis": "全部交易日 H 日绝对收益分布，不使用任何信号结果",
            "per_holding_samples": per_holding,
        }
    return {
        "definition": "训练段全部交易日 H 日绝对收益的 75 分位",
        "quantile": AMPLITUDE_QUANTILE,
        "horizons": list(HOLD_PERIODS),
        "line_count": len(lines) * len(HOLD_PERIODS),
        "determined_on": f"train {TRAIN_START.date()}~{TRAIN_END.date()}",
        "validation_must_not_adjust": True,
        "lines": lines,
        "evidence": evidence,
    }


def amplitude_line(frozen: dict, code: str, horizon: int) -> float:
    """取该指数该持有期的冻结线；缺线直接报错，不回退到统一阈值。"""
    try:
        return float(frozen["lines"][code][str(horizon)])
    except KeyError as exc:
        raise KeyError(f"缺少冻结幅度线：{code} / {horizon} 日") from exc


def frozen_params(
    frames: dict[str, pd.DataFrame],
    gate: dict | None = None,
    grid_evidence: dict | None = None,
) -> dict:
    """完整冻结参数集：拐点阈值、六维闸门、MAE、幅度线、输入哈希与随机种子。"""
    lines = freeze_amplitude_lines(frames)
    return {
        "algorithm_version": ALGORITHM_VERSION,
        "legacy_algorithm_version": LEGACY_ALGORITHM_VERSION,
        "determined_on": f"train {TRAIN_START.date()}~{TRAIN_END.date()} (7 indices)",
        "pivot_amplitude": FROZEN_PIVOT["amplitude"],
        "pivot_min_gap": FROZEN_PIVOT["min_gap"],
        "six_factor_gate": dict(gate or FROZEN_GATE),
        "parameter_grid_evidence": grid_evidence,
        "mae_window": MAE_WINDOW,
        "mae_limit": MAE_LIMIT,
        "mae_rule": "20 日盘中极值；|MAE| > 5% 判失败，恰好 5% 不判失败",
        "effective_amplitude": lines,
        "input_hash": input_hash(),
        "random_seed": RANDOM_SEED,
        "baseline_seed": BASELINE_SEED,
        "validation_must_not_adjust": True,
    }
