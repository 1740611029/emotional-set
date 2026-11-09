"""换源影响调研：申万行业指数 vs 东方财富同名行业板块指数。

只出报告，不写任何生产产物（sector-trends.json 等一律不动）。
目的：量化"把板块数据源从申万换成东财"会让日线差多少、会让多少天的
bull/warn/bear/rise 状态翻转，供决策使用。
所有比较只用同日已收盘数据对齐，不做任何前视：第 t 天的状态只依赖 t 及以前。
"""
from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

import pandas as pd  # noqa: E402

from confirmed_sector_trends import calculate_sector_trend  # noqa: E402
from fetch_market import RAW, call_with_retry  # noqa: E402
from research_algorithms import read_sector_csv  # noqa: E402

SECTOR_RAW = RAW / "sectors" / "first"
SECTOR_MANIFEST = RAW / "sectors-manifest.json"
EM_RAW = RAW / "sectors-em"
WARMUP_DAYS = 150          # Donchian20 + MA60 + 状态判定所需预热交易日
REPORT_MD = ROOT / "data" / "research" / "sector-source-em-vs-sw.md"
REPORT_JSON = ROOT / "data" / "research" / "sector-source-em-vs-sw.json"
STATE_ORDER = ("bull", "warn", "bear", "rise")


def sector_universe() -> list[dict]:
    """申万一级 31 个行业：代码 + 名称，名称用于匹配东财同名行业板块。"""
    manifest = json.loads(SECTOR_MANIFEST.read_text(encoding="utf-8"))
    return [{"code": item["code"], "name": item["name"]}
            for item in manifest if item["level"] == "first"]


def fetch_em(name: str, start: str, end: str, *, force: bool = False) -> pd.DataFrame:
    """拉取并缓存东财行业板块日线；缓存存在时直接读盘，避免重复下载。"""
    import akshare as ak  # 延迟导入，与服务启动解耦

    path = EM_RAW / f"{name}.csv"
    if path.exists() and not force:
        return read_sector_csv(path)
    frame = call_with_retry(
        ak.stock_board_industry_hist_em, symbol=name, start_date=start,
        end_date=end, period="日k", adjust="",
    )
    frame = frame.rename(columns={
        "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
        "最低": "low", "成交量": "volume", "成交额": "amount",
    })
    frame["date"] = pd.to_datetime(frame["date"])
    frame = frame.sort_values("date").drop_duplicates("date", keep="last")
    frame = frame[["date", "open", "close", "high", "low", "volume", "amount"]]
    EM_RAW.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")
    return frame.reset_index(drop=True)


def align(left: pd.DataFrame, right: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame]:
    """只保留两边共有的交易日，并按同一日期顺序返回，保证逐日可比。"""
    common = pd.DatetimeIndex(sorted(set(left["date"]) & set(right["date"])))
    return (left.set_index("date").loc[common].reset_index(),
            right.set_index("date").loc[common].reset_index())


def return_table(sw: pd.DataFrame, em: pd.DataFrame) -> pd.DataFrame:
    """逐日收盘涨跌幅差异（pct），两源都用同一口径：close/close - 1。"""
    table = pd.DataFrame({"date": sw["date"]})
    table["ret_sw"] = sw["close"].pct_change() * 100
    table["ret_em"] = em["close"].pct_change() * 100
    table["abs_diff"] = (table["ret_sw"] - table["ret_em"]).abs()
    return table.dropna(subset=["abs_diff"]).reset_index(drop=True)


def state_comparison(trend_sw: pd.DataFrame, trend_em: pd.DataFrame, start: str) -> dict:
    """评估段内两个源的 state/strength 差异；预热段与窗口未满的日子不计入。"""
    both = ((trend_sw["date"] >= pd.Timestamp(start))
            & trend_sw["state"].notna() & trend_em["state"].notna())
    window = trend_sw[both]
    em_window = trend_em[both]
    if window.empty:
        return {"days": 0, "state_match_rate": None, "flip_days": 0,
                "flip_breakdown": {}, "strength_abs_gap_mean": None,
                "strength_abs_gap_median": None, "strength_abs_gap_p90": None,
                "per_state": {}}
    same = int((window["state"] == em_window["state"]).sum())
    flips: dict[str, int] = {}
    for sw_state, em_state in zip(window["state"], em_window["state"]):
        if sw_state != em_state:
            key = f"{sw_state}->{em_state}"
            flips[key] = flips.get(key, 0) + 1
    strength_gap = (window["strength"] - em_window["strength"]).abs()
    per_state = {}
    for code in STATE_ORDER:
        sw_has = window["state"].eq(code)
        em_has = em_window["state"].eq(code)
        either = int((sw_has | em_has).sum())
        per_state[code] = {
            "sw_days": int(sw_has.sum()),
            "em_days": int(em_has.sum()),
            "both_days": int((sw_has & em_has).sum()),
            "either_days": either,
            # 至少一方给出该状态时，两源同时给出的命中率
            "match_rate": round((sw_has & em_has).sum() / either, 4) if either else None,
        }
    return {
        "days": int(len(window)),
        "state_match_rate": round(same / len(window), 4),
        "flip_days": int(len(window) - same),
        "flip_breakdown": dict(sorted(flips.items(), key=lambda kv: -kv[1])),
        "strength_abs_gap_mean": round(float(strength_gap.mean()), 4),
        "strength_abs_gap_median": round(float(strength_gap.median()), 4),
        "strength_abs_gap_p90": round(float(strength_gap.quantile(0.9)), 4),
        "per_state": per_state,
    }


