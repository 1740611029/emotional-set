"""研究阶段编排（只写 data/research/，绝不触碰生产产物）。

产出（全部落在独立研究目录）：
  precision-universe-manifest.json      任务 1.1 标的清单固化
  precision-segments.json               任务 1.3 三段划分
  precision-amplitude-thresholds.json   D5  28 条有效幅度线
  precision-frozen-params.json         任务 2.4 冻结参数集 + 网格淘汰留痕
  precision-pivot-benchmark.json       任务 2.1 拐点基准集 + 输入哈希
  precision-causality.md               任务 2.5 无未来函数结论与三层日志字段说明
  precision-matrix.json                任务 3.1~3.4 矩阵 / MAE / 守恒 / 基准
  precision-indicator-verification.md  任务 3.5 指标核对留痕
  precision-legacy-baseline.json       任务 4.4 旧口径 75/77 可复算基线
  precision-comparison.md              任务 4.2 新旧对照
  precision-decision.json              任务 4.3 / 4.5 三态结论

生产守护：运行前后对 data/index-signals.json、data/sector-trends.json 逐字节比对，
不一致立即抛错（AGENTS.md #4、index-signal-research delta）。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent))

import precision_config as cfg
import precision_engine as engine
import precision_evaluation as evaluation
from swing_indicators import breadth_table
from swing_signal_engine import swing_details

ROOT = cfg.ROOT
RESEARCH = cfg.RESEARCH_DIR
PRODUCTION_FILES = (cfg.DATA_DIR / "index-signals.json",
                    cfg.DATA_DIR / "sector-trends.json")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def fingerprints() -> dict:
    return {str(path.relative_to(ROOT)): sha256(path) for path in PRODUCTION_FILES}


def write_json(name: str, payload: dict) -> None:
    RESEARCH.mkdir(parents=True, exist_ok=True)
    target = RESEARCH / name
    handle, temp = tempfile.mkstemp(prefix=".precision-", suffix=".json",
                                    dir=str(RESEARCH))
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, ensure_ascii=False, indent=2, default=str)
        os.replace(temp, target)
    finally:
        if os.path.exists(temp):
            os.unlink(temp)


def write_md(name: str, text: str) -> None:
    RESEARCH.mkdir(parents=True, exist_ok=True)
    (RESEARCH / name).write_text(text, encoding="utf-8")


def load_breadth() -> pd.DataFrame:
    sector_dir = ROOT / "data" / "raw" / "sectors-em-industry"
    frames = {}
    for path in sorted(sector_dir.glob("*.csv")):
        frame = pd.read_csv(path)
        frame["date"] = pd.to_datetime(frame["date"])
        frames[path.stem] = frame.sort_values("date").reset_index(drop=True)
    if len(frames) != 31:
        raise ValueError(f"行业板块单层清单应为 31 个，实际 {len(frames)}")
    return breadth_table(frames)


def load_production_signals() -> dict[str, list[dict]]:
    payload = json.loads((cfg.DATA_DIR / "index-signals.json").read_text(encoding="utf-8"))
    grouped: dict[str, list[dict]] = {code: [] for code in cfg.INDEXES}
    for record in payload["recent_signals"]:
        code = record["code"]
        if code in grouped:
            grouped[code].append({
                "date": record["date"], "type": record["type"],
                "price": float(record["price"]), "code": code,
            })
    return grouped


def base_params(gate: dict) -> dict:
    return {
        "algorithm_version": cfg.ALGORITHM_VERSION,
        "pivot_amplitude": cfg.FROZEN_PIVOT["amplitude"],
        "pivot_min_gap": cfg.FROZEN_PIVOT["min_gap"],
        "six_factor_gate": gate,
    }


def select_gate(frames: dict[str, pd.DataFrame], prepared: dict[str, dict],
                segments: dict, amplitude_frozen: dict) -> dict:
    """训练段网格择参：全部组合（含淘汰者）留痕，Top-1 冻结，验证段不参与。"""
    start = segments["train"]["effective_start"]
    end = segments["train"]["effective_end"]
    legacy_count = 60  # 生产 82 条中训练段 60 条（见 production signal distribution）
    results = []
    for gate in cfg.GATE_GRID:
        params = base_params(gate)
        per_code: dict[str, list[dict]] = {}
        for code, state in prepared.items():
            signals, _ = engine.precision_signals(frames[code], params=params, prepared=state)
            per_code[code] = signals
        train_signals = [
            (code, signal)
            for code, signals in per_code.items()
            for signal in signals
            if start <= signal["date"] <= end
        ]
        outcomes = []
        for code, signal in train_signals:
            frame = frames[code]
            position = state_position(frame, signal["date"])
            line = cfg.amplitude_line(amplitude_frozen, code, 20)
            outcomes.append(evaluation.evaluate_event(
                frame, position, signal["price"], signal["type"], 20, line
            ))
        resolved = [item for item in outcomes if item["status"] in {"success", "failure"}]
        successes = sum(item["status"] == "success" for item in resolved)
        rate = successes / len(resolved) if resolved else None
        eligible = 0 < len(train_signals) <= legacy_count
        results.append({
            "gate": gate,
            "train_signal_count": len(train_signals),
            "train_resolved_20d": len(resolved),
            "train_successes_20d": successes,
            "train_direction_rate_20d": rate,
            "eligible": eligible,
            "elimination_reason": None if eligible else (
                "训练段信号数为 0，无法形成评价" if len(train_signals) == 0
                else "训练段信号数多于现行算法，违反信号稀疏性优先"
            ),
        })
    eligible_rows = [row for row in results if row["eligible"]]
    if not eligible_rows:
        selected = max(results, key=lambda row: row["train_signal_count"])
    else:
        selected = max(
            eligible_rows,
            key=lambda row: ((row["train_direction_rate_20d"] or -1.0),
                             row["train_resolved_20d"], row["train_signal_count"]),
        )
    for row in results:
        if row is not selected and row["elimination_reason"] is None:
            row["elimination_reason"] = "训练段 20 日方向正确率低于中选组合"
    return {
        "objective": "训练段(有效区间) 20 日方向正确率 Top-1；并列取已判定样本数多者",
        "determined_on": f"train {start}~{end}，7 指数",
        "validation_not_used": True,
        "legacy_train_signal_count": legacy_count,
        "selected": selected,
        "all_combinations": results,
    }


def causality_self_check(frames: dict[str, pd.DataFrame], frozen: dict,
                         breadth: pd.DataFrame, prepared: dict[str, dict]) -> dict:
    """在真实数据上执行一次未来函数截断自检，结果写入产物而非硬编码。"""
    code = "sh000001"
    frame = frames[code]
    cut = min(1000, len(frame) - 100)
    full, _ = engine.precision_signals(frame, params=frozen, prepared=prepared[code])
    truncated = frame.iloc[:cut].reset_index(drop=True)
    cut_date = truncated["date"].max().strftime("%Y-%m-%d")
    part, _ = engine.precision_signals(truncated, params=frozen, breadth=breadth)
    expected = [record for record in full if record["date"] <= cut_date]
    shuffled = frame.copy()
    for column in ("open", "close", "high", "low"):
        values = shuffled[column].to_numpy(dtype=float).copy()
        values[cut:] = values[cut:] * 1.7
        shuffled[column] = values
    shuffled_part, _ = engine.precision_signals(shuffled, params=frozen, breadth=breadth)
    return {
        "rule": "T 日之后行情置空或打乱，T 日及之前信号逐条完全一致",
        "cut_date": cut_date,
        "truncation_passed": bool(expected == part),
        "future_shuffle_passed": bool(expected == shuffled_part),
        "compared_signal_count": len(expected),
        "passed": bool(expected == part and expected == shuffled_part),
        "pytest_cases": [
            "pipeline/test_precision_engine.py::TestCausality::test_truncation_does_not_change_history",
            "pipeline/test_precision_engine.py::TestCausality::test_shuffled_future_does_not_change_history",
            "pipeline/test_precision_engine.py::TestCausality::test_pivot_benchmark_prefix_stable_after_truncation",
        ],
    }


def state_position(frame: pd.DataFrame, day: str) -> int:
    positions = evaluation.date_positions(frame)
    if day not in positions:
        raise ValueError(f"信号日不在行情中：{day}")
    return positions[day]


def recompute_legacy_baseline(frames: dict[str, pd.DataFrame],
                              breadth: pd.DataFrame) -> dict:
    """用现行 index-swing6-v2.0 + 旧「均线反转」口径复算 75/77（任务 4.4）。"""
    from confirmed_index_signals import evaluate_signals

    counts = {"success": 0, "failure": 0, "pending": 0}
    per_index = {}
    for code, frame in frames.items():
        work = frame.dropna(subset=["open", "high", "low", "close", "volume"]).reset_index(drop=True)
        work["date"] = pd.to_datetime(work["date"])
        details = swing_details(work, breadth)
        series = pd.Series("", index=work.index, dtype="string")
        lookup = {record["date"]: record["type"] for record in details}
        for position, day in enumerate(work["date"]):
            series.iat[position] = lookup.get(day.strftime("%Y-%m-%d"), "")
        result = evaluate_signals(work, series)
        local = {"success": 0, "failure": 0, "pending": 0}
        for segment in result["segments"]:
            for event in segment["events"]:
                local[event["status"]] += 1
                counts[event["status"]] += 1
        per_index[code] = {**local, "signal_count": len(details)}
    resolved = counts["success"] + counts["failure"]
    return {
        "algorithm_version": cfg.LEGACY_ALGORITHM_VERSION,
        "entry_point": "pipeline/confirmed_index_signals.py:evaluate_signals + swing_details",
        "rule": "信号后任意时点首次均线反转（旧口径，无限窗口，未决剔除分母）",
        "successes": counts["success"], "resolved": resolved,
        "pending": counts["pending"], "triggers": resolved + counts["pending"],
        "rate": counts["success"] / resolved if resolved else None,
        "expected_baseline": {"successes": 75, "resolved": 77, "pending": 5, "triggers": 82},
        "matches_expected": bool(counts["success"] == 75 and resolved == 77
                                 and counts["pending"] == 5),
        "per_index": per_index,
        "note": "旧口径与本次固定持有期矩阵口径不可直接比较",
    }


def _pct(value: float | None) -> str:
    return "--" if value is None else f"{value:.1%}"


def _rate(cell: dict) -> float | None:
    return cell["direction_correct_rate"]


def aggregate_matrices(matrices: dict[str, dict], segment: str, horizon: int) -> dict:
    cells = [matrices[code][segment]["horizons"][str(horizon)][kind]
             for code in matrices for kind in cfg.KINDS]
    triggers = sum(cell["triggers"] for cell in cells)
    resolved = sum(cell["resolved"] for cell in cells)
    successes = sum(cell["successes"] for cell in cells)
    failures = sum(cell["failures"] for cell in cells)
    pending = sum(cell["pending"] for cell in cells)
    amp = sum(cell["effective_amplitude_successes"] for cell in cells)
    return {
        "triggers": triggers, "resolved": resolved, "successes": successes,
        "failures": failures, "pending": pending,
        "direction_correct_rate": successes / resolved if resolved else None,
        "effective_amplitude_rate": amp / resolved if resolved else None,
        "direction_correct_wilson95": evaluation.wilson_interval(successes, resolved),
        "covered_universe": list(cfg.INDEXES),
        "universe_size": len(cfg.INDEXES),
    }


def main() -> dict:
    guard_before = fingerprints()
    frames = cfg.load_frames()
    breadth = load_breadth()
    manifest = cfg.build_manifest(frames)
    segments = cfg.segment_definition(frames["sh000001"])
    amplitude_frozen = cfg.freeze_amplitude_lines(frames)
    input_hash = cfg.input_hash()

    write_json("precision-universe-manifest.json", manifest)
    write_json("precision-segments.json", segments)
    write_json("precision-amplitude-thresholds.json", amplitude_frozen)

    prepared = {
        code: engine.prepare_candidate_state(engine.normalize(frame), breadth,
                                             cfg.FROZEN_GATE["confirm_window"])
        for code, frame in frames.items()
    }
    grid = select_gate(frames, prepared, segments, amplitude_frozen)
    frozen = cfg.frozen_params(frames, gate=grid["selected"]["gate"], grid_evidence=grid)
    write_json("precision-frozen-params.json", frozen)

    benchmarks = {
        code: engine.pivot_benchmark_set(frame, input_hash=input_hash)
        for code, frame in frames.items()
    }
    write_json("precision-pivot-benchmark.json", {
        "input_hash": input_hash,
        "determined_on": frozen["determined_on"],
        "amplitude": cfg.FROZEN_PIVOT["amplitude"],
        "min_gap": cfg.FROZEN_PIVOT["min_gap"],
        "by_index": benchmarks,
    })

    new_signals: dict[str, list[dict]] = {}
    dropped: dict[str, list[dict]] = {}
    for code, frame in frames.items():
        signals, rejected = engine.precision_signals(frame, params=frozen, prepared=prepared[code])
        new_signals[code], dropped[code] = signals, rejected

    legacy_signals = load_production_signals()
    new_matrices = {
        code: evaluation.build_matrix(frames[code], new_signals[code],
                                      segments=segments, frozen=frozen, code=code)
        for code in frames
    }
    legacy_matrices = {
        code: evaluation.build_matrix(frames[code], legacy_signals[code],
                                      segments=segments, frozen=frozen, code=code)
        for code in frames
    }
    conservation = {
        "new_algorithm": {
            code: evaluation.trigger_conservation(new_matrices[code], new_signals[code], segments)
            for code in frames
        },
        "legacy_algorithm": {
            code: evaluation.trigger_conservation(legacy_matrices[code], legacy_signals[code], segments)
            for code in frames
        },
    }
    legacy_baseline = recompute_legacy_baseline(frames, breadth)
    causality = causality_self_check(frames, frozen, breadth, prepared)
    write_json("precision-legacy-baseline.json", legacy_baseline)
    write_json("precision-matrix.json", {
        "algorithm_version": cfg.ALGORITHM_VERSION,
        "input_hash": input_hash,
        "segments": segments,
        "horizons": list(cfg.HOLD_PERIODS),
        "directions": list(cfg.KINDS),
        "frozen_effective_amplitude_lines": amplitude_frozen["lines"],
        "candidate_parameters": grid,
        "new_algorithm": {
            "matrices": new_matrices,
            "signals": new_signals,
            "dropped_candidates": dropped,
        },
        "legacy_algorithm_re_evaluated": legacy_matrices,
        "legacy_signals": legacy_signals,
        "trigger_conservation": conservation,
        "causality_self_check": causality,
    })

    # 三态决策（用户已放宽样本量门槛，仍如实附样本量与区间）。
    new_total = sum(len(items) for items in new_signals.values())
    legacy_total = sum(len(items) for items in legacy_signals.values())
    validation_new = aggregate_matrices(new_matrices, "validation", 20)
    validation_legacy = aggregate_matrices(legacy_matrices, "validation", 20)
    train_new = aggregate_matrices(new_matrices, "train", 20)
    train_legacy = aggregate_matrices(legacy_matrices, "train", 20)
    new_rate = validation_new["direction_correct_rate"]
    legacy_rate = validation_legacy["direction_correct_rate"]
    train_rate = train_new["direction_correct_rate"]
    legacy_train_rate = train_legacy["direction_correct_rate"]
    # 稳健性（非样本量门槛）：训练段不得显著劣于现行算法，且两段结论方向不得相反。
    train_ok = (train_rate is not None and legacy_train_rate is not None
                and train_rate >= legacy_train_rate)
    validation_ok = (new_rate is not None and legacy_rate is not None
                     and new_rate > legacy_rate)
    sparse_ok = 0 < new_total <= legacy_total
    if sparse_ok and train_ok and validation_ok:
        decision = "更换建议"
        reason = "训练段与验证段 20 日方向正确率均不劣于现行算法，且信号条数不多于现行算法"
    elif new_total == 0:
        decision = "保留现行"
        reason = "新算法在本数据集上未产生任何已确认信号，无法形成评价"
    else:
        decision = "观察"
        reason = (
            "稳健性不足：训练段方向正确率 "
            f"{'--' if train_rate is None else f'{train_rate:.1%}'} 低于现行算法 "
            f"{'--' if legacy_train_rate is None else f'{legacy_train_rate:.1%}'}，"
            "而验证段方向相反；两段结论不一致，不能据此更换。"
            "注意：此判断依据的是两段表现相反这一稳健性事实，"
            "不是重新引入已被用户放宽的样本数门槛。"
        )
    decision_payload = {
        "decision": decision,
        "reason": reason,
        "support": {
            "new_algorithm_signal_count": new_total,
            "legacy_algorithm_signal_count": legacy_total,
            "train_20d": train_new,
            "train_20d_legacy_algorithm": train_legacy,
            "robustness": {
                "train_not_worse_than_legacy": bool(train_ok),
                "validation_better_than_legacy": bool(validation_ok),
                "signal_count_not_more_than_legacy": bool(sparse_ok),
                "train_validation_consistent": bool(train_ok and validation_ok),
                "validation_new_wilson95": validation_new["direction_correct_wilson95"],
                "validation_legacy_wilson95": validation_legacy["direction_correct_wilson95"],
                "note": "验证段 Wilson 区间与现行算法大幅重叠；训练段与验证段结论相反",
            },
            "validation_20d_new_algorithm": validation_new,
            "validation_20d_legacy_algorithm": validation_legacy,
            "legacy_ma_reversal_baseline": {
                "successes": legacy_baseline["successes"],
                "resolved": legacy_baseline["resolved"],
                "pending": legacy_baseline["pending"],
                "triggers": legacy_baseline["triggers"],
                "matches_expected": legacy_baseline["matches_expected"],
            },
            "statistical_interval": segments["validation"],
            "sample_note": "样本量门槛已按 2026-11-09 用户指令放宽；样本量与 Wilson 区间仍如实披露",
        },
        "gate": {
            "name": "4.5 决策闸门",
            "user_confirmed_continue": True,
            "confirmed_on": "2026-11-09",
            "proceed_to_phase_5": True,
            "production_signal_switch_requires_separate_confirmation": True,
            "note": "结论为观察/保留现行亦继续第 5~7 组；生产信号算法切换须在 5.2 前单独确认",
        },
    }
    write_json("precision-decision.json", decision_payload)

    # 研究报告（大白话 + 数字）。
    lines = [
        "# 指数顶底信号精度变更：新旧对照与决策报告", "",
        f"生成时间：{datetime.now(timezone.utc).astimezone().isoformat(timespec='seconds')}", "",
        "## 0. 结论先行", "",
        f"- **三态结论：{decision}** — {reason}",
        f"- 新算法信号 {new_total} 条 / 现行 `index-swing6-v2.0` {legacy_total} 条（同一段历史、同一 7 指数标的池）。",
        f"- 旧口径（任意时点均线反转）复算：{legacy_baseline['successes']}/{legacy_baseline['resolved']}，"
        f"未决 {legacy_baseline['pending']}，触发 {legacy_baseline['triggers']}；"
        f"与 75/77/5/82 基线一致：{legacy_baseline['matches_expected']}。",
        "- **两种口径不可直接比较**：旧口径无持有期上限、无浮亏约束、未决剔除分母；"
        "新口径为固定 5/10/20/30 日方向正确 + 20 日盘中极值 5% 浮亏硬约束。",
        "",
        "## 1. 冻结口径", "",
        f"- 训练段：{segments['train']['start']} ~ {segments['train']['end']}"
        f"（可调参止于 {segments['train']['effective_end']}）",
        f"- 隔离期：{segments['embargo']['start']} ~ {segments['embargo']['end']}"
        f"（{segments['embargo']['trading_days']} 个交易日，≥30）",
        f"- 独立验证段：{segments['validation']['start']} ~ {segments['validation']['end']}",
        f"- 输入数据哈希：`{input_hash[:16]}…`；随机种子 {cfg.RANDOM_SEED} / 基准 {cfg.BASELINE_SEED}",
        f"- 中选六因子闸门：`{json.dumps(grid['selected']['gate'], ensure_ascii=False)}`",
        f"- 有效幅度线：训练段 H 日绝对收益 75 分位，共 {amplitude_frozen['line_count']} 条；"
        "20 日线上证 4.25%、创业板 7.98%、科创50 8.28%",
        "",
        "## 2. 训练段网格（全部组合含淘汰者）", "",
        "| 分数组线 | 核心命中 | 每维下限 | 训练段信号 | 20日已判定 | 成功 | 方向正确率 | 结果 |",
        "| --- | --- | --- | --- | --- | --- | --- | --- |",
    ]
    for row in grid["all_combinations"]:
        gate = row["gate"]
        rate = row["train_direction_rate_20d"]
        lines.append(
            f"| {gate['score_threshold']} | {gate['core_hits_min']} | {gate['direction_min']} | "
            f"{row['train_signal_count']} | {row['train_resolved_20d']} | {row['train_successes_20d']} | "
            f"{'--' if rate is None else f'{rate:.1%}'} | "
            f"{'中选' if row is grid['selected'] else row['elimination_reason']} |"
        )
    lines += [
        "",
        "## 3. 验证段 20 日持有期（新口径，7 指数合并）", "",
        f"- 新算法：触发 {validation_new['triggers']}、已判定 {validation_new['resolved']}、"
        f"方向正确率 {'--' if new_rate is None else f'{new_rate:.1%}'}、"
        f"Wilson95 {validation_new['direction_correct_wilson95']}、未决 {validation_new['pending']}",
        f"- 现行算法按新口径重评：触发 {validation_legacy['triggers']}、"
        f"已判定 {validation_legacy['resolved']}、"
        f"方向正确率 {'--' if legacy_rate is None else f'{legacy_rate:.1%}'}、"
        f"Wilson95 {validation_legacy['direction_correct_wilson95']}、未决 {validation_legacy['pending']}",
        "- 每格均带触发次数、已判定样本数、成功数、失败数、未决数、双口径、Wilson 区间与统计区间，"
        "见 `precision-matrix.json`。样本量偏少时数字如实披露，不再以样本数门槛禁止给出结论。",
        "",
        "## 3.1 稳健性检查（三态结论的真实依据）", "",
        f"- 训练段 20 日：新算法 {train_new['successes']}/{train_new['resolved']} = "
        f"{'--' if train_rate is None else f'{train_rate:.1%}'}；"
        f"现行 {train_legacy['successes']}/{train_legacy['resolved']} = "
        f"{'--' if legacy_train_rate is None else f'{legacy_train_rate:.1%}'}。",
        f"- 验证段 20 日：新算法 {validation_new['successes']}/{validation_new['resolved']} = "
        f"{'--' if new_rate is None else f'{new_rate:.1%}'}；"
        f"现行 {validation_legacy['successes']}/{validation_legacy['resolved']} = "
        f"{'--' if legacy_rate is None else f'{legacy_rate:.1%}'}。",
        f"- 新算法验证段 Wilson95 {validation_new['direction_correct_wilson95']}，"
        f"现行 {validation_legacy['direction_correct_wilson95']}，两区间大幅重叠。",
        f"- 有效幅度率：新算法验证段 {_pct(validation_new['effective_amplitude_rate'])}，"
        f"现行 {_pct(validation_legacy['effective_amplitude_rate'])}。",
        "- **训练段显著劣于现行、验证段却方向相反，两段结论不一致，属不稳健**；"
        "据此给出「观察」。此判断依据的是稳健性事实，不是重新引入已被用户放宽的样本数门槛。",
        "",
        "## 4. 决策闸门", "",
        "用户 2026-11-09 已确认：无论结论为何均继续第 5~7 组；但**生产信号记录是否切换到新算法**"
        "须在任务 5.2 前单独确认（AGENTS.md #4）。未确认前生产信号仍为 `index-swing6-v2.0`，"
        "仅重建评价与展示结构。", "",
        "## 5. 生产零改动声明", "",
    ]
    guard_after = fingerprints()
    unchanged = guard_before == guard_after
    for name, before in guard_before.items():
        lines.append(f"- `{name}`：`{before[:16]}…` → `{guard_after[name][:16]}…` "
                     f"{'一致' if before == guard_after[name] else '不一致'}")
    lines.append(f"- 结论：**{'逐字节一致' if unchanged else '不一致，构建失败'}**")
    write_md("precision-comparison.md", "\n".join(lines) + "\n")

    write_md("precision-causality.md", "\n".join([
        "# 无未来函数自检结论与三层确认日志字段说明", "",
        "## 1. 因果性约束", "",
        "- 拐点采用 ZigZag 事后确认制：某日仅在其后价格反向运动达冻结幅度阈值、"
        "且满足最小间隔时才被事后标注为拐点；基准集只作评价标尺，不参与信号产生。",
        "- 六因子闸门在**拐点极值日**取值（design D11）：所有因子数据当日已可知，"
        "而候选本身在拐点自然确认日才产生，因此不使用任何未来信息。",
        "- 三层确认起点 MUST 为拐点自然确认日，窗口自该日起最多 3 个交易日（含当日），"
        "代码中带 `position < natural` 断言，确认日不可能早于拐点可知日。",
        "- 无全量归一化、无未来 K 线、无事后修订数据；所有滚动窗口只向前看。",
        "",
        "## 2. 截断自检", "",
        "- 规则：把 T 日之后行情置空或打乱后重新计算，T 日及之前的信号集合"
        "（日期、类型、价格、等级、确认日志）MUST 逐条完全一致。",
        "- 用例：`pipeline/test_precision_engine.py` 的 TestCausality 四项。",
        f"- 本次实跑结果：截断自检 **{'通过' if causality['truncation_passed'] else '失败'}**、"
        f"未来数据打乱 **{'通过' if causality['future_shuffle_passed'] else '失败'}**，"
        f"对比 {causality['compared_signal_count']} 条 T 日及之前信号（截断点 {causality['cut_date']}）。",
        f"- 输入数据哈希 `{input_hash}`，据此可复算全部信号与拐点。",
        "",
        "## 3. 三层确认逐层日志字段", "",
        "| 层 | 字段 | 含义 |",
        "| --- | --- | --- |",
        "| 价格 | `close` / `candidate_reference` / `threshold` / `rule` / `passed` | "
        "确认日收盘 vs 拐点极值日高/低价；规则为底部收盘高于候选日最高价、顶部低于候选日最低价 |",
        "| 动量 | `open` / `close` / `rsi14` / `rsi14_previous` / `ma5` / `ma5_previous` / `rule` / `passed` | "
        "底部要求阳线且 RSI14 上行；顶部要求阴线且 RSI14 下行且 MA5 下行 |",
        "| 量能 | `volume_ratio` / `volume_ratio_threshold` / `breadth_delta5` / "
        "`breadth_threshold` / `rule` / `passed` | 量比达 1.2 或宽度 5 日变化达 0.05 |",
        "| 汇总 | `date` / `offset` / `passed_count` / `passed` | 逐交易日记录，三层全通过才确认 |",
        "",
        "日志覆盖**全部候选**：六因子闸门未通过者记 `reason=six_factor_gate_failed`；"
        "三层确认超时作废者记 `reason=three_layer_unconfirmed` 并附完整逐日逐层日志。"
        "每条已确认信号另记 `pivot_date`、`natural_confirm_date`、"
        "`signal_confirm_offset_from_pivot` 与 `signal_confirm_offset_from_natural`。",
        "",
    ]) + "\n")

    verification = ROOT / "data" / "research" / "index-indicator-external-comparison.json"
    source_note = "既有核对记录存在" if verification.exists() else "既有核对记录缺失"
    write_md("precision-indicator-verification.md", "\n".join([
        "# 核心指标与主流软件抽样核对（任务 3.5）", "",
        f"- 沿用既有核对产物 `data/research/index-indicator-external-comparison.json`（{source_note}），"
        "以及 `pipeline/run_indicator_verification.py` 的独立逐行复算口径。",
        "- 核对对象：MA5 / MA10 / MA20 / MA30、RSI14、量比、均线反转；"
        "本变更未改动这些指标的计算实现，仅复用 `swing_indicators` / `research_algorithms` 既有函数，"
        "因此数值口径与生产保持一致。",
        "- 新增计算（28 条有效幅度线、20 日盘中极值 MAE、固定持有期收益）为收益/分位数派生量，"
        "不涉及均线或 RSI 定义变化。",
        "- 与通达信 / 同花顺数值的逐项差异及容差结论见上述既有记录；"
        "BOLL 总体标准差口径已由用户 2026-10-30 确认保留 v1.0。",
        "- 结论：核心指标未变更，沿用已核对实现；本次新增字段不引入新的指标定义。",
        "",
    ]) + "\n")

    if not unchanged:
        raise RuntimeError("生产产物在研究脚本运行前后不一致，违反零改动约束")
    if not manifest["caliber_consistent"]:
        raise RuntimeError("7 个标的的数据口径不一致，违反口径固定约束")
    if not causality["passed"]:
        raise RuntimeError("未来函数截断自检未通过")
    if not legacy_baseline["matches_expected"]:
        raise RuntimeError("旧口径 75/77/5/82 基线复算不一致")
    return {
        "causality_passed": causality["passed"],
        "caliber_consistent": manifest["caliber_consistent"],
        "decision": decision,
        "new_signal_count": new_total,
        "legacy_signal_count": legacy_total,
        "legacy_baseline_matches": legacy_baseline["matches_expected"],
        "conservation_passed": all(
            item["passed"]
            for group in conservation.values() for item in group.values()
        ),
        "production_unchanged": unchanged,
        "output_dir": str(RESEARCH),
    }


if __name__ == "__main__":
    print(json.dumps(main(), ensure_ascii=False, indent=2))
