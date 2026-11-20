"""逐算法输出统一事后主波段与自身反向信号闭环收益；不写生产产物。"""
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
from run_top_bottom_closed_loop import build_configurations
from run_top_bottom_optimization import (
    OUTPUT, json_safe, research_input_hash, segment_bounds,
)
from swing_signal_engine import prepare, swing_details
from top_bottom_candidate_algorithms import KINDS, candidate_specs
from top_bottom_closed_loop import RISK_LINES, evaluate_closed_loop
from wave_return_diagnostics import build_oracle_waves, evaluate_oracle

DEFAULT_CODES = ("sz399006", "sh000688")


def flatten_summary(code: str, config: dict, own: dict, oracle: dict) -> list[dict]:
    rows = []
    for kind in KINDS:
        row = {
            "code": code,
            "name": pc.INDEXES[code],
            "source": config["source"],
            "kind": kind,
            "algorithm": config["algorithm"],
            "family": config["family"],
            "params": json.dumps(config["params"], ensure_ascii=False, sort_keys=True),
            "confirm": json.dumps(config["confirm"], sort_keys=True),
            "risk_line": RISK_LINES[code],
        }
        for segment in ("train", "validation"):
            own_summary = own["summary"][kind][segment]
            for key, value in own_summary.items():
                if key != "wilson95":
                    row[f"{segment}_own_{key}"] = value
            oracle_summary = oracle["summary"][kind][segment]
            for key, value in oracle_summary.items():
                row[f"{segment}_oracle_{key}"] = value
        rows.append(row)
    return rows


def flatten_events(code: str, config: dict, own: dict, oracle: dict) -> list[dict]:
    rows = []
    own_fields = (
        "status", "outcome_date", "holding_days", "ambiguous_same_day", "exit_return",
        "mark_to_market_return", "mae", "mfe", "outcome_return",
    )
    oracle_fields = (
        "oracle_status", "oracle_wave_start_date", "oracle_wave_end_date",
        "oracle_delay_days", "oracle_lag_price_move", "oracle_holding_days",
        "oracle_exit_return", "oracle_mae", "oracle_mfe", "oracle_capture_ratio",
        "ideal_return", "ideal_extreme_date", "ideal_extreme_price",
    )
    for kind in KINDS:
        for segment in ("train", "validation"):
            own_by_date = {
                event["date"]: event for event in own["events"][kind][segment]
            }
            for event in oracle["events"][kind][segment]:
                own_event = own_by_date.get(event["date"], {})
                row = {
                    "code": code,
                    "name": pc.INDEXES[code],
                    "source": config["source"],
                    "kind": kind,
                    "algorithm": config["algorithm"],
                    "confirm": json.dumps(config["confirm"], sort_keys=True),
                    "segment": segment,
                    "signal_date": event["date"],
                    "candidate_date": event.get("candidate_date"),
                    "signal_price": event.get("price"),
                }
                row.update({f"own_{key}": own_event.get(key) for key in own_fields})
                row.update({key: event.get(key) for key in oracle_fields})
                rows.append(row)
    return rows


def formal_oracle_rows(rows: list[dict], code: str, kind: str) -> list[dict]:
    eligible = [
        row for row in rows
        if row["code"] == code and row["kind"] == kind
        and row["train_oracle_closed"] >= 20 and row["train_oracle_active_years"] >= 2
    ]
    return sorted(
        eligible,
        key=lambda row: (
            row["train_oracle_avg_exit_return"],
            row["train_oracle_avg_capture_ratio"] if row["train_oracle_avg_capture_ratio"] is not None else -999,
            -abs(row["train_oracle_avg_delay_days"]),
            row["train_oracle_closed"],
        ),
        reverse=True,
    )


def pct(value) -> str:
    return "—" if value is None else f"{value * 100:.2f}%"


def num(value) -> str:
    return "—" if value is None else f"{value:.2f}"


def confirm_text(row: dict) -> str:
    confirm = json.loads(row["confirm"])
    if not confirm:
        return "—"
    return f"v{confirm['volume_ratio']:g}/b{confirm['breadth_improve']:g}"


