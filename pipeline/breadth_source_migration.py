"""横截面宽度换源对照：旧申万一级宽度 vs 新东财行业板块宽度，以及由此重算的指数顶底信号。

只读缓存与内存计算，绝不写生产文件（对照产物写 data/research/）。
两遍构建使用同一套指数算法、同一份指数日线，唯一差别是宽度输入，
因此信号差异全部可归因于板块数据源切换；阈值、六维权重与三层确认规则未作任何调整。

分段沿用生产划分：研究段 2020-10-30～2024-12-31、验证段 2025-01-01 起，
两段分别给出准确率与样本量，不用同一段数据既调参又算准确率。
"""
from __future__ import annotations

import json
import sys
import warnings
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

import pandas as pd  # noqa: E402

import build_dashboard as bd  # noqa: E402
from confirmed_sector_trends import HISTORY_DAYS  # noqa: E402
from research_algorithms import read_sector_csv  # noqa: E402
from swing_indicators import breadth_table  # noqa: E402

SW_FIRST = ROOT / "data" / "raw" / "sectors" / "first"
PRODUCTION_SIGNALS = ROOT / "data" / "index-signals.json"
REPORT_MD = ROOT / "data" / "research" / "breadth-source-migration-sw-vs-em.md"
REPORT_JSON = ROOT / "data" / "research" / "breadth-source-migration-sw-vs-em.json"
SEGMENTS = (("research", "研究段"), ("exploratory_validation", "验证段"))
BREADTH_COLUMNS = ("breadth20", "breadth60", "breadth_delta5")


def old_breadth() -> pd.DataFrame:
    """旧口径宽度输入：申万一级行业日线（仅对照基线，不参与生产计算）。"""
    files = sorted(SW_FIRST.glob("*.csv"))
    if len(files) < 20:
        raise RuntimeError(f"旧申万一级缓存文件仅 {len(files)} 个，无法出对照")
    return breadth_table({path.stem: read_sector_csv(path) for path in files})


def new_breadth() -> pd.DataFrame:
    """新口径宽度输入：与生产板块产物同源的东财行业板块日线。"""
    return bd.load_breadth()


def build_with(frame: pd.DataFrame) -> dict:
    """把宽度输入替换后在内存重建指数信号；不写盘。"""
    original = bd.load_breadth
    bd.load_breadth = lambda: frame
    try:
        return bd.build_indices()
    finally:
        bd.load_breadth = original


def stats_of(old: pd.DataFrame, new: pd.DataFrame, column: str, *, recent: bool) -> dict:
    """两源宽度差分布；样本数与区间必须与统计量同时给出。"""
    common = old.index.intersection(new.index)
    if recent:
        common = common[-HISTORY_DAYS:]
    left = pd.to_numeric(old.loc[common, column], errors="coerce")
    right = pd.to_numeric(new.loc[common, column], errors="coerce")
    both = left.notna() & right.notna()
    gap = (right - left)[both]
    window = common[both.to_numpy()]
    return {
        "column": column,
        "window": "最近展示窗口" if recent else "全部共有交易日",
        "common_days": int(len(common)),
        "samples": int(gap.size),
        "start": window[0].strftime("%Y-%m-%d") if len(window) else None,
        "end": window[-1].strftime("%Y-%m-%d") if len(window) else None,
        "old_missing_days": int((left.isna() & right.notna()).sum()),
        "new_missing_days": int((right.isna() & left.notna()).sum()),
        "signed_mean": round(float(gap.mean()), 4) if gap.size else None,
        "abs_mean": round(float(gap.abs().mean()), 4) if gap.size else None,
        "abs_median": round(float(gap.abs().median()), 4) if gap.size else None,
        "abs_p90": round(float(gap.abs().quantile(0.9)), 4) if gap.size else None,
        "abs_max": round(float(gap.abs().max()), 4) if gap.size else None,
        "new_gt_old_days": int((gap > 0).sum()) if gap.size else 0,
    }


