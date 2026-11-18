"""逐指数逐方向离线网格研究；不修改生产信号与原始行情。"""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipeline") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipeline"))

import precision_config as pc
from bigwave_bottom_engine import bottom_details
from build_dashboard import load_breadth
from fetch_market import THS_SECTOR_RAW
from research_algorithms import ma_reversal_masks, wilson_interval
from swing_signal_engine import prepare, swing_details
from top_bottom_candidate_algorithms import (
    KINDS, candidate_points, candidate_specs, confirm_candidates, spec_id,
)

OUTPUT = ROOT / "data" / "research"
HORIZON = 60
COOLDOWN = 20
MIN_TRAIN_RESOLVED = 20
CONFIRM_GRID = (
    {"volume_ratio": 1.2, "breadth_improve": 0.05},
    {"volume_ratio": 1.0, "breadth_improve": 0.05},
    {"volume_ratio": 1.2, "breadth_improve": 0.03},
    {"volume_ratio": 0.8, "breadth_improve": 0.03},
)
UNTESTED = [
    {"family": "ERP/PE/PB/股息率估值", "reason": "缺少完整日频 point-in-time 指数估值与国债收益序列；已有ERP仅月频，禁止前向填充制造隐性未来信息。"},
    {"family": "成分股ADL/McClellan/TRIN/新高新低/BPI", "reason": "缺少历史时点成分股与退市股面板；现有90行业宽度不能冒充指数成分股宽度。"},
    {"family": "期权PCR/隐含波动率", "reason": "历史合约链与缺失率不满足本轮七指数统一回测；QVIX已按既有研究淘汰，不重复测试。"},
    {"family": "基金发行/公募88仓位/私募仓位", "reason": "公开资料没有完整日频历史时点口径；不构造伪精确信号。"},
    {"family": "全A换手率/破净率/居民资金", "reason": "自由流通股本、历史财报及宏观修订值不完整；破净率与融资数据已有增量检验记录，不重复测试。"},
]


def research_input_hash() -> str:
    digest = hashlib.sha256()
    digest.update(pc.input_hash().encode("ascii"))
    for path in sorted(THS_SECTOR_RAW.glob("*.csv")):
        digest.update(path.name.encode("utf-8"))
        digest.update(path.read_bytes())
    return digest.hexdigest()


