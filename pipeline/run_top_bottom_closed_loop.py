"""全量实际顶底组合的闭环离线研究；不写生产信号与原始行情。"""
from __future__ import annotations

import argparse
import heapq
import json
import sys
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipeline") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipeline"))

import precision_config as pc
from build_dashboard import load_breadth
from research_algorithms import wilson_interval
from run_top_bottom_optimization import (
    CONFIRM_GRID, COOLDOWN, MIN_TRAIN_RESOLVED, OUTPUT, UNTESTED,
    events_for_row, research_input_hash, segment_bounds,
)
from swing_signal_engine import prepare, swing_details
from top_bottom_candidate_algorithms import KINDS, candidate_points, candidate_specs, confirm_candidates, spec_id
from top_bottom_closed_loop import (
    RISK_LINES, TRAIN_ANNUALIZED_VOLATILITY, evaluate_closed_loop,
    outcome, prepare_direction,
)

DEFAULT_CODES = ("sh000001", "sz399006", "sh000688")


def quick_summary(prepared: list[dict], opposite_positions: list[int], start: int, end: int) -> dict:
    successes = failures = pending = total = 0
    years = set()
    for event in prepared:
        position = event["_position"]
        if not start <= position <= end:
            continue
        total += 1
        years.add(event["date"][:4])
        status, _, _ = outcome(position, event["_risk_position"], opposite_positions, end)
        successes += status == "closed_success"
        failures += status == "risk_failure"
        pending += status == "pending"
    resolved = successes + failures
    low, high = wilson_interval(successes, resolved)
    return {
        "total": total, "resolved": resolved, "successes": successes,
        "closed_successes": successes, "failures": failures,
        "risk_failures": failures, "pending": pending,
        "rate": successes / resolved if resolved else None,
        "wilson95": [low, high], "wilson_low": low, "wilson_high": high,
        "small_sample": resolved < 30, "active_years": len(years),
    }


def pair_score(bottom: dict, top: dict) -> tuple | None:
    if min(bottom["resolved"], top["resolved"]) < MIN_TRAIN_RESOLVED:
        return None
    if min(bottom["active_years"], top["active_years"]) < 2:
        return None
    return (
        min(bottom["wilson_low"], top["wilson_low"]),
        (bottom["rate"] + top["rate"]) / 2,
        min(bottom["resolved"], top["resolved"]),
        bottom["resolved"] + top["resolved"],
    )


def observation_score(bottom: dict, top: dict) -> tuple | None:
    if min(bottom["resolved"], top["resolved"]) == 0:
        return None
    return (
        min(bottom["wilson_low"], top["wilson_low"]),
        (bottom["rate"] + top["rate"]) / 2,
        min(bottom["resolved"], top["resolved"]),
        bottom["resolved"] + top["resolved"],
    )


def push_ranking(heap: list, item: tuple, limit: int) -> None:
    if len(heap) < limit:
        heapq.heappush(heap, item)
    elif item[0] > heap[0][0]:
        heapq.heapreplace(heap, item)


def rank_pairs(frame: pd.DataFrame, configurations: list[dict], start: int, end: int,
               limit: int = 5) -> dict:
    formal = []
    observation = []
    evaluated = eligible = 0
    for bottom_index, bottom in enumerate(configurations):
        bottom_prepared = bottom["prepared"]["bottom"]
        for top_index, top in enumerate(configurations):
            top_prepared = top["prepared"]["top"]
            bottom_summary = quick_summary(
                bottom_prepared, top["positions"]["top"], start, end,
            )
            top_summary = quick_summary(
                top_prepared, bottom["positions"]["bottom"], start, end,
            )
            evaluated += 1
            score = pair_score(bottom_summary, top_summary)
            if score is not None:
                eligible += 1
                item = (*score, -bottom_index, -top_index)
                push_ranking(formal, (item, bottom_index, top_index), limit)
            watch_score = observation_score(bottom_summary, top_summary)
            if watch_score is not None:
                item = (*watch_score, -bottom_index, -top_index)
                push_ranking(observation, (item, bottom_index, top_index), 3)
    return {
        "evaluated": evaluated,
        "eligible": eligible,
        "formal": sorted(formal, reverse=True),
        "observation": sorted(observation, reverse=True),
    }