def breadth_comparison(old: pd.DataFrame, new: pd.DataFrame) -> dict:
    parts = {}
    for column in BREADTH_COLUMNS:
        parts[column] = [stats_of(old, new, column, recent=False),
                         stats_of(old, new, column, recent=True)]
    return parts


def signal_comparison(old: dict, new: dict) -> dict:
    rows = []
    totals = {"old": 0, "new": 0, "same_date": 0, "same_type": 0, "added": 0, "removed": 0}
    for left, right in zip(old["indices"], new["indices"]):
        old_dates = [item["date"] for item in left["signals"]]
        new_dates = [item["date"] for item in right["signals"]]
        old_types = {item["date"]: item["type"] for item in left["signals"]}
        new_types = {item["date"]: item["type"] for item in right["signals"]}
        shared = sorted(set(old_dates) & set(new_dates))
        same_type = sum(1 for day in shared if old_types[day] == new_types[day])
        added = sorted(set(new_dates) - set(old_dates))
        removed = sorted(set(old_dates) - set(new_dates))
        history = left["history"]
        rows.append({
            "code": left["code"], "name": left["name"],
            "data_start": history[0]["date"] if history else None,
            "data_end": history[-1]["date"] if history else None,
            "old_count": len(old_dates), "new_count": len(new_dates),
            "same_date_count": len(shared), "same_date_same_type": same_type,
            "added": added, "removed": removed,
            "old_current": left["current_signal"], "new_current": right["current_signal"],
        })
        totals["old"] += len(old_dates)
        totals["new"] += len(new_dates)
        totals["same_date"] += len(shared)
        totals["same_type"] += same_type
        totals["added"] += len(added)
        totals["removed"] += len(removed)
    return {"per_index": rows, "totals": totals,
            "as_of": {"old": old["as_of"], "new": new["as_of"]}}


def aggregate_accuracy(payload: dict, segment: str, kind: str) -> dict:
    """跨 7 个指数合并某段某方向的计次与胜率（含未决），附 Wilson 区间。"""
    from research_algorithms import wilson_interval

    total = resolved = successes = failures = pending = 0
    for item in payload["indices"]:
        part = next((row for row in item["evaluation"]["segments"] if row["segment"] == segment), None)
        if part is None:
            continue
        summary = part["summary"].get(kind)
        if summary is None:
            continue
        total += summary["total"]
        resolved += summary["resolved"]
        successes += summary["successes"]
        failures += summary["failures"]
        pending += summary["pending"]
    return {
        "segment": segment, "type": kind,
        "entries": total, "resolved": resolved, "successes": successes,
        "failures": failures, "pending": pending,
        "rate": round(successes / resolved, 4) if resolved else None,
        "wilson95": [round(value, 4) if value is not None else None
                     for value in wilson_interval(successes, resolved)],
        "small_sample": resolved < 30,
    }


def accuracy_comparison(old: dict, new: dict) -> list[dict]:
    rows = []
    for key, label in SEGMENTS:
        for kind in ("bottom", "top"):
            old_row = aggregate_accuracy(old, key, kind)
            new_row = aggregate_accuracy(new, key, kind)
            rows.append({
                "segment": key, "segment_label": label, "type": kind,
                "old": old_row, "new": new_row,
                "rate_delta": (round(new_row["rate"] - old_row["rate"], 4)
                               if old_row["rate"] is not None and new_row["rate"] is not None else None),
            })
    return rows


