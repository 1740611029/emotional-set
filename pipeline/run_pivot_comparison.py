"""新旧拐点算法对照决策入口（离线调研，绝不改动任何生产信号）。

串起：真实拐点基准集 → 全部候选（含三层确认变体与辅助增益版）→ 现行 `index-swing6-v2.0`
（同基准、同口径、同"确认日"语义）→ 验证段一次性评估，产出：

- `data/research/index-pivot-comparison.json`（结构化全量结果）
- `data/research/index-pivot-comparison.md`（人类可读决策报告）

生产保护（design D6 / 任务 4.2）：入口执行前后对 `data/index-signals.json`、
`data/sector-trends.json`、`data/raw/indices-ths/*.csv` 做 sha256+mtime 指纹比对，
任一不一致立即报错退出。本模块只读生产行情，产物只写 `data/research/`。

三态结论（spec 需求"三态决策结论"）：
- `更换建议`：候选各主指标（捕捉率、主达标率、滞后中位、点位偏差中位）在验证段均不劣于现行且样本充分；
- `保留现行`：候选主达标率低于现行；
- `观察`：结果 mixed 或验证段拐点样本 < 30（强制上限，禁止出"更换建议"）。
最终采纳权在用户，本调研不更换生产算法。
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

import pivot_algorithms as alg
import pivot_aux_factors as aux
import pivot_benchmark as bench
import pivot_eval as ev
from research_algorithms import read_sector_csv, wilson_interval
from swing_indicators import breadth_table
from swing_signal_engine import ALGORITHM_VERSION, swing_details

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = ROOT / "data" / "research"
COMPARISON_JSON = RESEARCH_DIR / "index-pivot-comparison.json"
COMPARISON_MD = RESEARCH_DIR / "index-pivot-comparison.md"

INCUMBENT = f"{ALGORITHM_VERSION}(current)"
MIN_VALIDATION_SAMPLES = ev.SMALL_SAMPLE            # 30，与评价模块小样本阈值一致
RECALL_INFLATION_RATIO = 3                          # 信号数 > 3×拐点数视为过量触发
VERDICTS = ("更换建议", "观察", "保留现行")
SEGMENTS = ("train", "validation")

# 生产文件指纹防护对象（任务 4.2）：三份生产 JSON + 全部指数日线 CSV。
GUARDED = [ROOT / "data" / name for name in ("index-signals.json", "sector-trends.json")]
GUARDED += sorted(bench.INDEX_RAW.glob("*.csv"))


def fingerprints() -> dict:
    """对受保护生产文件取 sha256+mtime_ns 指纹；文件不存在则跳过。"""
    result = {}
    for path in GUARDED:
        if not path.exists():
            continue
        result[path.relative_to(ROOT).as_posix()] = {
            "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mtime_ns": path.stat().st_mtime_ns,
        }
    return result


def load_breadth() -> pd.DataFrame | None:
    """行业宽度输入与生产同源（东财行业板块日线）；缺失返回 None，候选仍纯价格结构运行。"""
    folder = ROOT / "data" / "raw" / "sectors-em-industry"
    paths = sorted(folder.glob("*.csv")) if folder.exists() else []
    if not paths:
        return None
    return breadth_table({path.stem: read_sector_csv(path) for path in paths})


def incumbent_signals(frame: pd.DataFrame, breadth: pd.DataFrame | None) -> list[dict]:
    """现行 index-swing6-v2.0 信号，取确认日语义的 date/type/price（与候选同口径）。"""
    return [{"date": row["date"], "type": row["type"], "price": float(row["price"])}
            for row in swing_details(frame, breadth)]


def build_signal_sets(frames: dict[str, pd.DataFrame], breadth: pd.DataFrame | None) -> tuple[dict, dict]:
    """生成 {算法: {code: [信号]}} 与 {算法: {code: [作废候选日志]}}，含现行基线。"""
    signal_sets: dict[str, dict[str, list[dict]]] = {}
    dropped: dict[str, dict[str, list[dict]]] = {}
    for name in alg.PREREGISTERED["algorithm_params"]:
        params = alg.frozen_params(name)
        per_code: dict[str, list[dict]] = {}
        drop_code: dict[str, list[dict]] = {}
        for code, frame in frames.items():
            if name == "pivot-zigzag+confirm3":
                signals, logs = alg.zigzag_confirm3_signals(frame, breadth=breadth, **params)
                per_code[code] = signals
                drop_code[code] = logs
            else:
                per_code[code] = alg.run_algorithm(name, frame, **params)
        signal_sets[name] = per_code
        dropped[name] = drop_code
    signal_sets[INCUMBENT] = {code: incumbent_signals(frame, breadth) for code, frame in frames.items()}
    return signal_sets, dropped


def _wilson(hits: int, total: int) -> dict:
    return {"hits": int(hits), "total": int(total),
            "rate": (hits / total) if total else None,
            "wilson95": wilson_interval(hits, total),
            "small_sample": bool(total < MIN_VALIDATION_SAMPLES)}


def _pooled_quantiles(values: list[float]) -> dict:
    clean = [float(value) for value in values if value is not None and np.isfinite(value)]
    return {
        "median": float(np.percentile(clean, 50)) if clean else None,
        "p90": float(np.percentile(clean, 90)) if clean else None,
        "counted": len(clean),
    }


def _empty_metric() -> dict:
    return {"pivot_samples": 0, "signal_count": 0,
            "capture": {"hits": 0, "total": 0}, "main_hit": {"hits": 0, "total": 0},
            "precision": {"hits": 0, "total": 0},
            "lag_values": [], "dev_values": [], "interval": [None, None]}


def _aggregate(per_code: dict[str, dict]) -> dict:
    """把逐指数的分段结果按计数池化（ rates 用命中/分母求和，分位数用原始值池化）。"""
    buckets = {segment: _empty_metric() for segment in SEGMENTS}
    for code, result in per_code.items():
        for segment in SEGMENTS:
            metrics = result["segments"][segment]
            bucket = buckets[segment]
            bucket["pivot_samples"] += metrics["pivot_samples"]
            bucket["signal_count"] += metrics["signal_count"]
            for key in ("capture", "main_hit", "precision"):
                bucket[key]["hits"] += metrics[key]["hits"]
                bucket[key]["total"] += metrics[key]["total"]
            bucket["lag_values"].extend(metrics["lag"]["values"])
            bucket["dev_values"].extend(metrics["price_deviation"]["values"])
            start, end = bucket["interval"]
            day_start, day_end = metrics["interval"]["start"], metrics["interval"]["end"]
            if day_start:
                start = min(start, day_start) if start else day_start
            if day_end:
                end = max(end, day_end) if end else day_end
            bucket["interval"] = [start, end]
    summary = {}
    for segment, bucket in buckets.items():
        lag = _pooled_quantiles(bucket["lag_values"])
        dev = _pooled_quantiles(bucket["dev_values"])
        summary[segment] = {
            "pivot_samples": bucket["pivot_samples"],
            "signal_count": bucket["signal_count"],
            "capture": _wilson(bucket["capture"]["hits"], bucket["capture"]["total"]),
            "main_hit": _wilson(bucket["main_hit"]["hits"], bucket["main_hit"]["total"]),
            "precision": _wilson(bucket["precision"]["hits"], bucket["precision"]["total"]),
            "lag": lag, "price_deviation": dev,
            "interval": {"start": bucket["interval"][0], "end": bucket["interval"][1]},
        }
    return summary


def _rate(metric: dict) -> float:
    return metric["rate"] or 0.0


def _not_worse(candidate, incumbent, key: str, *, lower_is_better: bool = False) -> bool:
    if lower_is_better:
        left, right = candidate[key]["median"], incumbent[key]["median"]
        if left is None:
            return right is None
        return right is not None and left <= right
    return _rate(candidate[key]) >= _rate(incumbent[key])


def decide(candidate: dict, incumbent: dict) -> dict:
    """按冻结口径给出候选三态结论；验证段拐点样本 < 30 强制不高于"观察"。"""
    val = candidate["validation"]
    inc = incumbent["validation"]
    samples = val["pivot_samples"]
    recall_heavy = val["signal_count"] > RECALL_INFLATION_RATIO * max(samples, 1)
    base = {
        "validation_pivot_samples": samples,
        "candidate_main_hit": val["main_hit"]["rate"],
        "incumbent_main_hit": inc["main_hit"]["rate"],
        "candidate_capture": val["capture"]["rate"],
        "incumbent_capture": inc["capture"]["rate"],
        "candidate_precision": val["precision"]["rate"],
        "incumbent_precision": inc["precision"]["rate"],
        "recall_inflation_flag": recall_heavy,
    }
    if samples < MIN_VALIDATION_SAMPLES:
        return {**base, "verdict": "观察",
                "reason": (f"验证段拐点样本 n={samples} < {MIN_VALIDATION_SAMPLES}，统计力不足，"
                           f"结论强制不高于观察（AGENTS.md 第 8 条）")}
    core_ok = (
        _not_worse(val, inc, "capture")
        and _not_worse(val, inc, "main_hit")
        and _not_worse(val, inc, "lag", lower_is_better=True)
        and _not_worse(val, inc, "price_deviation", lower_is_better=True)
    )
    precision_ok = _not_worse(val, inc, "precision")
    if core_ok and precision_ok:
        verdict, reason = "更换建议", (
            f"验证段四项主指标均不劣于现行（主达标 {_pct(val['main_hit']['rate'])} vs "
            f"{_pct(inc['main_hit']['rate'])}，捕捉 {_pct(val['capture']['rate'])} vs "
            f"{_pct(inc['capture']['rate'])}），且精确度 {_pct(val['precision']['rate'])} 不低于现行 "
            f"{_pct(inc['precision']['rate'])}（n={samples}）")
    elif core_ok and not precision_ok:
        # 精确度硬门槛：靠过量触发刷高召回但命中质量不及现行，不得直接建议更换
        verdict, reason = "观察", (
            f"四项主指标不劣于现行，但精确度 {_pct(val['precision']['rate'])} 低于现行 "
            f"{_pct(inc['precision']['rate'])}（信号量 {val['signal_count']} vs 拐点数 {samples}），"
            f"疑似靠过量触发刷高召回，精确度硬门槛下降级为观察（n={samples}）")
    elif _rate(val["main_hit"]) < _rate(inc["main_hit"]):
        verdict, reason = "保留现行", (
            f"验证段主达标率 {_pct(val['main_hit']['rate'])} 低于现行 {_pct(inc['main_hit']['rate'])}，"
            f"候选不优于现行（n={samples}）")
    else:
        verdict, reason = "观察", (
            f"指标表现 mixed（主达标不劣于现行但其余指标存在劣项），证据不足以建议更换（n={samples}）")
    if recall_heavy:
        reason += (f"；注意：信号量 {val['signal_count']} 远超拐点数 {samples}，"
                   f"捕捉/主达标为召回型指标不因过量触发降分，精确度 {_pct(val['precision']['rate'])}"
                   f"（现行 {_pct(inc['precision']['rate'])}）须一并判读")
    return {**base, "verdict": verdict, "reason": reason}


def _aux_stats(signals_by_code, aux_scores_by_code, benchmark_by_code, weights, M) -> dict:
    grades = {"A": 0, "B": 0, "C": 0}
    hit_gains: list[float] = []
    miss_gains: list[float] = []
    gains: list[float] = []
    for code, signals in signals_by_code.items():
        scores = aux_scores_by_code.get(code)
        if signals is None or scores is None:
            continue
        annotated = aux.annotate_signals(signals, scores, weights)
        pivots = benchmark_by_code.get(code, [])
        signal_hit = _hit_mask(pivots, signals, code, M)
        for record, hit in zip(annotated, signal_hit):
            grades[record["aux_grade"]] += 1
            gains.append(record["aux_gain"])
            (hit_gains if hit else miss_gains).append(record["aux_gain"])
    mean = lambda part: round(float(np.mean(part)), 4) if part else None  # noqa: E731
    discrimination = (mean(hit_gains) - mean(miss_gains)
                      if mean(hit_gains) is not None and mean(miss_gains) is not None else None)
    return {"grade_counts": grades, "mean_aux_gain": mean(gains),
            "mean_aux_gain_hit": mean(hit_gains), "mean_aux_gain_miss": mean(miss_gains),
            "discrimination": round(discrimination, 4) if discrimination is not None else None,
            "note": "辅助只加分不否决；分级与增益为对照列，不影响捕捉/主达标/精确度等信号集指标"}


def _hit_mask(pivots, signals, code, M) -> list[bool]:
    """逐信号判定是否为"命中"（同类拐点 ±M 交易日内）。用真实指数行情的交易日日历定位。"""
    frame_path = bench.INDEX_RAW / f"{code}.csv"
    if not frame_path.exists():
        return [False] * len(signals)
    frame = pd.read_csv(frame_path)
    frame["date"] = pd.to_datetime(frame["date"])
    positions = ev.trading_positions(frame)
    pivot_pos = {kind: [] for kind in ev.KINDS}
    for pivot in pivots:
        pos = positions.get(pivot["date"])
        if pos is not None and pivot["type"] in pivot_pos:
            pivot_pos[pivot["type"]].append(pos)
    mask = []
    for signal in signals:
        pos = positions.get(signal["date"])
        hit = pos is not None and any(abs(p - pos) <= M for p in pivot_pos.get(signal["type"], []))
        mask.append(bool(hit))
    return mask


def _clean(value):
    """numpy 标量 / NaN / inf → 可 JSON 序列化值。"""
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if not np.isfinite(number) else round(number, 6)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def _pct(value) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _days(value) -> str:
    return "—" if value is None else f"{value:.1f}"


def build_comparison(frames: dict[str, pd.DataFrame], breadth: pd.DataFrame | None, *,
                     aux_scores_by_code: dict[str, dict] | None = None) -> dict:
    """核心对照计算：全部候选 + 现行，同基准同口径，训练/验证分段 + 三档敏感性 + 三态结论。"""
    train_start, train_end = bench.TRAIN_START, bench.TRAIN_END
    windows = ev.FROZEN_WINDOWS
    M = windows["M"]
    benchmark = bench.build_benchmark(frames)  # 冻结阈值（5% / 8d）
    signal_sets, dropped = build_signal_sets(frames, breadth)
    benchmark_by_amplitude = {
        amplitude: bench.build_benchmark(frames, params={"amplitude": amplitude,
                                                         "min_gap": bench.FROZEN_PARAMS["min_gap"]})
        for amplitude in bench.AMPLITUDE_GRID
    }

    candidates: dict[str, dict] = {}
    for name, signals_by_code in signal_sets.items():
        per_code = {code: ev.evaluate_signal_set(
            benchmark.get(code, []), signals_by_code.get(code, []), frames[code],
            train_start=train_start, train_end=train_end, windows=windows) for code in frames}
        aggregate = _aggregate(per_code)
        entry = {
            "definition": alg.ALGORITHMS.get(name, {}).get("definition", "现行生产算法（六因子三层确认）"),
            "params": (alg.frozen_params(name) if name in alg.PREREGISTERED["algorithm_params"]
                       else {"version": ALGORITHM_VERSION}),
            "data_deps": alg.ALGORITHMS.get(name, {}).get("data_deps", ("指数日线 OHLCV", "行业板块宽度 breadth")),
            "aggregate": aggregate,
            "sensitivity": ev.build_sensitivity(frames, benchmark_by_amplitude, signals_by_code,
                                                train_start=train_start, train_end=train_end, windows=windows),
        }
        if name in dropped and any(dropped[name].values()):
            entry["dropped_candidates"] = {code: len(rows) for code, rows in dropped[name].items() if rows}
        if aux_scores_by_code is not None and name != INCUMBENT:
            entry["aux_grading"] = _aux_stats(signals_by_code, aux_scores_by_code, benchmark,
                                              alg.PREREGISTERED["aux_weights"], M)
        candidates[name] = entry

    incumbent_entry = candidates[INCUMBENT]
    for name, entry in candidates.items():
        if name == INCUMBENT:
            entry["verdict"] = "现行基线"
            entry["verdict_detail"] = {"reason": "现行生产算法，作为对照基准不参与三态判定"}
            continue
        entry["verdict_detail"] = decide(entry["aggregate"], incumbent_entry["aggregate"])
        entry["verdict"] = entry["verdict_detail"]["verdict"]

    validation_samples = incumbent_entry["aggregate"]["validation"]["pivot_samples"]
    return _clean({
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "purpose": "指数点位拐点算法对照决策报告（离线调研，不改动生产信号）",
        "incumbent_version": ALGORITHM_VERSION,
        "frozen": {
            "benchmark_params": bench.FROZEN_PARAMS,
            "eval_windows": ev.FROZEN_WINDOWS,
            "algorithm_params": alg.PREREGISTERED["algorithm_params"],
            "aux_weights": alg.PREREGISTERED["aux_weights"],
            "aux_discrimination": alg.PREREGISTERED["aux_discrimination"],
        },
        "train_segment": [train_start.strftime("%Y-%m-%d"), train_end.strftime("%Y-%m-%d")],
        "validation_segment_start": bench.VALID_START.strftime("%Y-%m-%d"),
        "validation_pivot_samples": validation_samples,
        "small_sample_guard": validation_samples < MIN_VALIDATION_SAMPLES,
        "input_hash": bench.input_hash(tuple(frames)),
        "metrics_note": ("capture/main_hit 以基准拐点为分母（召回型），不惩罚过量触发；"
                         "precision 以信号为分母（精确型），并列展示防误导"),
        "candidates": candidates,
    })


def render_markdown(report: dict) -> str:
    frozen = report["frozen"]
    lines = [
        "# 指数点位拐点算法对照决策报告", "",
        f"生成时间：{report['generated_at']}", "",
        "## 0. 结论先行", "",
        "本调研**不改动生产信号算法**，现行 `index-swing6-v2.0` 保持不变；下表只回答"
        "「哪个拐点候选是否值得更换现行算法」，最终采纳权在用户。", "",
        "| 候选算法 | 验证段主达标 | 现行主达标 | 验证段捕捉 | 精确度(候选/现行) | 验证段拐点样本 | 结论 |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    incumbent = report["candidates"][report["incumbent_version"] + "(current)"]
    inc_val = incumbent["aggregate"]["validation"]
    for name, entry in report["candidates"].items():
        if name.startswith(report["incumbent_version"]):
            continue
        val = entry["aggregate"]["validation"]
        lines.append(f"| {name} | {_pct(val['main_hit']['rate'])} | {_pct(inc_val['main_hit']['rate'])} | "
                     f"{_pct(val['capture']['rate'])} | {_pct(val['precision']['rate'])} / "
                     f"{_pct(inc_val['precision']['rate'])} | {val['pivot_samples']} | {entry['verdict']} |")
    if report["small_sample_guard"]:
        lines += ["", f"> 验证段汇总拐点样本 {report['validation_pivot_samples']} < "
                      f"{MIN_VALIDATION_SAMPLES}，所有候选结论强制不高于「观察」。"]

    lines += ["", "## 1. 冻结口径（不可事后放宽）", "",
              "| 项目 | 取值 |", "| --- | --- |",
              f"| 基准 ZigZag 阈值 | amplitude={frozen['benchmark_params']['amplitude']:.0%}, "
              f"min_gap={frozen['benchmark_params']['min_gap']}d |",
              f"| 捕捉窗口 M | ±{frozen['eval_windows']['M']} 交易日 |",
              f"| 主达标提前量 N | {frozen['eval_windows']['N']} 交易日（确认后≤{frozen['eval_windows']['confirmation_cap']}d） |",
              f"| 训练段 | {report['train_segment'][0]} ~ {report['train_segment'][1]} |",
              f"| 验证段 | {report['validation_segment_start']} 起 |",
              "", f"基准集输入哈希：`{report['input_hash'][:16]}…`（行情变化需重建）。", ""]

    lines += ["## 2. 逐候选并列指标（训练段 / 验证段）", ""]
    for name, entry in report["candidates"].items():
        lines += [f"### {name}", "", f"- 定义：{entry['definition']}",
                  f"- 参数：{json.dumps(entry['params'], ensure_ascii=False)}",
                  f"- 数据依赖：{', '.join(entry['data_deps'])}", "",
                  "| 段 | 捕捉率 | 主达标率 | 滞后中位(d) | 滞后P90(d) | 点位偏差中位 | 精确度 | 信号数 | 拐点样本 | 区间 |",
                  "| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |"]
        for segment in SEGMENTS:
            metrics = entry["aggregate"][segment]
            interval = metrics["interval"]
            lines.append(f"| {segment} | {_pct(metrics['capture']['rate'])} | {_pct(metrics['main_hit']['rate'])} | "
                         f"{_days(metrics['lag']['median'])} | {_days(metrics['lag']['p90'])} | "
                         f"{_pct(metrics['price_deviation']['median'])} | {_pct(metrics['precision']['rate'])} | "
                         f"{metrics['signal_count']} | {metrics['pivot_samples']} | "
                         f"{interval['start']}~{interval['end']} |")
        if "dropped_candidates" in entry:
            lines.append(f"- 三层确认作废候选数（逐指数）：{entry['dropped_candidates']}")
        if name != report["incumbent_version"] + "(current)":
            detail = entry["verdict_detail"]
            wilson = entry["aggregate"]["validation"]["main_hit"]["wilson95"]
            lines.append(f"- **结论：{entry['verdict']}** — {detail['reason']}")
            if wilson and wilson[0] is not None:
                lines.append(f"- 验证段主达标 Wilson95：[{_pct(wilson[0])}, {_pct(wilson[1])}]")
        if "aux_grading" in entry:
            auxg = entry["aux_grading"]
            lines.append(f"- 辅助增益分级：A/B/C = {auxg['grade_counts']['A']}/"
                         f"{auxg['grade_counts']['B']}/{auxg['grade_counts']['C']}，"
                         f"平均增益 {auxg['mean_aux_gain']}（命中 {auxg['mean_aux_gain_hit']} / "
                         f"未命中 {auxg['mean_aux_gain_miss']}，判别 {auxg['discrimination']}）")
        lines.append("")

    lines += ["## 3. 基准阈值三档敏感性（捕捉率 / 主达标率，训练段→验证段）", "",
              "| 候选 | 3% train→val | 5% train→val | 7% train→val |", "| --- | --- | --- | --- |"]
    for name, entry in report["candidates"].items():
        tiers = entry["sensitivity"]

        def cell(amplitude):
            if amplitude not in tiers:
                return "—"
            tier = tiers[amplitude]
            return (f"{_pct(tier['train']['capture']['rate'])}/{_pct(tier['train']['main_hit']['rate'])} → "
                    f"{_pct(tier['validation']['capture']['rate'])}/{_pct(tier['validation']['main_hit']['rate'])}")
        lines.append(f"| {name} | {cell('3%')} | {cell('5%')} | {cell('7%')} |")

    lines += ["", "## 4. 生产零改动声明", "",
              "本调研全流程只读生产行情，产物仅落 `data/research/`，运行前后对 "
              "`data/index-signals.json`、`data/sector-trends.json`、`data/raw/indices-ths/*.csv` "
              "做 sha256+mtime 指纹比对，必须完全一致（见 JSON 的 `fingerprint_guard` 字段）。", "",
              "## 5. 读法与局限", "",
              "1. **召回型高分需谨慎 + 精确度硬门槛**：捕捉率/主达标率以拐点为分母，过量触发（如高频分形）可刷高召回；"
              "因此「更换建议」除四项主指标不劣于现行外，**还要求候选精确度不低于现行**，仅靠超发信号刷召回的候选会因精确度门槛降为「观察」。",
              "2. **验证段样本可能偏小**：2025 起拐点样本不足 30 时，结论强制不高于「观察」，不得据此更换算法。",
              "3. **现行算法非拐点定位器**：其设计目标是情绪共振波段确认，与真实价格拐点的捕捉/滞后可能天然偏差大，"
              "对照目的正是量化这一差异。",
              "4. **辅助因子只加分不否决**：分级与增益为对照信息，P2 外围（隔夜标普500）无本地数据源全程记 NaN。",
              "5. **阈值稳健性**：见第 3 节三档敏感性；结论若随阈值大幅摆动应视为不稳健。", ""]
    return "\n".join(lines)


def write_products(report: dict, *, json_path: Path = COMPARISON_JSON,
                   md_path: Path = COMPARISON_MD) -> tuple[Path, Path]:
    """原子写 JSON 到研究目录（不落生产路径），并渲染 Markdown 报告。"""
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _clean(report)
    descriptor, temp_name = tempfile.mkstemp(prefix=json_path.name + ".", suffix=".tmp", dir=json_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, json_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    md_path.write_text(render_markdown(payload), encoding="utf-8")
    return json_path, md_path


def main() -> None:
    before = fingerprints()
    frames = bench.load_index_frames()
    breadth = load_breadth()
    aux_scores_by_code = {code: aux.build_aux_scores(frame, breadth) for code, frame in frames.items()}
    report = build_comparison(frames, breadth, aux_scores_by_code=aux_scores_by_code)
    after = fingerprints()
    if before != after:
        changed = [key for key in before if before[key] != after.get(key)]
        raise RuntimeError(f"检测到生产文件被改动，立即中止：{changed}")
    report["fingerprint_guard"] = {
        "guarded_files": sorted(before), "unchanged": True,
        "before": before, "after": after,
    }
    json_path, md_path = write_products(report)
    print(f"已写入 {json_path}")
    print(f"已写入 {md_path}")
    for name, entry in report["candidates"].items():
        verdict = entry.get("verdict", "")
        print(f"  {name}: {verdict} {entry.get('verdict_detail', {}).get('reason', '')}")


if __name__ == "__main__":
    main()