def build_configurations(frame: pd.DataFrame, state: dict, specs: list[dict], code: str) -> list[dict]:
    configurations = []
    risk_line = RISK_LINES[code]
    for index, spec in enumerate(specs, 1):
        points = candidate_points(frame, spec)
        algorithm = spec_id(spec)
        for confirm in CONFIRM_GRID:
            events = {
                kind: confirm_candidates(
                    frame, state, points[kind], kind, confirm, COOLDOWN,
                )
                for kind in KINDS
            }
            prepared = {
                kind: prepare_direction(frame, events[kind], kind, risk_line)
                for kind in KINDS
            }
            configurations.append({
                "algorithm": algorithm,
                "family": spec["family"],
                "params": spec["params"],
                "confirm": confirm,
                "events": events,
                "prepared": prepared,
                "positions": {
                    kind: [event["_position"] for event in prepared[kind]]
                    for kind in KINDS
                },
            })
        if index % 10 == 0 or index == len(specs):
            print(f"  已生成 {index}/{len(specs)} 个参数算法", flush=True)
    return configurations


def config_key(config: dict) -> tuple:
    return config["algorithm"], json.dumps(config["confirm"], sort_keys=True)


def pair_payload(frame: pd.DataFrame, bottom: dict, top: dict, score: tuple,
                 risk_line: float, include_events: bool = False) -> dict:
    evaluation = evaluate_closed_loop(
        frame, {"bottom": bottom["events"]["bottom"], "top": top["events"]["top"]},
        risk_line, details=True,
    )
    payload = {
        "bottom_algorithm": bottom["algorithm"],
        "bottom_confirm": json.dumps(bottom["confirm"], sort_keys=True),
        "top_algorithm": top["algorithm"],
        "top_confirm": json.dumps(top["confirm"], sort_keys=True),
        "train_score": list(score[:4]),
        "summary": evaluation["summary"],
    }
    if include_events:
        payload["events"] = evaluation["events"]
    return payload


def flatten_pair(code: str, source: str, rank: int | None, pair: dict) -> list[dict]:
    rows = []
    for kind in KINDS:
        row = {
            "code": code, "name": pc.INDEXES[code], "source": source,
            "rank": rank, "kind": kind,
            "bottom_algorithm": pair["bottom_algorithm"],
            "bottom_confirm": pair["bottom_confirm"],
            "top_algorithm": pair["top_algorithm"],
            "top_confirm": pair["top_confirm"],
            "risk_line": RISK_LINES[code],
        }
        for segment in ("train", "validation"):
            for key, value in pair["summary"][kind][segment].items():
                if key != "wilson95":
                    row[f"{segment}_{key}"] = value
        rows.append(row)
    return rows


def named_pair(frame: pd.DataFrame, code: str, bottom_events: list[dict],
               top_events: list[dict], bottom_algorithm: str, top_algorithm: str,
               bottom_confirm: dict, top_confirm: dict) -> dict:
    evaluation = evaluate_closed_loop(
        frame, {"bottom": bottom_events, "top": top_events}, RISK_LINES[code],
    )
    return {
        "bottom_algorithm": bottom_algorithm,
        "bottom_confirm": json.dumps(bottom_confirm, sort_keys=True),
        "top_algorithm": top_algorithm,
        "top_confirm": json.dumps(top_confirm, sort_keys=True),
        "summary": evaluation["summary"],
    }


def accuracy_text(summary: dict) -> str:
    if summary["rate"] is None:
        return f"—（0已判定/{summary['total']}信号）"
    low, high = summary["wilson95"]
    return (
        f"{summary['rate'] * 100:.1f}%（{summary['successes']}/{summary['resolved']}已判定，"
        f"{summary['total']}信号）[{low * 100:.1f}%–{high * 100:.1f}%]"
    )