def cumulative_gap(sw: pd.DataFrame, em: pd.DataFrame) -> float:
    """评估段首日归一化为 1 后，段末两源累计净值的百分比差。"""
    if sw.empty:
        return float("nan")
    base_sw = sw["close"].iat[0]
    base_em = em["close"].iat[0]
    return round(((sw["close"].iat[-1] / base_sw) / (em["close"].iat[-1] / base_em) - 1) * 100, 2)


def compare_sector(code: str, name: str, sw: pd.DataFrame, em: pd.DataFrame,
                   start: str, end: str) -> dict:
    """单行业：日线差异 + 状态差异。"""
    sw_cut = sw[(sw["date"] >= pd.Timestamp(start) - timedelta(days=520)) & (sw["date"] <= end)]
    em_cut = em[(em["date"] >= pd.Timestamp(start) - timedelta(days=520)) & (em["date"] <= end)]
    sw_aligned, em_aligned = align(sw_cut, em_cut)
    if len(sw_aligned) < WARMUP_DAYS:
        return {"code": code, "name": name, "skipped": f"共同交易日仅 {len(sw_aligned)} 天"}

    returns = return_table(sw_aligned, em_aligned)
    in_window = returns[(returns["date"] >= pd.Timestamp(start)) & (returns["date"] <= end)]
    trend_sw = calculate_sector_trend(sw_aligned)
    trend_em = calculate_sector_trend(em_aligned)
    states = state_comparison(trend_sw, trend_em, start)
    window_sw = sw_aligned[(sw_aligned["date"] >= pd.Timestamp(start)) & (sw_aligned["date"] <= end)]
    window_em = em_aligned[(em_aligned["date"] >= pd.Timestamp(start)) & (em_aligned["date"] <= end)]
    return {
        "code": code,
        "name": name,
        "common_days": int(len(sw_aligned)),
        "window_days": int(len(in_window)),
        "daily_return_abs_gap": {
            "mean": round(float(in_window["abs_diff"].mean()), 4),
            "median": round(float(in_window["abs_diff"].median()), 4),
            "p90": round(float(in_window["abs_diff"].quantile(0.9)), 4),
            "max": round(float(in_window["abs_diff"].max()), 4),
            "gt_1pct_days": int((in_window["abs_diff"] > 1).sum()),
            "gt_2pct_days": int((in_window["abs_diff"] > 2).sum()),
        },
        "cumulative_gap_pct": cumulative_gap(window_sw, window_em),
        **states,
    }