def clean_number(value):
    if value is None:
        return None
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    if isinstance(value, (np.integer, int)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        return float(value) if np.isfinite(value) else None
    return value


def rate_text(summary: dict) -> str:
    if summary["rate"] is None:
        return f"— (0已判定/{summary['total']}信号)"
    return f"{summary['rate'] * 100:.1f}% ({summary['resolved']}已判定/{summary['total']}信号)"


def summary_accuracy_text(summary: dict) -> str:
    base = rate_text(summary)
    low, high = summary["wilson95"]
    if low is None or high is None:
        return base
    return f"{base} [{low * 100:.1f}%–{high * 100:.1f}%]"


def accuracy_text(row: dict, segment: str) -> str:
    return summary_accuracy_text({
        "rate": row[f"{segment}_rate"], "resolved": row[f"{segment}_resolved"],
        "total": row[f"{segment}_total"],
        "wilson95": [row[f"{segment}_wilson_low"], row[f"{segment}_wilson_high"]],
    })


def summarize(events: list[dict]) -> dict:
    successes = sum(event["status"] == "success" for event in events)
    failures = sum(event["status"] == "failure" for event in events)
    pending = sum(event["status"] == "pending" for event in events)
    resolved = successes + failures
    returns = [event["signed_return_20"] for event in events if event.get("signed_return_20") is not None]
    adverse = [event["mae_20"] for event in events if event.get("mae_20") is not None]
    interval = wilson_interval(successes, resolved)
    return {
        "total": len(events), "resolved": resolved, "successes": successes,
        "failures": failures, "pending": pending,
        "rate": successes / resolved if resolved else None,
        "wilson95": interval,
        "wilson_low": interval[0], "small_sample": resolved < 30,
        "active_years": len({event["date"][:4] for event in events}),
        "avg_signed_return_20": float(np.mean(returns)) if returns else None,
        "avg_mae_20": float(np.mean(adverse)) if adverse else None,
    }


def segment_bounds(frame: pd.DataFrame) -> dict:
    definition = pc.segment_definition(frame)
    dates = frame["date"]
    return {
        "train": (
            int(np.flatnonzero((dates >= pc.TRAIN_START).to_numpy())[0]),
            int(np.flatnonzero((dates <= pd.Timestamp(definition["train"]["effective_end"])).to_numpy())[-1]),
        ),
        "validation": (
            int(np.flatnonzero((dates >= pc.VALID_START).to_numpy())[0]),
            len(frame) - 1,
        ),
    }


def baseline_enricher(frame: pd.DataFrame):
    bull, bear = ma_reversal_masks(frame)
    bounds = segment_bounds(frame)
    cache = {}

    def enrich(row: dict) -> dict:
        for segment, (start, end) in bounds.items():
            resolved = row[f"{segment}_resolved"]
            key = (row["kind"], segment, resolved)
            if key not in cache:
                mask = bull if row["kind"] == "bottom" else bear
                available = []
                for position in range(start, end):
                    search_end = min(position + HORIZON, end)
                    early_success = bool(mask.iloc[position + 1:search_end + 1].any())
                    if early_success or position + HORIZON <= end:
                        available.append(position)
                sample_size = min(resolved, len(available))
                successes = 0
                if sample_size:
                    seed = pc.BASELINE_SEED + sum(ord(char) for char in f"{row['code']}{row['kind']}{segment}{resolved}")
                    generator = np.random.default_rng(seed)
                    sampled = generator.choice(available, size=sample_size, replace=False)
                    for position in sampled:
                        search_end = min(int(position) + HORIZON, end)
                        successes += bool(mask.iloc[int(position) + 1:search_end + 1].any())
                cache[key] = (successes / sample_size if sample_size else None, sample_size)
            baseline, random_samples = cache[key]
            row[f"{segment}_random_baseline"] = clean_number(baseline)
            row[f"{segment}_random_samples"] = random_samples
            row[f"{segment}_excess"] = clean_number(row[f"{segment}_rate"] - baseline) if row[f"{segment}_rate"] is not None and baseline is not None else None
        return row

    return enrich


def diagnostics(frame: pd.DataFrame, position: int, kind: str, segment_end: int) -> dict:
    price = float(frame["close"].iat[position])
    target = position + 20
    end = min(target, segment_end)
    part = frame.iloc[position + 1:end + 1]
    output = {"signed_return_20": None, "mae_20": None, "mfe_20": None}
    if target <= segment_end:
        change = float(frame["close"].iat[target] / price - 1)
        output["signed_return_20"] = change if kind == "bottom" else -change
    if not part.empty:
        if kind == "bottom":
            output["mae_20"] = float(part["low"].min() / price - 1)
            output["mfe_20"] = float(part["high"].max() / price - 1)
        else:
            output["mae_20"] = float(-(part["high"].max() / price - 1))
            output["mfe_20"] = float(-(part["low"].min() / price - 1))
    return output


def evaluate_fixed(frame: pd.DataFrame, events: list[dict], kind: str) -> dict:
    bull, bear = ma_reversal_masks(frame)
    success_mask = bull if kind == "bottom" else bear
    lookup = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(frame["date"])}
    bounds = segment_bounds(frame)
    output = {"train": [], "validation": []}
    for event in events:
        if event["type"] != kind or event["date"] not in lookup:
            continue
        position = lookup[event["date"]]
        for segment, (start, end) in bounds.items():
            if not start <= position <= end:
                continue
            search_end = min(position + HORIZON, end)
            matched = np.flatnonzero(success_mask.iloc[position + 1:search_end + 1].to_numpy())
            if len(matched):
                status = "success"
                confirmation = position + 1 + int(matched[0])
            elif position + HORIZON <= end:
                status = "failure"
                confirmation = None
            else:
                status = "pending"
                confirmation = None
            enriched = {
                **event, "status": status, "segment": segment,
                "confirmation_date": frame["date"].iat[confirmation].strftime("%Y-%m-%d") if confirmation is not None else None,
                "confirmation_days": confirmation - position if confirmation is not None else None,
                **diagnostics(frame, position, kind, end),
            }
            output[segment].append(enriched)
            break
    return {"events": output, "summary": {name: summarize(rows) for name, rows in output.items()}}


