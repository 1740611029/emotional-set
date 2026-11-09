"""板块换源对照报告：旧申万行业指数缓存 vs 新东财行业板块生产缓存。

只读缓存、只写 data/research/ 下的对照产物，绝不触碰任何生产文件
（index-signals.json / sector-trends.json / raw 数据一律不写）。
算法本体沿用 confirmed_sector_trends.calculate_sector_trend，两边输入完全同构，
因此报告中的每一处差异都只可能来自数据源切换，而不是判定口径变化。

统计分段与生产 evaluation 保持一致：研究段 2020-10-30～2024-12-31，
验证段 2025-01-01 起；另加生产展示窗口（最近 250 个可比交易日）。
所有比率一律附带样本数（可比板块数、行业日数）与区间起止。
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

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402

from confirmed_sector_trends import (  # noqa: E402
    ALGORITHM_VERSION, HISTORY_DAYS, calculate_sector_trend,
)
from fetch_market import (  # noqa: E402
    EM_SECTOR_RAW, EM_SECTOR_MANIFEST, MIN_SECTOR_ROWS, SECTOR_SOURCE,
)
from research_algorithms import RESEARCH_END, RESEARCH_START, read_sector_csv  # noqa: E402

SW_RAW = ROOT / "data" / "raw" / "sectors" / "first"
SW_MANIFEST = ROOT / "data" / "raw" / "sectors-manifest.json"
RESEARCH_LABEL = "研究段"
VALIDATION_LABEL = "验证段"
DISPLAY_LABEL = "展示窗口"
STATE_ORDER = ("bull", "warn", "bear", "rise")
REPORT_MD = ROOT / "data" / "research" / "sector-source-migration-sw-vs-em.md"
REPORT_JSON = ROOT / "data" / "research" / "sector-source-migration-sw-vs-em.json"
REASON = ("板块日线数据源由申万行业指数切换为东方财富行业板块（申万接口不稳定），"
          "同时板块清单由一级/二级两层改为单层 31 个行业")
SCOPE = ("影响板块四态、有向强度与横截面宽度输入，进而重算指数顶底信号历史；"
         "判定阈值、六维权重与三层确认规则均未调整")


def load_sw_universe() -> list[dict]:
    """旧口径：申万一级 31 个行业（代码 + 名称）。"""
    manifest = json.loads(SW_MANIFEST.read_text(encoding="utf-8"))
    return [{"code": item["code"], "name": item["name"],
             "insufficient_history": bool(item.get("insufficient_history"))}
            for item in manifest if item.get("level") == "first"]


def load_em_universe() -> list[dict]:
    """新口径：东财行业板块生产清单（BK 代码 + 名称）。"""
    manifest = json.loads(EM_SECTOR_MANIFEST.read_text(encoding="utf-8"))
    return [{"code": item["code"], "name": item["name"],
             "insufficient_history": bool(item.get("insufficient_history")),
             "rows": int(item.get("rows") or 0)}
            for item in manifest["boards"]]


def match_pairs(sw: list[dict], em: list[dict]) -> tuple[list[dict], list[str], list[str]]:
    """按行业名称配对（申万一级名称与东财行业板块名称 31/31 同名）。"""
    em_by_name = {item["name"]: item for item in em}
    pairs, only_sw = [], []
    for item in sw:
        twin = em_by_name.get(item["name"])
        if twin is None:
            only_sw.append(item["name"])
            continue
        pairs.append({"name": item["name"], "sw_code": item["code"], "em_code": twin["code"]})
    matched_names = {pair["name"] for pair in pairs}
    only_em = [item["name"] for item in em if item["name"] not in matched_names]
    return pairs, only_sw, only_em


def aligned_pair(pair: dict) -> tuple[pd.DataFrame, pd.DataFrame] | dict:
    sw_path = SW_RAW / f"{pair['sw_code']}.csv"
    em_path = EM_SECTOR_RAW / f"{pair['em_code']}.csv"
    if not sw_path.exists() or not em_path.exists():
        missing = "申万" if not sw_path.exists() else "东财"
        return {"skipped": f"缺少 {missing} 缓存文件"}
    sw = read_sector_csv(sw_path)
    em = read_sector_csv(em_path)
    common = pd.DatetimeIndex(sorted(set(sw["date"]) & set(em["date"])))
    if len(common) < MIN_SECTOR_ROWS:
        return {"skipped": f"共有交易日仅 {len(common)} 天，不足 {MIN_SECTOR_ROWS} 天"}
    sw_cut = sw.set_index("date").loc[common].reset_index()
    em_cut = em.set_index("date").loc[common].reset_index()
    try:
        return (calculate_sector_trend(sw_cut), calculate_sector_trend(em_cut))
    except ValueError as exc:
        return {"skipped": f"数据校验不通过：{exc}"}


def window_bounds(trend_sw: pd.DataFrame, trend_em: pd.DataFrame, label: str) -> tuple | None:
    """返回某一统计分段的（起止日期, 位置掩码）；无样本时返回 None。"""
    both = trend_sw["state"].notna() & trend_em["state"].notna()
    if label == RESEARCH_LABEL:
        mask = both & (trend_sw["date"] >= RESEARCH_START) & (trend_sw["date"] <= RESEARCH_END)
    elif label == VALIDATION_LABEL:
        mask = both & (trend_sw["date"] > RESEARCH_END)
    else:  # 展示窗口：最近 HISTORY_DAYS 个两源都有状态的交易日
        positions = np.flatnonzero(both.to_numpy())
        if positions.size == 0:
            return None
        mask = pd.Series(False, index=trend_sw.index)
        mask.iloc[positions[-HISTORY_DAYS:]] = True
    if not mask.any():
        return None
    return (trend_sw.loc[mask, "date"].iat[0], trend_sw.loc[mask, "date"].iat[-1], mask)


def compare_window(trend_sw: pd.DataFrame, trend_em: pd.DataFrame, label: str) -> dict | None:
    bounds = window_bounds(trend_sw, trend_em, label)
    if bounds is None:
        return None
    start, end, mask = bounds
    sw_states = trend_sw.loc[mask, "state"]
    em_states = trend_em.loc[mask, "state"]
    sw_strength = trend_sw.loc[mask, "strength"]
    em_strength = trend_em.loc[mask, "strength"]
    same = sw_states.eq(em_states)
    flips: dict[str, int] = {}
    for left, right in zip(sw_states[~same], em_states[~same]):
        key = f"{left}->{right}"
        flips[key] = flips.get(key, 0) + 1
    severe = sum(value for key, value in flips.items()
                 if {"bull", "bear"} <= set(key.split("->")))
    abs_gap = (sw_strength - em_strength).abs()
    per_state = {}
    for code in STATE_ORDER:
        sw_has = sw_states.eq(code)
        em_has = em_states.eq(code)
        either = int((sw_has | em_has).sum())
        per_state[code] = {
            "sw_days": int(sw_has.sum()), "em_days": int(em_has.sum()),
            "both_days": int((sw_has & em_has).sum()), "either_days": either,
        }
    return {
        "segment": label,
        "start": start.strftime("%Y-%m-%d"),
        "end": end.strftime("%Y-%m-%d"),
        "sector_days": int(mask.sum()),
        "state_match_days": int(same.sum()),
        "state_flip_days": int((~same).sum()),
        "flip_breakdown": dict(sorted(flips.items(), key=lambda kv: -kv[1])),
        "severe_bull_bear_flips": severe,
        "strength_signed_gap_mean": round(float((em_strength - sw_strength).mean()), 4),
        "strength_abs_gap_mean": round(float(abs_gap.mean()), 4),
        "strength_abs_gap_median": round(float(abs_gap.median()), 4),
        "strength_abs_gap_p90": round(float(abs_gap.quantile(0.9)), 4),
        "per_state": per_state,
    }


def compare_sector(pair: dict) -> dict:
    row = dict(pair)
    result = aligned_pair(pair)
    if isinstance(result, dict):
        row["skipped"] = result["skipped"]
        return row
    trend_sw, trend_em = result
    windows = {}
    for label in (RESEARCH_LABEL, VALIDATION_LABEL, DISPLAY_LABEL):
        part = compare_window(trend_sw, trend_em, label)
        if part is not None:
            windows[label] = part
    row["common_days"] = int(len(trend_sw))
    row["common_start"] = trend_sw["date"].iat[0].strftime("%Y-%m-%d")
    row["common_end"] = trend_sw["date"].iat[-1].strftime("%Y-%m-%d")
    row["windows"] = windows
    return row


def aggregate(rows: list[dict], label: str) -> dict:
    """按分段汇总：比率必须与参与统计的板块数、行业日数同时给出。"""
    usable = [row for row in rows if label in row.get("windows", {})]
    days = sum(row["windows"][label]["sector_days"] for row in usable)
    matched = sum(row["windows"][label]["state_match_days"] for row in usable)
    flips: dict[str, int] = {}
    severe = 0
    per_state = {code: {"sw_days": 0, "em_days": 0, "both_days": 0, "either_days": 0}
                 for code in STATE_ORDER}
    abs_gaps = []
    signed_gaps = []
    for row in usable:
        part = row["windows"][label]
        for key, value in part["flip_breakdown"].items():
            flips[key] = flips.get(key, 0) + value
        severe += part["severe_bull_bear_flips"]
        for code, counts in part["per_state"].items():
            for key in counts:
                per_state[code][key] += counts[key]
        abs_gaps.append((part["strength_abs_gap_mean"], part["sector_days"]))
        signed_gaps.append((part["strength_signed_gap_mean"], part["sector_days"]))
    weighted_abs = (sum(value * weight for value, weight in abs_gaps) / days) if days else None
    weighted_signed = (sum(value * weight for value, weight in signed_gaps) / days) if days else None
    for code, counts in per_state.items():
        either = counts["either_days"]
        counts["match_rate"] = round(counts["both_days"] / either, 4) if either else None
    starts = [row["windows"][label]["start"] for row in usable]
    ends = [row["windows"][label]["end"] for row in usable]
    return {
        "segment": label,
        "sectors_compared": len(usable),
        "sector_days": days,
        "start": min(starts) if starts else None,
        "end": max(ends) if ends else None,
        "state_match_rate": round(matched / days, 4) if days else None,
        "state_flip_rate": round((days - matched) / days, 4) if days else None,
        "severe_bull_bear_flip_rate": round(severe / days, 4) if days else None,
        "flip_breakdown_top": dict(sorted(flips.items(), key=lambda kv: -kv[1])[:12]),
        "strength_abs_gap_mean": round(weighted_abs, 4) if weighted_abs is not None else None,
        "strength_signed_gap_mean": round(weighted_signed, 4) if weighted_signed is not None else None,
        "per_state": per_state,
    }


def build_report() -> dict:
    sw_universe, em_universe = load_sw_universe(), load_em_universe()
    pairs, only_sw, only_em = match_pairs(sw_universe, em_universe)
    rows = [compare_sector(pair) for pair in pairs]
    skipped = [{"name": row["name"], "reason": row["skipped"]} for row in rows if "skipped" in row]
    windows = {label: aggregate(rows, label) for label in (RESEARCH_LABEL, VALIDATION_LABEL, DISPLAY_LABEL)}
    return {
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "change_reason": REASON,
        "impact_scope": SCOPE,
        "algorithm_version": ALGORITHM_VERSION,
        "old_source": {"label": "申万行业指数", "directory": str(SW_RAW.relative_to(ROOT)),
                       "sectors": len(sw_universe),
                       "insufficient_history": [item["name"] for item in sw_universe
                                                if item["insufficient_history"]]},
        "new_source": {"label": SECTOR_SOURCE, "directory": str(EM_SECTOR_RAW.relative_to(ROOT)),
                       "sectors": len(em_universe),
                       "insufficient_history": [item["name"] for item in em_universe
                                                if item["insufficient_history"]]},
        "name_matching": {"matched": len(pairs), "only_in_old": only_sw, "only_in_new": only_em},
        "skipped_sectors": skipped,
        "windows": windows,
        "per_sector": rows,
        "note": ("对照结果仅作换源核对，不进入生产链路；两遍计算使用同一套四态与有向强度算法，"
                 "差异全部来自数据源切换。展示窗口为最近 "
                 f"{HISTORY_DAYS} 个两源均有状态的交易日，统计区间按板块分别取起止后合并。"),
    }


def render_markdown(report: dict) -> str:
    lines = [
        "# 板块数据源换源对照：申万行业指数 → 东财行业板块",
        "",
        f"- 生成时间：{report['generated_at']}",
        f"- 变更原因：{report['change_reason']}",
        f"- 影响范围：{report['impact_scope']}",
        f"- 算法版本（新旧两遍计算同一个版本）：`{report['algorithm_version']}`",
        f"- 旧源：{report['old_source']['label']}，{report['old_source']['sectors']} 个行业，"
        f"目录 `{report['old_source']['directory']}`",
        f"- 新源：{report['new_source']['label']}，{report['new_source']['sectors']} 个行业，"
        f"目录 `{report['new_source']['directory']}`",
        f"- 名称配对：{report['name_matching']['matched']} 个同名行业；"
        f"仅旧源 {report['name_matching']['only_in_old'] or '无'}；"
        f"仅新源 {report['name_matching']['only_in_new'] or '无'}",
        "",
        "## 分段汇总（比率均附样本量）",
        "",
        "| 分段 | 可比板块 | 行业日样本 | 统计区间 | 四态一致率 | 翻转率 | bull↔bear 翻转率 | 强度绝对差 | 强度有向差 |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for part in report["windows"].values():
        lines.append(
            f"| {part['segment']} | {part['sectors_compared']} | {part['sector_days']} "
            f"| {part['start']} ~ {part['end']} "
            f"| {part['state_match_rate']} | {part['state_flip_rate']} "
            f"| {part['severe_bull_bear_flip_rate']} | {part['strength_abs_gap_mean']} "
            f"| {part['strength_signed_gap_mean']} |"
        )
    lines += ["", "## 四态命中情况（至少一方给出该状态时的共同命中率）", "",
              "| 分段 | 状态 | 旧源天数 | 新源天数 | 双方天数 | 至少一方 | 一致率 |",
              "|---|---|---|---|---|---|---|"]
    for part in report["windows"].values():
        for code, counts in part["per_state"].items():
            lines.append(f"| {part['segment']} | {code} | {counts['sw_days']} | {counts['em_days']} "
                         f"| {counts['both_days']} | {counts['either_days']} | {counts['match_rate']} |")
    lines += ["", "## 翻转类型分布（旧→新）", ""]
    for part in report["windows"].values():
        lines.append(f"- **{part['segment']}**：{part['flip_breakdown_top'] or '无翻转'}")
    lines += ["", "## 逐板块明细（展示窗口）", "",
              "| 行业 | 旧代码 | 新代码 | 可比交易日 | 一致率 | 翻转天数 | bull↔bear | 强度绝对差 |",
              "|---|---|---|---|---|---|---|---|"]
    for row in report["per_sector"]:
        if "skipped" in row:
            lines.append(f"| {row['name']} | {row['sw_code']} | {row['em_code']} | 跳过 "
                         f"| — | — | — | {row['skipped']} |")
            continue
        part = row["windows"].get(DISPLAY_LABEL)
        if part is None:
            lines.append(f"| {row['name']} | {row['sw_code']} | {row['em_code']} "
                         f"| {row['common_days']} | 无有效状态日 | — | — | — |")
            continue
        days = part["sector_days"]
        lines.append(
            f"| {row['name']} | {row['sw_code']} | {row['em_code']} | {row['common_days']} "
            f"| {round(part['state_match_days'] / days, 4)} | {part['state_flip_days']} "
            f"| {part['severe_bull_bear_flips']} | {part['strength_abs_gap_mean']} |")
    lines += ["", "## 历史不足与跳过清单", "",
              f"- 旧源标记历史不足：{report['old_source']['insufficient_history'] or '无'}",
              f"- 新源标记历史不足：{report['new_source']['insufficient_history'] or '无'}",
              f"- 对照中跳过：{report['skipped_sectors'] or '无'}",
              "", "## 说明", "", report["note"], ""]
    return "\n".join(lines)


def main() -> None:
    warnings.simplefilter("ignore", UserWarning)
    report = build_report()
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2, default=str),
                           encoding="utf-8")
    REPORT_MD.write_text(render_markdown(report), encoding="utf-8")
    print(f"已写出对照报告：{REPORT_MD.name} / {REPORT_JSON.name}", flush=True)
    for part in report["windows"].values():
        print(f"{part['segment']}：可比板块 {part['sectors_compared']}，行业日 {part['sector_days']}，"
              f"区间 {part['start']}~{part['end']}，四态一致率 {part['state_match_rate']}，"
              f"bull↔bear 翻转率 {part['severe_bull_bear_flip_rate']}，"
              f"强度绝对差 {part['strength_abs_gap_mean']}", flush=True)


if __name__ == "__main__":
    main()