def rules_unchanged(old: dict, new: dict, baseline: dict) -> dict:
    """判定规则不动的可核查证据：算法版本号、分段边界与元数据三处一致。"""
    def meta(payload: dict) -> dict:
        item = payload["indices"][0]["evaluation"]
        return {
            "algorithm_version": item["algorithm_version"],
            "rate_unit": item["rate_unit"],
            "boundary_policy": item["boundary_policy"],
            "research_status": item["research_status"],
            "segments": [[row["segment"], row["start"], row["end"]]
                         for row in item["segments"]],
        }
    current = meta(new)
    return {
        "old_build": meta(old),
        "new_build": current,
        "production_baseline": meta(baseline) if baseline else None,
        "identical_between_builds": meta(old) == current,
        "matches_production_baseline": (meta(baseline) == current) if baseline else None,
    }


def build_report() -> dict:
    old_frame = old_breadth()
    new_frame = new_breadth()
    payload_old = build_with(old_frame)
    payload_new = build_with(new_frame)
    baseline = (json.loads(PRODUCTION_SIGNALS.read_text(encoding="utf-8"))
                if PRODUCTION_SIGNALS.exists() else None)
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "change_reason": "板块日线数据源由申万行业指数切换为东方财富行业板块，横截面宽度输入随之切换",
        "impact_scope": "宽度是指数六维评分之一并参与三层确认的量能层，指数顶底信号历史整套重算",
        "breadth_input": {
            "old": {"label": "申万一级行业", "sectors": len(list(SW_FIRST.glob("*.csv"))),
                    "days": int(len(old_frame))},
            "new": {"label": bd.SECTOR_SOURCE, "sectors": len(list(bd.EM_SECTOR_RAW.glob("*.csv"))),
                    "days": int(len(new_frame))},
        },
        "breadth_gap": breadth_comparison(old_frame, new_frame),
        "signal_comparison": signal_comparison(payload_old, payload_new),
        "accuracy_comparison": accuracy_comparison(payload_old, payload_new),
        "rules_unchanged": rules_unchanged(payload_old, payload_new, baseline),
        "sector_product_consistency": {
            "sector_trends_as_of": json.loads((ROOT / "data" / "sector-trends.json")
                                              .read_text(encoding="utf-8"))["as_of"],
            "index_signals_new_as_of": payload_new["as_of"],
        },
        "note": ("两遍构建使用同一份指数日线与同一套指数算法，唯一差别是宽度输入；"
                 "未调任何阈值或权重。对照产物不进生产链路，删除后重建结果逐条一致。"),
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# 横截面宽度换源对照：申万一级 → 东财行业板块",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 变更原因：{report['change_reason']}",
        f"- 影响范围：{report['impact_scope']}",
        f"- 宽度输入：旧 {report['breadth_input']['old']['label']} "
        f"{report['breadth_input']['old']['sectors']} 个行业 / {report['breadth_input']['old']['days']} 行；"
        f"新 {report['breadth_input']['new']['label']} "
        f"{report['breadth_input']['new']['sectors']} 个行业 / {report['breadth_input']['new']['days']} 行",
        "",
        "## 宽度序列差异（占比为 0–1，差值 = 新 - 旧）",
        "",
        "| 指标 | 窗口 | 共有交易日 | 有效样本 | 区间 | 有向均值 | 绝对差均值 | 绝对差中位 | 绝对差 P90 | 绝对差最大 | 仅旧缺失 | 仅新缺失 |",
        "|---|---|---|---|---|---|---|---|---|---|---|---|",
    ]
    for column, parts in report["breadth_gap"].items():
        for part in parts:
            lines.append(
                f"| {column} | {part['window']} | {part['common_days']} | {part['samples']} "
                f"| {part['start']}~{part['end']} | {part['signed_mean']} | {part['abs_mean']} "
                f"| {part['abs_median']} | {part['abs_p90']} | {part['abs_max']} "
                f"| {part['old_missing_days']} | {part['new_missing_days']} |")
    totals = report["signal_comparison"]["totals"]
    lines += [
        "",
        "## 指数顶底信号对照（含样本数，不给孤立百分比）",
        "",
        f"- 合计：旧 {totals['old']} 条 → 新 {totals['new']} 条；日期一致 {totals['same_date']} 条"
        f"（其中类型一致 {totals['same_type']} 条）、新增 {totals['added']} 条、消失 {totals['removed']} 条",
        f"- as_of：旧 {report['signal_comparison']['as_of']['old']} / "
        f"新 {report['signal_comparison']['as_of']['new']}",
        "",
        "| 指数 | 行情区间 | 旧条数 | 新增数 | 消失数 | 日期一致 | 类型一致 | 旧当前信号 | 新当前信号 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["signal_comparison"]["per_index"]:
        lines.append(
            f"| {row['name']} | {row['data_start']}~{row['data_end']} | {row['old_count']} "
            f"| {len(row['added'])} | {len(row['removed'])} | {row['same_date_count']} "
            f"| {row['same_date_same_type']} | {row['old_current']['type']}@{row['old_current']['date']} "
            f"| {row['new_current']['type']}@{row['new_current']['date']} |")
    lines += [
        "",
        "## 分段准确率重验（跨 7 指数合并，未决不进入分母）",
        "",
        "| 分段 | 方向 | 旧触发/已判定/成功/失败/未决 | 旧胜率 | 新触发/已判定/成功/失败/未决 | 新胜率 | 胜率差 | 新 Wilson95 | 小样本 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for row in report["accuracy_comparison"]:
        old, new = row["old"], row["new"]
        lines.append(
            f"| {row['segment_label']} | {row['type']} "
            f"| {old['entries']}/{old['resolved']}/{old['successes']}/{old['failures']}/{old['pending']} "
            f"| {old['rate']} "
            f"| {new['entries']}/{new['resolved']}/{new['successes']}/{new['failures']}/{new['pending']} "
            f"| {new['rate']} | {row['rate_delta']} | {new['wilson95']} | {new['small_sample']} |")
    rules = report["rules_unchanged"]
    lines += [
        "",
        "## 判定规则未变（可核查）",
        "",
        f"- 算法版本/比率单位/边界策略/研究状态与分段边界：两遍构建是否一致 `{rules['identical_between_builds']}`；"
        f"与生产基线产物是否一致 `{rules['matches_production_baseline']}`",
        f"- 版本号：`{rules['new_build']['algorithm_version']}`，分段 `{rules['new_build']['segments']}`",
        f"- 板块产物 as_of `{report['sector_product_consistency']['sector_trends_as_of']}` 与"
        f"新宽度重建的指数产物 as_of `{report['sector_product_consistency']['index_signals_new_as_of']}` 是否同日："
        f"`{report['sector_product_consistency']['sector_trends_as_of'] == report['sector_product_consistency']['index_signals_new_as_of']}`",
        "",
        "## 消失与新增信号明细",
        "",
    ]
    for row in report["signal_comparison"]["per_index"]:
        lines.append(f"- **{row['name']}** 消失：{row['removed'] or '无'}；新增：{row['added'] or '无'}")
    lines += ["", "## 说明", "", report["note"], ""]
    return "\n".join(lines)


def main() -> None:
    warnings.simplefilter("ignore", UserWarning)
    report = build_report()
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    print(f"已写出宽度对照报告：{REPORT_MD.name} / {REPORT_JSON.name}", flush=True)
    totals = report["signal_comparison"]["totals"]
    print(f"信号合计：旧 {totals['old']} → 新 {totals['new']}，日期一致 {totals['same_date']}，"
          f"新增 {totals['added']}，消失 {totals['removed']}", flush=True)
    for row in report["accuracy_comparison"]:
        print(f"{row['segment_label']}/{row['type']}：旧 {row['old']['rate']}"
              f"（{row['old']['successes']}/{row['old']['resolved']}）→ 新 {row['new']['rate']}"
              f"（{row['new']['successes']}/{row['new']['resolved']}），差 {row['rate_delta']}", flush=True)


if __name__ == "__main__":
    main()