def evaluate_legacy(frame: pd.DataFrame, events: list[dict] | dict[str, list[dict]]) -> dict:
    if isinstance(events, dict):
        events = [event for kind in KINDS for event in events[kind]]
    bull, bear = ma_reversal_masks(frame)
    lookup = {day.strftime("%Y-%m-%d"): i for i, day in enumerate(frame["date"])}
    positioned = {
        kind: sorted(lookup[event["date"]] for event in events if event["type"] == kind and event["date"] in lookup)
        for kind in KINDS
    }
    bounds = segment_bounds(frame)
    output = {kind: {"train": [], "validation": []} for kind in KINDS}
    for kind in KINDS:
        opposite = positioned["top" if kind == "bottom" else "bottom"]
        success_mask = bull if kind == "bottom" else bear
        for event in events:
            if event["type"] != kind or event["date"] not in lookup:
                continue
            position = lookup[event["date"]]
            later = [value for value in opposite if value > position]
            next_opposite = later[0] if later else None
            for segment, (start, end) in bounds.items():
                if not start <= position <= end:
                    continue
                stop = min(end, next_opposite - 1) if next_opposite is not None else end
                matched = np.flatnonzero(success_mask.iloc[position + 1:stop + 1].to_numpy()) if stop > position else []
                if len(matched):
                    status = "success"
                elif next_opposite is not None and next_opposite <= end:
                    status = "failure"
                else:
                    status = "pending"
                output[kind][segment].append({**event, "status": status, "segment": segment})
                break
    return {
        "events": output,
        "summary": {
            kind: {segment: summarize(rows) for segment, rows in segments.items()}
            for kind, segments in output.items()
        },
    }


def flatten_row(code: str, name: str, kind: str, algorithm: str, family: str,
                params: dict, confirm: dict, fixed: dict, legacy: dict) -> dict:
    row = {
        "code": code, "name": name, "kind": kind, "algorithm": algorithm,
        "family": family, "params": json.dumps(params, ensure_ascii=False, sort_keys=True),
        "confirm": json.dumps(confirm, sort_keys=True),
    }
    for segment in ("train", "validation"):
        summary = fixed["summary"][segment]
        for key, value in summary.items():
            if key == "wilson95":
                continue
            row[f"{segment}_{key}"] = clean_number(value)
        old = legacy["summary"][kind][segment]
        row[f"{segment}_wilson_high"] = clean_number(summary["wilson95"][1])
        row[f"legacy_{segment}_rate"] = clean_number(old["rate"])
        row[f"legacy_{segment}_resolved"] = old["resolved"]
        row[f"legacy_{segment}_total"] = old["total"]
    return row


def rank_key(row: dict) -> tuple:
    return (
        row["train_wilson_low"] if row["train_wilson_low"] is not None else -1,
        row["train_rate"] if row["train_rate"] is not None else -1,
        row["train_excess"] if row.get("train_excess") is not None else -1,
        row["train_resolved"],
    )


def select_options(rows: list[dict]) -> list[dict]:
    eligible = [row for row in rows if row["train_resolved"] >= MIN_TRAIN_RESOLVED and row["train_active_years"] >= 2]
    ordered = sorted(eligible, key=rank_key, reverse=True)
    options = []
    families = set()
    for row in ordered:
        if row["family"] in families:
            continue
        options.append(row)
        families.add(row["family"])
        if len(options) == 5:
            break
    return options


def events_for_row(frame: pd.DataFrame, state: dict, specs: list[dict], row: dict,
                   with_audit: bool = False) -> tuple[dict, dict]:
    spec = next(item for item in specs if spec_id(item) == row["algorithm"])
    confirm = json.loads(row["confirm"])
    points = candidate_points(frame, spec)
    audits = {kind: [] for kind in KINDS}
    events = {
        kind: confirm_candidates(
            frame, state, points[kind], kind, confirm, COOLDOWN,
            audits[kind] if with_audit else None,
        )
        for kind in KINDS
    }
    return events, audits