def build_report(payload: dict, rows: list[dict]) -> str:
    lines = [
        "# 科创50与创业板指：逐算法波段收益、理论极值与延迟比较", "",
        f"- 数据截止：{payload['data_end']}；输入哈希：`{payload['input_hash']}`。",
        f"- 每指数 {payload['spec_count']} 个参数算法 × 4 组三层确认 = {payload['configuration_count_per_index']} 个配置；另列生产 bigwave 与回滚 swing6。",
        f"- 训练段：{payload['protocol']['train']}；隔离期：{payload['protocol']['embargo']}；验证段：{payload['protocol']['validation']}。",
        "- **统一事后主波段**：用各指数已冻结的生产 ZigZag 幅度和 min_gap 的极值参考日作共同标尺；这是马后炮评价，不是实时信号，也不代表用户逐日人工标注。",
        "- 统一标尺的实际收益 = 信号确认日收盘到该主波段反向极值日收盘的方向收益；理论最大收益 = 底部极值日最低价到随后波段最高价，顶部反向同理。",
        "- 延迟 = 信号日位置 − 主波段极值日位置，正数表示滞后、负数表示提前；错过幅度为信号价相对极值日内价已经走过的方向幅度。表内为带符号平均值，CSV另列提前占比、绝对延迟及提前/滞后分组延迟。理论收益与延迟只用于离线评价。",
        "- **自身闭环**：底部到同一算法下一顶部信号收盘退出，顶部反之；风险线先触发为失败，pending 不进闭环正确率分母。闭环成功不等于盈利。",
        f"- 下表仅按训练段统一标尺平均实际收益排序，要求训练完整波段至少20条且覆盖至少2年；验证只并列展示，不参与排名。完整{payload['configuration_count_per_index']}配置及小样本全部在CSV，不把Top 5当成生产选型。",
        "- 验证段此前已经查看，且用户截图已用于讨论波段粒度，不能再宣称为未触碰的独立验证。未计交易成本。", "",
    ]
    for code in payload["selections"]:
        lines.extend([f"## {pc.INDEXES[code]}（{code}）", ""])
        oracle = payload["oracle"][code]
        lines.append(
            f"统一标尺：ZigZag 幅度 {oracle['amplitude'] * 100:.2f}%，min_gap={oracle['min_gap']}，"
            f"共 {oracle['pivot_count']} 个极值参考点。"
        )
        lines.append("")
        for kind, label in (("bottom", "底部"), ("top", "顶部")):
            options = payload["selections"][code][kind]
            lines.extend([
                f"### {label}：训练统一标尺实际收益前5", "",
                "| 算法 | 三层 | 训练n | 自身闭环收益 | 统一波段实际收益 | 理论最大收益 | 延迟交易日 | 已错过幅度 | 验证n | 验证实际收益 | 验证延迟 |",
                "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            ])
            if not options:
                lines.append("| 无达到训练样本门槛的配置 | — | 0 | — | — | — | — | — | — | — | — |")
            for row in options:
                lines.append(
                    f"| `{row['algorithm']}` | {confirm_text(row)} | {row['train_oracle_closed']} | "
                    f"{pct(row['train_own_avg_closed_exit_return'])} | {pct(row['train_oracle_avg_exit_return'])} | "
                    f"{pct(row['train_oracle_avg_ideal_return'])} | {num(row['train_oracle_avg_delay_days'])} | "
                    f"{pct(row['train_oracle_avg_lag_price_move'])} | {row['validation_oracle_closed']} | "
                    f"{pct(row['validation_oracle_avg_exit_return'])} | {num(row['validation_oracle_avg_delay_days'])} |"
                )
            lines.append("")
        lines.extend([
            "### 生产与回滚基线（不套用上述样本门槛）", "",
            "| 算法 | 方向 | 训练n | 训练统一波段实际收益 | 理论最大 | 延迟 | 已错过 | 验证n | 验证实际收益 | 验证延迟 |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
        ])
        for row in payload["baselines"][code]:
            lines.append(
                f"| {row['algorithm']} | {'底部' if row['kind'] == 'bottom' else '顶部'} | "
                f"{row['train_oracle_closed']} | {pct(row['train_oracle_avg_exit_return'])} | "
                f"{pct(row['train_oracle_avg_ideal_return'])} | {num(row['train_oracle_avg_delay_days'])} | "
                f"{pct(row['train_oracle_avg_lag_price_move'])} | {row['validation_oracle_closed']} | "
                f"{pct(row['validation_oracle_avg_exit_return'])} | {num(row['validation_oracle_avg_delay_days'])} |"
            )
        lines.extend(["", "生产 bigwave 顶部关闭，因此自身闭环没有顶部退出；其底部统一标尺收益仍可计算，两种口径不可混用。", ""])
    stem = payload["output_stem"]
    lines.extend([
        "## 完整数据", "",
        f"- `{stem}-grid.csv`：每指数、每算法、每三层参数、每方向的训练/验证完整比较。",
        f"- `{stem}-events.csv`：每个信号的自身闭环与统一主波段明细。",
        f"- `{stem}-results.json`：协议、输入哈希、统一标尺参数和报告入选行。",
        "- 本轮仅新增评价，不修改任何候选生成、三层确认、生产信号、生产JSON或前端。", "",
    ])
    return "\n".join(lines)


def main(args) -> None:
    input_hash_before = research_input_hash()
    specs = candidate_specs()
    if args.limit_specs:
        specs = specs[:args.limit_specs]
    codes = args.codes.split(",") if args.codes else list(DEFAULT_CODES)
    breadth = load_breadth()
    rows = []
    event_rows = []
    selections = {}
    baselines = {}
    oracle_metadata = {}
    data_end = None
    for code in codes:
        frame = pc.read_index_csv(code)
        data_end = max(data_end or frame["date"].max(), frame["date"].max())
        state = prepare(frame, breadth, {"confirm_window": 3})
        bounds = segment_bounds(frame)
        waves, metadata = build_oracle_waves(frame, code)
        oracle_metadata[code] = metadata
        print(f"[{code} {pc.INDEXES[code]}] 生成并评价候选配置", flush=True)
        configurations = build_configurations(frame, state, specs, code)
        for config in configurations:
            config.update({"source": "candidate"})
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
            rows.extend(flattened)
            code_rows.extend(flattened)
            event_rows.extend(flatten_events(code, config, own, oracle))
            if index % 50 == 0 or index == len(configurations):
                print(f"  已评价 {index}/{len(configurations)} 个配置", flush=True)
        selections[code] = {
            kind: [dict(row) for row in formal_oracle_rows(code_rows, code, kind)[:5]]
            for kind in KINDS
        }
        baselines[code] = [row for row in code_rows if row["source"] != "candidate"]
    input_hash_after = research_input_hash()
    if input_hash_before != input_hash_after:
        raise RuntimeError("回测期间原始指数行情或行业宽度发生变化，已停止输出")
    first_frame = pc.read_index_csv(codes[0])
    train_def = pc.segment_definition(first_frame)
    payload = {
        "input_hash": input_hash_before,
        "data_end": data_end.strftime("%Y-%m-%d"),
        "spec_count": len(specs),
        "configuration_count_per_index": len(specs) * 4,
        "codes": codes,
        "protocol": {
            "train": f"{pc.TRAIN_START.date()}~{train_def['train']['effective_end']}",
            "embargo": f"{train_def['embargo']['start']}~{train_def['embargo']['end']}",
            "validation": f"{pc.VALID_START.date()}~{data_end.date()}",
        },
        "oracle_rule": "frozen production adaptive-close-ZigZag reference pivots; ex-post common ruler only; close-to-opposite-pivot actual return; intraday-extreme ideal return; signed trading-day delay",
        "own_rule": "same configuration next opposite confirmed signal exit; frozen risk line first; no fixed horizon or profit target; same-day risk ambiguity fails",
        "selection_rule": "train only: oracle closed>=20 and active_years>=2; avg oracle exit return, avg capture ratio, lower absolute delay, sample size; validation never ranks",
        "validation_warning": "validation and screenshot periods have been seen; not untouched independent validation",
        "oracle": oracle_metadata,
        "selections": selections,
        "baselines": baselines,
        "output_stem": args.output_stem,
    }
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT / f"{args.output_stem}-grid.csv", index=False, encoding="utf-8-sig")
    pd.DataFrame(event_rows).to_csv(OUTPUT / f"{args.output_stem}-events.csv", index=False, encoding="utf-8-sig")
    (OUTPUT / f"{args.output_stem}-results.json").write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
    )
    (OUTPUT / f"{args.output_stem}-report.md").write_text(build_report(payload, rows), encoding="utf-8")
    print(f"输出完成：{OUTPUT / args.output_stem}-*；汇总 {len(rows)} 行，事件 {len(event_rows)} 行", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-specs", type=int, default=0)
    parser.add_argument("--codes", default="")
    parser.add_argument("--output-stem", default="wave-return-comparison")
    main(parser.parse_args())