def summarize(results: list[dict], *, window_start: str, window_end: str) -> dict:
    """汇总：样本量必须与结论同时给出（比率一律注明参与统计的行业数与天数）。"""
    usable = [item for item in results if "skipped" not in item]
    if not usable:
        raise RuntimeError("没有任何行业具备可比数据，调研中止")
    days = sum(item["window_days"] for item in usable)
    state_days = sum(item["days"] for item in usable)
    flips = sum(item["flip_days"] for item in usable)
    gap_means = pd.Series([item["daily_return_abs_gap"]["mean"] for item in usable])
    flip_types: dict[str, int] = {}
    for item in usable:
        for key, value in item["flip_breakdown"].items():
            flip_types[key] = flip_types.get(key, 0) + value
    # bull↔bear 是方向完全相反，危害远大于同一侧的粒度差异
    severe = sum(value for key, value in flip_types.items()
                 if {"bull", "bear"} <= set(key.split("->")))
    cumulative = pd.Series([item["cumulative_gap_pct"] for item in usable], dtype=float)
    strength_gap = pd.Series([item["strength_abs_gap_mean"] for item in usable
                              if item["strength_abs_gap_mean"] is not None], dtype=float)
    per_state: dict[str, dict] = {}
    for code in STATE_ORDER:
        both = sum(item["per_state"][code]["both_days"] for item in usable)
        either = sum(item["per_state"][code]["either_days"] for item in usable)
        per_state[code] = {"both_days": both, "either_days": either,
                           "match_rate": round(both / either, 4) if either else None}
    return {
        "window": {"start": window_start, "end": window_end},
        "sectors_total": len(results),
        "sectors_compared": len(usable),
        "sector_days": days,
        "state_evaluated_days": state_days,
        "state_flip_sector_days": flips,
        "state_flip_rate": round(flips / state_days, 4) if state_days else None,
        "daily_return_gap_mean_across_sectors": round(float(gap_means.mean()), 4),
        "flip_type_distribution": dict(sorted(flip_types.items(), key=lambda kv: -kv[1])),
        "opposite_direction_days": severe,
        "opposite_direction_rate": round(severe / state_days, 4) if state_days else None,
        "cumulative_gap_pct": {"median": round(float(cumulative.median()), 2),
                              "min": round(float(cumulative.min()), 2),
                              "max": round(float(cumulative.max()), 2)},
        "strength_gap_points": {"median": round(float(strength_gap.median()), 2),
                               "max": round(float(strength_gap.max()), 2)},
        "bull_vs_nonbull_match_rate": per_state,
        "worst_state_agreement": sorted(
            ({"name": item["name"], "state_match_rate": item["state_match_rate"],
              "flip_days": item["flip_days"], "window_days": item["window_days"]}
             for item in usable),
            key=lambda row: row["state_match_rate"])[:8],
        "largest_return_gap": sorted(
            ({"name": item["name"], **item["daily_return_abs_gap"]} for item in usable),
            key=lambda row: -row["mean"])[:8],
        "per_sector": usable,
        "skipped": [{"code": item["code"], "name": item["name"], "reason": item["skipped"]}
                    for item in results if "skipped" in item],
    }


def render_markdown(summary: dict) -> str:
    window = summary["window"]
    lines = [
        "# 板块数据源换源影响调研：申万 vs 东方财富",
        "",
        f"- 评估区间：{window['start']} ~ {window['end']}",
        f"- 样本量：{summary['sectors_compared']}/{summary['sectors_total']} 个申万一级行业，"
        f"日线对比 {summary['sector_days']} 个行业日，状态对比 {summary['state_evaluated_days']} 个行业日",
        f"- 预热：每行业评估段前至少 {WARMUP_DAYS} 个共同交易日（MA60/Donchian20 完整窗口）",
        "",
        "## 结论",
        "",
        f"- 逐日收盘涨跌幅差异（两源各自 close/close-1）：跨行业均值 "
        f"**{summary['daily_return_gap_mean_across_sectors']} 个百分点**",
        f"- 状态（bull/warn/bear/rise）不一致：**{summary['state_flip_sector_days']} / "
        f"{summary['state_evaluated_days']} 个行业日 = {summary['state_flip_rate'] * 100:.1f}%**",
        f"- 其中方向完全相反（bull↔bear）：**{summary['opposite_direction_days']} 个行业日 = "
        f"{summary['opposite_direction_rate'] * 100:.1f}%**",
        f"- 有向强度平均绝对差（满量程 ±50 分）：中位 {summary['strength_gap_points']['median']} 分，"
        f"最大 {summary['strength_gap_points']['max']} 分",
        f"- 两年累计净值差：中位 {summary['cumulative_gap_pct']['median']}%，"
        f"范围 {summary['cumulative_gap_pct']['min']}% ~ {summary['cumulative_gap_pct']['max']}%",
        "",
        "## 翻转类型分布（申万 → 东财）",
        "",
        "| 申万 | 东财 | 行业日数 | 占翻转比例 |",
        "|---|---|---|---|",
    ]
    names = {"bull": "上涨确认", "warn": "预警", "bear": "下跌确认", "rise": "反弹"}
    total_flips = sum(summary["flip_type_distribution"].values()) or 1
    for key, value in summary["flip_type_distribution"].items():
        sw_state, em_state = key.split("->")
        lines.append(f"| {names.get(sw_state, sw_state)} | {names.get(em_state, em_state)} | "
                     f"{value} | {value / total_flips * 100:.1f}% |")
    lines += [
        "", "## 分状态命中率（至少一方给出该状态时，两源同时给出的比例）", "",
        "| 状态 | 两源同时 | 至少一方 | 命中率 |", "|---|---|---|---|",
    ]
    for code, row in summary["bull_vs_nonbull_match_rate"].items():
        rate = "—" if row["match_rate"] is None else f"{row['match_rate'] * 100:.1f}%"
        lines.append(f"| {names.get(code, code)}（{code}） | {row['both_days']} | "
                     f"{row['either_days']} | {rate} |")
    lines += ["", "## 状态最不一致的行业", "",
              "| 行业 | 一致率 | 翻转天数 | 区间天数 |", "|---|---|---|---|"]
    for row in summary["worst_state_agreement"]:
        lines.append(f"| {row['name']} | {row['state_match_rate'] * 100:.1f}% | "
                     f"{row['flip_days']} | {row['window_days']} |")
    lines += ["", "## 日线差异最大的行业（单位：个百分点）", "",
              "| 行业 | 均值 | 中位 | P90 | 最大 | >1pct 天数 | >2pct 天数 |",
              "|---|---|---|---|---|---|---|"]
    for row in summary["largest_return_gap"]:
        lines.append(f"| {row['name']} | {row['mean']} | {row['median']} | {row['p90']} | "
                     f"{row['max']} | {row['gt_1pct_days']} | {row['gt_2pct_days']} |")
    if summary["skipped"]:
        lines += ["", "## 未纳入对比", ""]
        for row in summary["skipped"]:
            lines.append(f"- {row['name']}（{row['code']}）：{row['reason']}")
    lines += [
        "",
        "## 本次调研未覆盖的部分",
        "",
        "- 只对比了 31 个申万一级行业；二级/三级板块（含 Ⅱ、Ⅲ 后缀）未参与",
        "- 两源名称一致不代表篮子一致：成分股与权重的具体差异本次未逐股核对",
        "- 未核对编算方法（基期、自由流通权重、是否剔除 ST 等）差异",
        "- 未评估换源后的信号准确率：真要换源需先重算全部信号历史，并在独立区间上重新验证胜率",
        "- 东财盘中即返回未完成 K 线，若改用该源必须只采收盘后定稿数据，否则会把盘中 bar 当已收盘数据使用",
    ]
    return "\n".join(lines) + "\n"