def baseline_rows(code: str, name: str, frame: pd.DataFrame, breadth: pd.DataFrame) -> tuple[list[dict], dict]:
    sources = {
        "生产bigwave底部": bottom_details(frame, breadth, code=code),
        "回滚swing6": swing_details(frame, breadth),
    }
    rows = []
    evaluations = {}
    for label, events in sources.items():
        fixed = {kind: evaluate_fixed(frame, events, kind) for kind in KINDS}
        legacy = evaluate_legacy(frame, events)
        evaluations[label] = {"fixed": fixed, "legacy": legacy}
        for kind in KINDS:
            rows.append(flatten_row(
                code, name, kind, label, label, {}, {},
                fixed[kind], legacy,
            ))
    return rows, evaluations


def choose_pairs(code: str, selections: dict, frames: dict, states: dict, specs: list[dict]) -> dict:
    cache = {}

    def events(row: dict) -> dict:
        key = (row["algorithm"], row["confirm"])
        if key not in cache:
            cache[key] = events_for_row(frames[code], states[code], specs, row)[0]
        return cache[key]

    pairs = []
    bottom_options = selections[code]["bottom"]["options"]
    top_options = selections[code]["top"]["options"]
    for bottom in bottom_options:
        bottom_events = events(bottom)
        for top in top_options:
            top_events = events(top)
            evaluation = evaluate_legacy(frames[code], {"bottom": bottom_events["bottom"], "top": top_events["top"]})
            bottom_train = evaluation["summary"]["bottom"]["train"]
            top_train = evaluation["summary"]["top"]["train"]
            if bottom_train["resolved"] < MIN_TRAIN_RESOLVED or top_train["resolved"] < MIN_TRAIN_RESOLVED:
                continue
            score = (
                min(bottom_train["wilson_low"], top_train["wilson_low"]),
                (bottom_train["rate"] + top_train["rate"]) / 2,
                min(bottom_train["resolved"], top_train["resolved"]),
            )
            pairs.append({
                "bottom_algorithm": bottom["algorithm"], "bottom_confirm": bottom["confirm"],
                "top_algorithm": top["algorithm"], "top_confirm": top["confirm"],
                "bottom_fixed_60d": bottom, "top_fixed_60d": top,
                "summary": evaluation["summary"], "events": evaluation["events"], "score": score,
            })
    pairs.sort(key=lambda pair: pair["score"], reverse=True)
    return {
        "chosen": pairs[0] if pairs else None,
        "options": [{key: value for key, value in pair.items() if key != "events"} for pair in pairs[:5]],
    }