def percent(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def number(value: float | None) -> str:
    return "—" if value is None else f"{value:.1f}"


def build_report(payload: dict) -> str:
    lines = [
        "# 指数顶底实际组合闭环回测（第二轮）", "",
        f"- 数据截止：{payload['data_end']}；输入哈希：`{payload['input_hash']}`。",
        f"- 参数算法：{payload['spec_count']}；每指数配置：{payload['configuration_count_per_index']}；实际顶底组合：{payload['pair_evaluation_count']:,} 个。",
        f"- 训练段：{payload['protocol']['train']}；隔离期：{payload['protocol']['embargo']}；验证段：{payload['protocol']['validation']}。",
        "- 闭环成功 = 未先突破风险线且等到下一个实际反向信号；风险先触发 = 失败；段尾未退出且未失败 = pending，不进分母。无固定持有期限、无固定收益目标。",
        "- 风险线严格按‘超过’触发，恰好等于不失败；反向信号与风险同日出现时保守判失败。信号价为三层确认日收盘，从下一交易日开始检查。",
        "- 闭环成功不必然盈利：盈利退出率、亏损闭环、退出收益、MAE/MFE和持有交易日必须并列阅读。风险失败日收益为当日收盘诊断，不假设能按风险线精确成交；未计交易成本。",
        f"- 正式组合要求训练顶底各至少{MIN_TRAIN_RESOLVED}条已判定且各覆盖2年；仅按训练 min(Wilson下界)、平均成功率、较小样本量排序。验证段不参与排名。", "",
    ]
    previous_hash = payload.get("previous_input_hash")
    if previous_hash and previous_hash != payload["input_hash"]:
        lines.extend([
            f"- 第一轮输入哈希为 `{previous_hash}`，与本轮不同；第一轮旧数字不能直接拼接比较，本报告中的旧口径首选组合已在当前输入上重新计算。", "",
        ])
    lines.extend([
        "> 验证段已在第一轮查看过，本轮仍不参与排名，但已不能视为未来从未触碰的独立检验；后续若据本轮继续调参，必须保留新的未来数据验证。", "",
        "## 冻结风险线", "",
        "| 指数 | 训练年化波动 | 最大可接受不利波动 |", "|---|---:|---:|",
    ])
    for code, name in pc.INDEXES.items():
        if code in payload["selections"]:
            lines.append(f"| {name} | {TRAIN_ANNUALIZED_VOLATILITY[code] * 100:.4f}% | {RISK_LINES[code] * 100:.1f}% |")
    lines.extend(["", "风险线由训练段波动换算，双创8%为用户指定；其余指数按双创平均波动比例换算并取整到0.5个百分点，未使用验证段。", ""])
    for code, selection in payload["selections"].items():
        name = pc.INDEXES[code]
        choice = selection["pair_choice"]
        lines.extend([f"## {name}（{code}）", ""])
        lines.append(f"训练共评价 {choice['evaluated']:,} 个实际组合；{choice['eligible']:,} 个达到正式样本门槛。")
        lines.append("")
        options = choice["options"]
        source = "正式组合"
        if not options:
            options = choice["observation_options"]
            source = "小样本观察（非正式候选）"
            lines.extend(["**无达到训练样本门槛的正式组合。以下仅披露训练排名靠前的小样本观察，不建议据此上线。**", ""])
        if options:
            lines.extend([
                f"**{source}**", "",
                "| 排名 | 底部算法/三层参数 | 顶部算法/三层参数 | 底部训练→验证闭环准确率[95%区间] | 顶部训练→验证闭环准确率[95%区间] |",
                "|---|---|---|---:|---:|",
            ])
            for rank, pair in enumerate(options, 1):
                bottom = f"`{pair['bottom_algorithm']}`<br>`{pair['bottom_confirm']}`"
                top = f"`{pair['top_algorithm']}`<br>`{pair['top_confirm']}`"
                bottom_accuracy = f"{accuracy_text(pair['summary']['bottom']['train'])} → {accuracy_text(pair['summary']['bottom']['validation'])}"
                top_accuracy = f"{accuracy_text(pair['summary']['top']['train'])} → {accuracy_text(pair['summary']['top']['validation'])}"
                lines.append(f"| {rank} | {bottom} | {top} | {bottom_accuracy} | {top_accuracy} |")
            lines.append("")
        else:
            lines.extend(["没有任何可判定闭环样本。", ""])
        chosen = choice["chosen"]
        diagnostic = options[0] if options else None
        if diagnostic:
            heading = "首选组合路径诊断" if chosen else "排名第1的小样本观察路径诊断"
            lines.extend([f"### {heading}", "", "| 方向 | 区间 | 盈利退出率 | 亏损闭环 | 平均退出收益 | 平均/最深MAE | 平均MFE | 平均/最长持有交易日 |", "|---|---|---:|---:|---:|---:|---:|---:|"])
            for kind, label in (("bottom", "底部"), ("top", "顶部")):
                for segment, segment_label in (("train", "训练"), ("validation", "验证")):
                    summary = diagnostic["summary"][kind][segment]
                    lines.append(
                        f"| {label} | {segment_label} | {percent(summary['profitable_exit_rate'])} | "
                        f"{summary['loss_making_closed_exits']} | {percent(summary['avg_closed_exit_return'])} | "
                        f"{percent(summary['avg_mae'])}/{percent(summary['max_mae'])} | {percent(summary['avg_mfe'])} | "
                        f"{number(summary['avg_holding_days'])}/{number(summary['max_holding_days'])} |"
                    )
            lines.append("")
        for comparison in selection["comparisons"]:
            pair = comparison["pair"]
            lines.append(
                f"- 对照 {comparison['label']}：底部 `{pair['bottom_algorithm']}` + 顶部 `{pair['top_algorithm']}`；"
                f"底部训练 {accuracy_text(pair['summary']['bottom']['train'])}，验证 {accuracy_text(pair['summary']['bottom']['validation'])}；"
                f"顶部训练 {accuracy_text(pair['summary']['top']['train'])}，验证 {accuracy_text(pair['summary']['top']['validation'])}。"
            )
        lines.extend(["- 对照 生产bigwave：只输出底部、生产顶部关闭，没有实际反向顶部可构成闭环，**不计算也不伪造生产闭环胜率**。", ""])
    lines.extend(["## 本轮未测试的数据型方法", ""])
    for item in UNTESTED:
        reason = item["reason"].replace("本轮七指数", "本轮三指数")
        lines.append(f"- **{item['family']}**：{reason}")
    stem = payload["output_stem"]
    lines.extend(["", f"组合汇总见 `{stem}-grid.csv`；首选逐日信号、路径指标与完整三层日志见 `{stem}-results.json`。", ""])
    return "\n".join(lines)


def main(args) -> None:
    input_hash_before = research_input_hash()
    specs = candidate_specs()
    if args.limit_specs:
        specs = specs[:args.limit_specs]
    codes = args.codes.split(",") if args.codes else list(DEFAULT_CODES)
    breadth = load_breadth()
    first_round = {}
    previous_input_hash = None
    first_round_path = OUTPUT / "top-bottom-optimization-results.json"
    if first_round_path.exists():
        first_round_payload = json.loads(first_round_path.read_text(encoding="utf-8"))
        first_round = first_round_payload.get("selections", {})
        previous_input_hash = first_round_payload.get("input_hash")
    selections = {}
    grid_rows = []
    data_end = None
    total_pairs = 0
    for code in codes:
        name = pc.INDEXES[code]
        frame = pc.read_index_csv(code)
        data_end = max(data_end or frame["date"].max(), frame["date"].max())
        state = prepare(frame, breadth, {"confirm_window": 3})
        print(f"[{code} {name}] 生成 {len(specs)} 个参数算法 × {len(CONFIRM_GRID)} 组三层参数", flush=True)
        configurations = build_configurations(frame, state, specs, code)
        by_key = {config_key(config): config for config in configurations}
        train_start, train_end = segment_bounds(frame)["train"]
        print(f"  枚举 {len(configurations) ** 2:,} 个实际顶底组合", flush=True)
        ranking = rank_pairs(frame, configurations, train_start, train_end, args.pair_limit)
        total_pairs += ranking["evaluated"]

        def materialize(items, include_first_events=False):
            output = []
            for index, (_, bottom_index, top_index) in enumerate(items):
                score = items[index][0]
                output.append(pair_payload(
                    frame, configurations[bottom_index], configurations[top_index], score,
                    RISK_LINES[code], include_events=include_first_events and index == 0,
                ))
            return output

        options = materialize(ranking["formal"], include_first_events=True)
        observations = materialize(ranking["observation"])
        chosen = options[0] if options else None
        if chosen:
            audits = {}
            for kind, prefix in (("bottom", "bottom"), ("top", "top")):
                row = {
                    "algorithm": chosen[f"{prefix}_algorithm"],
                    "confirm": chosen[f"{prefix}_confirm"],
                }
                _, audit = events_for_row(frame, state, specs, row, with_audit=True)
                audits[kind] = audit[kind]
            chosen["candidate_audit"] = audits
        comparisons = []
        swing = swing_details(frame, breadth)
        swing_pair = named_pair(
            frame, code,
            [event for event in swing if event["type"] == "bottom"],
            [event for event in swing if event["type"] == "top"],
            "回滚swing6", "回滚swing6", {}, {},
        )
        comparisons.append({"label": "回滚swing6实际顶底", "pair": swing_pair})
        grid_rows.extend(flatten_pair(code, "回滚swing6", None, swing_pair))
        old_choice = first_round.get(code, {}).get("pair_choice", {}).get("chosen")
        if old_choice:
            bottom_key = old_choice["bottom_algorithm"], old_choice["bottom_confirm"]
            top_key = old_choice["top_algorithm"], old_choice["top_confirm"]
            if bottom_key in by_key and top_key in by_key:
                bottom = by_key[bottom_key]
                top = by_key[top_key]
                old_pair = named_pair(
                    frame, code, bottom["events"]["bottom"], top["events"]["top"],
                    bottom["algorithm"], top["algorithm"], bottom["confirm"], top["confirm"],
                )
                comparisons.append({"label": "第一轮旧口径首选组合", "pair": old_pair})
                grid_rows.extend(flatten_pair(code, "第一轮旧口径首选", None, old_pair))
        for rank, pair in enumerate(options, 1):
            grid_rows.extend(flatten_pair(code, "闭环正式候选", rank, pair))
        if not options:
            for rank, pair in enumerate(observations, 1):
                grid_rows.extend(flatten_pair(code, "闭环小样本观察", rank, pair))
        selections[code] = {
            "pair_choice": {
                "evaluated": ranking["evaluated"], "eligible": ranking["eligible"],
                "chosen": chosen, "options": options,
                "observation_options": [] if options else observations,
            },
            "comparisons": comparisons,
        }
        print(f"  完成：正式组合 {ranking['eligible']:,} 个", flush=True)
    input_hash_after = research_input_hash()
    if input_hash_before != input_hash_after:
        raise RuntimeError("回测期间原始指数行情或行业宽度发生变化，已停止输出")
    first_frame = pc.read_index_csv(codes[0])
    train_def = pc.segment_definition(first_frame)
    payload = {
        "input_hash": input_hash_before,
        "previous_input_hash": previous_input_hash,
        "data_end": data_end.strftime("%Y-%m-%d"),
        "spec_count": len(specs),
        "configuration_count_per_index": len(specs) * len(CONFIRM_GRID),
        "pair_evaluation_count": total_pairs,
        "cooldown": COOLDOWN,
        "confirm_grid": list(CONFIRM_GRID),
        "min_train_resolved": MIN_TRAIN_RESOLVED,
        "risk_lines": RISK_LINES,
        "risk_method": "active indices fixed at user 8%; others = 8% * train annualized volatility / mean(active train annualized volatility), rounded to nearest 0.5 percentage point",
        "train_annualized_volatility": TRAIN_ANNUALIZED_VOLATILITY,
        "selection_rule": "train only: min(bottom/top Wilson95 lower), mean closed-loop rate, min resolved, total resolved; each direction resolved>=20 and active_years>=2",
        "evaluation_rule": "next actual opposite confirmed signal exit vs strict adverse-risk breach; no fixed horizon or profit target; same-day ambiguity fails conservatively",
        "protocol": {
            "train": f"{pc.TRAIN_START.date()}~{train_def['train']['effective_end']}",
            "embargo": f"{train_def['embargo']['start']}~{train_def['embargo']['end']}",
            "validation": f"{pc.VALID_START.date()}~{data_end.date()}",
        },
        "untested": UNTESTED,
        "output_stem": args.output_stem,
        "selections": selections,
    }
    from run_top_bottom_optimization import json_safe
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(grid_rows).to_csv(OUTPUT / f"{args.output_stem}-grid.csv", index=False, encoding="utf-8-sig")
    (OUTPUT / f"{args.output_stem}-results.json").write_text(
        json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8",
    )
    (OUTPUT / f"{args.output_stem}-report.md").write_text(build_report(payload), encoding="utf-8")
    print(f"输出完成：{OUTPUT / args.output_stem}-*；共评价 {total_pairs:,} 个实际组合", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-specs", type=int, default=0)
    parser.add_argument("--codes", default="")
    parser.add_argument("--pair-limit", type=int, default=5)
    parser.add_argument("--output-stem", default="top-bottom-closed-loop-round2")
    main(parser.parse_args())