def main(*, window_days: int = 730, force_fetch: bool = False) -> dict:
    end = date.today()
    start = end - timedelta(days=window_days)
    # 预热段：评估段开始前多拉一年，保证窗口首日的 MA60/Donchian20 窗口就是完整的
    fetch_start = start - timedelta(days=520)
    window_start = start.strftime("%Y-%m-%d")
    window_end = end.strftime("%Y-%m-%d")
    results = []
    for item in sector_universe():
        sw_path = SECTOR_RAW / f"{item['code']}.csv"
        if not sw_path.exists():
            results.append({**item, "skipped": "本地申万日线缺失"})
            continue
        try:
            em = fetch_em(item["name"], fetch_start.strftime("%Y%m%d"),
                          window_end.replace("-", ""), force=force_fetch)
        except Exception as exc:
            results.append({**item, "skipped": f"东财拉取失败：{type(exc).__name__}"})
            continue
        try:
            results.append(compare_sector(item["code"], item["name"],
                                          read_sector_csv(sw_path), em, window_start, window_end))
        except Exception as exc:
            results.append({**item, "skipped": f"对比失败：{type(exc).__name__}: {exc}"})
        if "skipped" in results[-1]:
            print(f"[survey] {item['name']} 未纳入：{results[-1]['skipped']}", flush=True)
        print(f"[survey] {item['name']} 完成（{len(results)}/31）", flush=True)

    summary = summarize(results, window_start=window_start, window_end=window_end)
    summary["algorithm_version"] = "sector-donchian20-ma60-v1.1-independent-subsectors"
    summary["note"] = ("仅调研，未改动任何生产数据；东财板块指数盘中即返回未完成 K 线，"
                       "若改用该源必须只采收盘后定稿数据，否则会把盘中 bar 当已收盘数据使用")
    REPORT_JSON.parent.mkdir(parents=True, exist_ok=True)
    REPORT_JSON.write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    REPORT_MD.write_text(render_markdown(summary), encoding="utf-8")
    print(f"[survey] 报告已写入 {REPORT_MD.relative_to(ROOT)} 与 {REPORT_JSON.relative_to(ROOT)}")
    return summary


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="申万 vs 东财 板块日线换源影响调研")
    parser.add_argument("--days", type=int, default=730, help="评估区间长度（自然日）")
    parser.add_argument("--force-fetch", action="store_true", help="忽略缓存重新拉取东财日线")
    args = parser.parse_args()
    main(window_days=args.days, force_fetch=args.force_fetch)