def json_safe(value):
    if isinstance(value, dict):
        return {key: json_safe(item) for key, item in value.items()}
    if isinstance(value, list):
        return [json_safe(item) for item in value]
    if isinstance(value, np.generic):
        return json_safe(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    return value


def build_report(payload: dict, rows: list[dict]) -> str:
    lines = [
        "# 指数顶底算法批量优化结果", "",
        f"- 数据截止：{payload['data_end']}；输入哈希：`{payload['input_hash']}`",
        f"- 算法族：{payload['family_count']}；参数算法：{payload['spec_count']}；逐指数/方向/确认网格结果：{payload['candidate_row_count']} 行",
        f"- 训练段：{payload['protocol']['train']}；验证段：{payload['protocol']['validation']}；隔离：训练末尾30个交易日",
        "- 主准确率：信号后60个交易日内首次出现同向均线反转；满60日未出现为失败，不足60日为未决，未决不进入分母。",
        "- 原项目口径同时保留：同向均线反转先于反向信号为成功，反向信号先出现为失败。",
        "- 所有候选均经过价格+动量+量能三层确认；候选去重20日；信号价为确认日收盘。",
        f"- 单方向只按训练段 Wilson 95% 下界、准确率、随机基准超额、样本量排序；训练已判定至少{MIN_TRAIN_RESOLVED}条且覆盖2年为优先池，验证段不参与择参。",
        "- 原项目口径组合按训练段 min(顶部Wilson下界, 底部Wilson下界)、顶底平均准确率、较小样本量排序；验证段同样不参与。", "",
        "> 验证段已被本次查看，后续若据此再调参，它就不再是独立验证。小样本高准确率不等于稳定胜率。", "",
    ]
    for code in payload["selections"]:
        name = pc.INDEXES[code]
        lines.extend([f"## {name}（{code}）", ""])
        pair = payload["selections"][code]["pair_choice"]["chosen"]
        if pair:
            lines.extend([
                f"**原项目口径组合首选**：底部 `{pair['bottom_algorithm']}` + 顶部 `{pair['top_algorithm']}`", "",
                "| 方向 | 训练准确率[95%区间] | 验证准确率[95%区间] |", "|---|---:|---:|",
            ])
            for kind, label in (("bottom", "底部"), ("top", "顶部")):
                lines.append(f"| {label} | {summary_accuracy_text(pair['summary'][kind]['train'])} | {summary_accuracy_text(pair['summary'][kind]['validation'])} |")
            lines.append("")
        else:
            lines.extend(["**原项目口径组合首选**：无同时满足顶底训练样本门槛的组合。", ""])
        for kind, label in (("bottom", "底部"), ("top", "顶部")):
            lines.extend([f"### {label}信号", "", "| 排名 | 算法与参数 | 三层参数 | 训练准确率[95%区间] | 验证准确率[95%区间] | 随机基准/超额 | 验证20日均向收益 |", "|---|---|---|---:|---:|---:|---:|"])
            block = payload["selections"][code][kind]
            if not block["options"]:
                lines.append(f"| — | 无达到训练{MIN_TRAIN_RESOLVED}条已判定且覆盖2年的算法；小样本结果见CSV | — | — | — | — | — |")
            for rank, row in enumerate(block["options"], 1):
                baseline = f"{row['validation_random_baseline'] * 100:.1f}%/{row['validation_excess'] * 100:+.1f}%" if row["validation_random_baseline"] is not None else "—"
                ret = f"{row['validation_avg_signed_return_20'] * 100:.2f}%" if row["validation_avg_signed_return_20"] is not None else "—"
                lines.append(f"| {rank} | {row['algorithm']} | {row['confirm']} | {accuracy_text(row, 'train')} | {accuracy_text(row, 'validation')} | {baseline} | {ret} |")
            lines.append("")
            for baseline in block["baselines"]:
                lines.append(f"- 对照 {baseline['algorithm']}：训练 {accuracy_text(baseline, 'train')}；验证 {accuracy_text(baseline, 'validation')}")
            lines.append("")
    lines.extend(["## 本轮未测试的数据型方法", ""])
    for item in UNTESTED:
        lines.append(f"- **{item['family']}**：{item['reason']}")
    stem = payload["output_stem"]
    lines.extend(["", f"完整逐行结果见 `{stem}-grid.csv`；选中信号的逐日三层日志与事件见 `{stem}-results.json`。", ""])
    return "\n".join(lines)


def main(args) -> None:
    input_hash_before = research_input_hash()
    specs = candidate_specs()
    if args.limit_specs:
        specs = specs[:args.limit_specs]
    codes = args.codes.split(",") if args.codes else list(pc.INDEXES)
    breadth = load_breadth()
    rows = []
    frames = {}
    states = {}
    data_end = None
    for code in codes:
        name = pc.INDEXES[code]
        frame = pc.read_index_csv(code)
        frames[code] = frame
        data_end = max(data_end or frame["date"].max(), frame["date"].max())
        state = prepare(frame, breadth, {"confirm_window": 3})
        states[code] = state
        enrich_baseline = baseline_enricher(frame)
        print(f"[{code} {name}] 基线与 {len(specs)} 个参数算法", flush=True)
        baselines, _ = baseline_rows(code, name, frame, breadth)
        rows.extend(enrich_baseline(row) for row in baselines)
        for index, spec in enumerate(specs, 1):
            points = candidate_points(frame, spec)
            algorithm = spec_id(spec)
            for confirm in CONFIRM_GRID:
                events = {
                    kind: confirm_candidates(frame, state, points[kind], kind, confirm, COOLDOWN)
                    for kind in KINDS
                }
                fixed = {kind: evaluate_fixed(frame, events[kind], kind) for kind in KINDS}
                legacy = evaluate_legacy(frame, events)
                for kind in KINDS:
                    row = flatten_row(code, name, kind, algorithm, spec["family"], spec["params"], confirm, fixed[kind], legacy)
                    rows.append(enrich_baseline(row))
            if index % 10 == 0 or index == len(specs):
                print(f"  已完成 {index}/{len(specs)}", flush=True)
    grouped = defaultdict(list)
    for row in rows:
        if row["family"] not in {"生产bigwave底部", "回滚swing6"}:
            grouped[(row["code"], row["kind"])].append(row)
    selections = {}
    for code in codes:
        selections[code] = {}
        for kind in KINDS:
            options = select_options(grouped[(code, kind)])
            baselines = [row for row in rows if row["code"] == code and row["kind"] == kind and row["family"] in {"生产bigwave底部", "回滚swing6"}]
            chosen = options[0] if options else None
            chosen_events = None
            if chosen:
                events, audits = events_for_row(frames[code], states[code], specs, chosen, with_audit=True)
                legacy_eval = evaluate_legacy(frames[code], events)
                chosen_events = {
                    "fixed_60d": evaluate_fixed(frames[code], events[kind], kind),
                    "legacy": {"events": legacy_eval["events"][kind], "summary": legacy_eval["summary"][kind]},
                    "candidate_audit": audits[kind],
                }
            selections[code][kind] = {
                "options": options,
                "baselines": baselines,
                "chosen_events": chosen_events,
            }
    for code in codes:
        selections[code]["pair_choice"] = choose_pairs(code, selections, frames, states, specs)
    family_count = len({spec["family"] for spec in specs})
    candidate_row_count = sum(row["family"] not in {"生产bigwave底部", "回滚swing6"} for row in rows)
    first_frame = pc.read_index_csv(codes[0])
    train_def = pc.segment_definition(first_frame)
    input_hash_after = research_input_hash()
    if input_hash_before != input_hash_after:
        raise RuntimeError("回测期间原始指数行情或行业宽度发生变化，已停止输出")
    stem = args.output_stem
    payload = {
        "metadata": {
            "input_hash": input_hash_before, "data_end": data_end.strftime("%Y-%m-%d"),
            "family_count": family_count, "spec_count": len(specs), "candidate_row_count": candidate_row_count,
            "horizon": HORIZON, "cooldown": COOLDOWN, "confirm_grid": list(CONFIRM_GRID),
            "min_train_resolved": MIN_TRAIN_RESOLVED,
            "selection_rule": "train Wilson95 lower bound, rate, random-baseline excess, resolved; validation not used for ranking",
            "pair_selection_rule": "min(bottom/top train legacy Wilson95 lower), mean legacy rate, min resolved; validation not used",
        },
        "protocol": {
            "train": f"{pc.TRAIN_START.date()}~{train_def['train']['effective_end']}",
            "embargo": f"{train_def['embargo']['start']}~{train_def['embargo']['end']}",
            "validation": f"{pc.VALID_START.date()}~{data_end.date()}",
        },
        "untested": UNTESTED,
        "output_stem": stem,
        "selections": selections,
    }
    payload.update(payload.pop("metadata"))
    OUTPUT.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows).to_csv(OUTPUT / f"{stem}-grid.csv", index=False, encoding="utf-8-sig")
    (OUTPUT / f"{stem}-results.json").write_text(json.dumps(json_safe(payload), ensure_ascii=False, indent=2, allow_nan=False), encoding="utf-8")
    (OUTPUT / f"{stem}-report.md").write_text(build_report(payload, rows), encoding="utf-8")
    print(f"输出完成：{OUTPUT / stem}-*；共 {len(rows)} 行", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit-specs", type=int, default=0)
    parser.add_argument("--codes", default="")
    parser.add_argument("--output-stem", default="top-bottom-optimization")
    main(parser.parse_args())
