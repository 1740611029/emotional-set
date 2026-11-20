"""外部低滞后波段候选的本地硬门槛筛选；不写生产产物。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipeline") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipeline"))

import precision_config as pc
from bigwave_bottom_engine import bottom_details
from build_dashboard import load_breadth
from external_wave_candidates import EXTERNAL_FAMILIES, external_candidate_points, external_specs
from run_top_bottom_optimization import (
    CONFIRM_GRID, COOLDOWN, OUTPUT, json_safe, research_input_hash, segment_bounds,
)
from run_wave_return_comparison import confirm_text, flatten_events, flatten_summary, num, pct
from swing_signal_engine import prepare, swing_details
from top_bottom_candidate_algorithms import KINDS, candidate_points, confirm_candidates, spec_id
from top_bottom_closed_loop import RISK_LINES, evaluate_closed_loop
from wave_return_diagnostics import build_oracle_waves, evaluate_oracle

DEFAULT_CODES = ("sz399006", "sh000688")
REFERENCE_SPEC = {"family": "MACD交叉", "params": {"zero_filter": True}}


def points_for_spec(frame: pd.DataFrame, spec: dict) -> dict[str, list[tuple[int, int]]]:
    if spec["family"] in EXTERNAL_FAMILIES:
        return external_candidate_points(frame, spec)
    return candidate_points(frame, spec)


def build_screen_configurations(frame: pd.DataFrame, state: dict, specs: list[dict], code: str) -> list[dict]:
    configurations = []
    for index, spec in enumerate(specs, 1):
        points = points_for_spec(frame, spec)
        algorithm = spec_id(spec)
        for confirm in CONFIRM_GRID:
            events = {
                kind: confirm_candidates(frame, state, points[kind], kind, confirm, COOLDOWN)
                for kind in KINDS
            }
            configurations.append({
                "algorithm": algorithm,
                "family": spec["family"],
                "params": spec["params"],
                "confirm": confirm,
                "source": "reference" if spec["family"] == REFERENCE_SPEC["family"] else "external",
                "events": events,
            })
        if index % 4 == 0 or index == len(specs):
            print(f"  已生成 {index}/{len(specs)} 个参数算法", flush=True)
    return configurations


def present(value) -> bool:
    return value is not None and not pd.isna(value)


def passes_final_gate(row: dict) -> bool:
    required_values = [
        "train_own_rate", "train_own_wilson_low", "train_own_avg_closed_exit_return",
        "validation_own_rate", "validation_own_avg_closed_exit_return",
        "train_oracle_avg_exit_return", "train_oracle_avg_abs_delay_days",
        "train_oracle_avg_lag_price_move", "validation_oracle_avg_exit_return",
        "validation_oracle_avg_abs_delay_days", "validation_oracle_avg_lag_price_move",
    ]
    if not all(present(row[key]) for key in required_values):
        return False
    checks = [
        row["train_own_resolved"] >= 20,
        row["train_own_active_years"] >= 2,
        row["train_own_rate"] >= 0.70,
        row["train_own_wilson_low"] >= 0.50,
        row["train_own_avg_closed_exit_return"] > 0,
        row["validation_own_resolved"] >= 5,
        row["validation_own_rate"] >= 0.60,
        row["validation_own_avg_closed_exit_return"] > 0,
        row["train_oracle_closed"] >= 20,
        row["train_oracle_avg_exit_return"] > 0,
        row["train_oracle_avg_abs_delay_days"] <= 20,
        row["train_oracle_avg_lag_price_move"] <= 0.10,
        row["validation_oracle_closed"] >= 3,
        row["validation_oracle_avg_exit_return"] > 0,
        row["validation_oracle_avg_abs_delay_days"] <= 20,
        row["validation_oracle_avg_lag_price_move"] <= 0.10,
    ]
    return all(checks)


def screen_key(row: dict) -> tuple:
    return (
        bool(row["final_gate_passed"]),
        row["train_own_wilson_low"] if present(row["train_own_wilson_low"]) else -1,
        row["train_own_rate"] if present(row["train_own_rate"]) else -1,
        row["train_own_avg_closed_exit_return"] if present(row["train_own_avg_closed_exit_return"]) else -999,
        row["train_oracle_avg_exit_return"] if present(row["train_oracle_avg_exit_return"]) else -999,
        -(row["train_oracle_avg_abs_delay_days"] if present(row["train_oracle_avg_abs_delay_days"]) else 999),
    )


def accuracy(row: dict, segment: str) -> str:
    resolved = row[f"{segment}_own_resolved"]
    rate = row[f"{segment}_own_rate"]
    low = row[f"{segment}_own_wilson_low"]
    if not present(rate):
        return f"—（0/{row[f'{segment}_own_total']}）"
    return f"{rate * 100:.1f}%（{resolved}判定）[{low * 100:.1f}%+]"


def build_report(payload: dict, rows: list[dict]) -> str:
    lines = [
        "# 外部低滞后波段信号最终筛选", "",
        f"- 数据截止：{payload['data_end']}；输入哈希：`{payload['input_hash']}`。",
        "- 外部候选：HMA16/25/36/49、DEMA20、TEMA20、ZLEMA20、Ehlers瞬时趋势线20；全部接项目既有三层确认、风险线和双口径评价。",
        "- 外部资料只用于候选发现：HMA 用低滞后换过冲和噪声；DEMA/TEMA/ZLEMA 同样存在噪声与过冲；Ehlers iTrend 为固定周期低滞后平滑。所有结论以本地数据为准。",
        "- **最终硬门槛**：训练自身闭环≥20判定、≥2年、正确率≥70%且Wilson下界≥50%、平均退出收益>0；验证自身闭环≥5判定、正确率≥60%、平均退出收益>0；训练统一波段完整样本≥20、验证≥3；两期统一实际收益均>0、平均绝对延迟≤20个交易日、信号时已走幅度≤10%。",
        "- 验证段此前已查看，因此即使通过也只是候选，不代表可直接上线。未计交易成本。", "",
        f"## 结论：{payload['final_verdict']}", "",
        payload["final_reason"], "",
    ]
    for code in payload["selections"]:
        lines.extend([f"## {pc.INDEXES[code]}（{code}）", ""])
        for kind, label in (("bottom", "底部"), ("top", "顶部")):
            options = payload["selections"][code][kind]
            lines.extend([
                f"### {label}训练最接近候选（最多5个）", "",
                "| 算法 | 三层 | 硬门槛 | 训练闭环正确率 | 训练闭环收益 | 训练统一实际/理论 | 绝对延迟 | 已走幅度 | 验证闭环正确率 | 验证统一实际收益 | 验证绝对延迟 | 验证已走幅度 |",
                "|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ])
            if not options:
                lines.append("| 无达到训练统一波段样本门槛的配置 | — | 否 | — | — | — | — | — | — | — | — | — |")
            for row in options:
                lines.append(
                    f"| `{row['algorithm']}` | {confirm_text(row)} | {'是' if row['final_gate_passed'] else '否'} | "
                    f"{accuracy(row, 'train')} | {pct(row['train_own_avg_closed_exit_return'])} | "
                    f"{pct(row['train_oracle_avg_exit_return'])}/{pct(row['train_oracle_avg_ideal_return'])} | "
                    f"{num(row['train_oracle_avg_abs_delay_days'])} | {pct(row['train_oracle_avg_lag_price_move'])} | "
                    f"{accuracy(row, 'validation')} | {pct(row['validation_oracle_avg_exit_return'])} | "
                    f"{num(row['validation_oracle_avg_abs_delay_days'])} | {pct(row['validation_oracle_avg_lag_price_move'])} |"
                )
            lines.append("")
    stem = payload["output_stem"]
    lines.extend([
        "## 完整数据", "",
        f"- `{stem}-grid.csv`：外部候选、MACD参照、生产bigwave与回滚swing6的完整双口径数据。",
        f"- `{stem}-events.csv`：逐信号明细。",
        f"- `{stem}-results.json`：外部来源、硬门槛、通过行与协议哈希。",
        "- 本轮没有修改生产算法、生产JSON或前端。", "",
    ])
    return "\n".join(lines)


def main(args) -> None:
    input_hash_before = research_input_hash()
    codes = args.codes.split(",") if args.codes else list(DEFAULT_CODES)
    specs = external_specs() + [REFERENCE_SPEC]
    breadth = load_breadth()
    rows = []
    event_rows = []
    selections = {}
    data_end = None
    oracle_metadata = {}
    for code in codes:
        frame = pc.read_index_csv(code)
        data_end = max(data_end or frame["date"].max(), frame["date"].max())
        state = prepare(frame, breadth, {"confirm_window": 3})
        bounds = segment_bounds(frame)
        waves, metadata = build_oracle_waves(frame, code)
        oracle_metadata[code] = metadata
        print(f"[{code} {pc.INDEXES[code]}] 筛选外部低滞后候选", flush=True)
        configurations = build_screen_configurations(frame, state, specs, code)
        bigwave_events = bottom_details(frame, breadth, code=code)
        swing_events = swing_details(frame, breadth)
        configurations.extend([
            {
                "algorithm": "生产bigwave", "family": "生产bigwave", "params": {}, "confirm": {},
                "source": "production", "events": {"bottom": bigwave_events, "top": []},
            },
            {
                "algorithm": "回滚swing6", "family": "回滚swing6", "params": {}, "confirm": {},
                "source": "rollback",
                "events": {kind: [event for event in swing_events if event["type"] == kind] for kind in KINDS},
            },
        ])
        code_rows = []
        for index, config in enumerate(configurations, 1):
            own = evaluate_closed_loop(frame, config["events"], RISK_LINES[code], details=True)
            oracle = evaluate_oracle(frame, config["events"], waves, bounds)
            flattened = flatten_summary(code, config, own, oracle)
            for row in flattened:
                row["final_gate_passed"] = passes_final_gate(row)
            rows.extend(flattened)
            code_rows.extend(flattened)
            event_rows.extend(flatten_events(code, config, own, oracle))
            if index % 20 == 0 or index == len(configurations):
                print(f"  已评价 {index}/{len(configurations)} 个配置", flush=True)
        selections[code] = {}
        for kind in KINDS:
            eligible = [
                row for row in code_rows
                if row["kind"] == kind and row["train_oracle_closed"] >= 20
                and row["train_oracle_active_years"] >= 2
            ]
            selections[code][kind] = sorted(eligible, key=screen_key, reverse=True)[:5]
    input_hash_after = research_input_hash()
    if input_hash_before != input_hash_after:
        raise RuntimeError("筛选期间原始指数行情或行业宽度发生变化，已停止输出")
    passed = [row for row in rows if row["final_gate_passed"]]
    payload = {
        "input_hash": input_hash_before,
        "data_end": data_end.strftime("%Y-%m-%d"),
        "codes": codes,
        "spec_count": len(specs),
        "external_sources": [
            "https://indicator.trading/en/indicator/hull-ma",
            "https://finwiz.io/technical-indicators/hull-moving-average",
            "https://arrowalgo.com/indicator-lag",
            "http://rtta.dev/documentation/algorithms/ehlers-instantaneous-trendline.zh-CN",
        ],
        "gate": {
            "train_own": "resolved>=20, active_years>=2, rate>=0.70, wilson_low>=0.50, avg_closed_exit_return>0",
            "validation_own": "resolved>=5, rate>=0.60, avg_closed_exit_return>0",
            "train_oracle": "closed>=20, avg_exit_return>0, avg_abs_delay_days<=20, avg_lag_price_move<=0.10",
            "validation_oracle": "closed>=3, avg_exit_return>0, avg_abs_delay_days<=20, avg_lag_price_move<=0.10",
        },
        "final_verdict": "找到候选" if passed else "未找到可继续候选",
        "final_reason": (
            f"共有 {len(passed)} 个方向配置同时通过收益、延迟与正确率硬门槛。"
            if passed else
            "没有任何方向配置同时通过训练/验证收益、统一波段延迟和自身闭环正确率硬门槛；按用户要求停止顶底信号方向继续研究。"
        ),
        "oracle": oracle_metadata,
        "selections": selections,
        "passed_rows": passed,
        "output_stem": args.output_stem,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT / f"{args.output_stem}-grid.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(event_rows).to_csv(OUTPUT / f"{args.output_stem}-events.csv", index=False, encoding="utf-8-sig")
    (OUTPUT / f"{args.output_stem}-results.json").write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
    )
    (OUTPUT / f"{args.output_stem}-report.md").write_text(build_report(payload, rows), encoding="utf-8")
    print(f"输出完成：{OUTPUT / args.output_stem}-*；汇总 {len(rows)} 行，通过硬门槛 {len(passed)} 行", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--codes", default="")
    parser.add_argument("--output-stem", default="external-low-lag-wave-screen")
    main(parser.parse_args())
