"""ETF 放量事件研究：名单核实 + 分层统计，产出 data/research/etf-volume-spike.{json,md}。

回答三个问题：破百亿的到底是哪几只（核心池 vs 宽基全集）、"只有 6 只"是否成立、
放量簇之后的指数表现是否真的偏向下跌底部区或趋势上行区。

本脚本只读原始行情与写 data/research/ 下的产物，不写任何生产 JSON；
事件识别、状态分类、分层统计全部调用 `etf_volume_events` 的同一套纯函数，
因此"图上标的事件"与"报告统计的事件"必然同源。
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from pathlib import Path

import etf_volume_events as ev
import pandas as pd
from fetch_etf import ETF_DAILY_RAW, ETF_UNIVERSE, fetch_etf_hist_sina, read_etf_csv
from fetch_market import INDEXES, INDEX_RAW
from rate_limiter import LIMITER
from research_algorithms import read_index_csv
from run_algorithm_research import write_json


def call_with_retry_ak(func, **kwargs):
    """研究脚本专用的简单重试包装：三次退避，识别限流即抛（与生产 call_with_retry 语义一致）。"""
    import time as _time

    import akshare as ak  # noqa: F401  # 延迟导入，仅研究脚本加载

    last_error = None
    for attempt in range(1, 4):
        try:
            frame = func(**kwargs)
            if frame is None or frame.empty:
                raise ValueError("接口返回空数据")
            return frame
        except Exception as exc:
            last_error = exc
            if attempt < 3:
                _time.sleep(attempt * 2)
    raise RuntimeError(f"接口重试后仍失败：{last_error}") from last_error


DATA = Path(__file__).resolve().parents[1] / "data"
RESEARCH = DATA / "research"
STUDY_JSON = "etf-volume-spike.json"
STUDY_MD = "etf-volume-spike.md"
# 924 起点：此前 ETF 份额基数不可比，跨年份只作定性提示
START = "2024-09-24"
# 宽基全集口径：名称以"宽基指数名 + ETF"开头，排除增强/等权/价值/成长等策略型产品
BROAD_PATTERN = re.compile(r"^(沪深300|上证50|中证500|中证1000|创业板|科创50)ETF")
PRIMARY_HORIZON = 5  # 三态结论的主口径前瞻期（其余前瞻期只列表，不单独下结论）
VERDICTS = (ev.VERDICT_TRUE, ev.VERDICT_FALSE, ev.VERDICT_UNKNOWN)


def _day_text(value) -> str:
    return str(value)[:10]


def _clip(frame: pd.DataFrame, start: str, end: str) -> pd.DataFrame:
    dates = frame["date"].astype(str).str.slice(0, 10)
    return frame[(dates >= start) & (dates <= end)].reset_index(drop=True)


def _sha256(paths) -> str:
    digest = hashlib.sha256()
    for path in sorted(paths):
        digest.update(Path(path).name.encode("utf-8"))
        digest.update(Path(path).read_bytes())
    return digest.hexdigest()


def _amount_text(amount: float) -> str:
    return f"{amount / 1e8:.1f} 亿元"


def _rate_text(cell: dict) -> str:
    span = f"（n={cell.get('samples', 0)}，区间 {cell.get('start') or '—'} ~ {cell.get('end') or '—'}）"
    if not cell.get("samples"):
        return f"—{span}"
    low, high = cell.get("wilson95") or (None, None)
    interval = f"[{low:.0%}, {high:.0%}]" if low is not None else "—"
    return f"{cell['rate']:.1%} {interval}{span}"


def _baseline_text(cell: dict, prefix: str) -> str:
    return _rate_text(
        {
            key: cell.get(f"{prefix}_{key}")
            for key in ("samples", "rate", "start", "end", "wilson95")
        }
    )


def _question_verdict(cell: dict) -> str:
    return cell["verdict"] if cell["verdict"] in VERDICTS else ev.VERDICT_UNKNOWN


def _range_text(stat: dict) -> str:
    return (
        f"{stat.get('start') or '—'} ~ {stat.get('end') or '—'}（n={stat.get('n', 0)}）"
    )


def load_inputs(end: str | None):
    """保留截至 end 的完整历史；研究起点只限制样本，不截掉指标预热数据。"""
    core = {}
    for item in ETF_UNIVERSE:
        path = ETF_DAILY_RAW / f"{item['code']}.csv"
        if not path.exists():
            raise ValueError(
                f"ETF 行情文件缺失：{item['code']}（先运行 python -m pipeline.fetch_etf）"
            )
        core[item["code"]] = read_etf_csv(path)
    boundary = end or min(frame["date"].iat[-1] for frame in core.values())
    core = {code: _clip(frame, "0001-01-01", boundary) for code, frame in core.items()}
    indices = {
        code: _clip(read_index_csv(INDEX_RAW / f"{code}.csv"), "0001-01-01", boundary)
        for code in INDEXES
    }
    return core, indices, boundary


def core_break_list(core: dict) -> dict:
    """核心池逐只的破百亿事实：首次日期、天数、区间最大成交额。"""
    rows = []
    for item in ETF_UNIVERSE:
        frame = core[item["code"]]
        hits = frame[frame["amount"].astype(float) >= ev.THRESHOLD_AMOUNT]
        rows.append(
            {
                "code": item["code"],
                "name": item["name"],
                "index": item["index"],
                "days": len(hits),
                "first_date": _day_text(hits["date"].iat[0]) if len(hits) else None,
                "max_amount": float(frame["amount"].astype(float).max()),
            }
        )
    return {
        "rows": rows,
        "codes": [row["code"] for row in rows if row["days"]],
        "complete": all(row["days"] for row in rows),
    }


def broad_candidates() -> list[dict]:
    """从东财 ETF 实时清单按名称筛宽基全集口径，只做名称过滤，不猜跟踪指数。

    生产清单校验已换腾讯六只口径（见 fetch_etf.etf_spot）；宽基研究需要全市场
    清单，仍走东财 clist（研究脚本一次性手动运行，受统一限速器节奏约束）。
    """
    # 东财 clist 系接口是封 IP 的主要来源：即使研究脚本也必须先取统一限速器
    # 令牌，冷却期内直接止损（可稍后重试或 --skip-broad），不得绕过冷却请求。
    if not LIMITER.acquire("eastmoney", line="etf-spot-research"):
        raise RuntimeError(
            "东财限流冷却中，跳过宽基全市场清单（可稍后重试或 --skip-broad）"
        )
    import akshare as ak  # noqa: PLC0415  # 延迟导入，仅宽基研究触网时加载

    frame = call_with_retry_ak(ak.fund_etf_spot_em)
    code_col = next((c for c in frame.columns if "代码" in c), None)
    name_col = next((c for c in frame.columns if "名称" in c), None)
    if not code_col or not name_col:
        raise ValueError(f"东财 ETF 清单缺少代码/名称字段：{frame.columns.tolist()}")
    return [
        {"code": str(row[code_col]).strip(), "name": str(row[name_col]).strip()}
        for _, row in frame.iterrows()
        if BROAD_PATTERN.match(str(row[name_col]).strip())
    ]


def broad_break_list(candidates: list[dict], boundary: str, start: str = START) -> dict:
    """逐只拉区间日线核对是否破百亿；单只失败只记录，不替换成别的标的。

    2026-11-14 换源：东财 fund_etf_hist_em 已移除，改走与生产一致的
    fetch_etf_hist_sina（新浪一次拉全历史），再以 _clip 截取研究区间。
    """
    hit, failed = [], []
    for item in candidates:
        try:
            frame = fetch_etf_hist_sina(item["code"])
        except Exception as exc:  # 停牌/退市/接口缺数据都不影响其余标的
            failed.append(
                {"code": item["code"], "name": item["name"], "error": str(exc)}
            )
            continue
        frame = _clip(frame, start, boundary)
        if frame.empty:
            continue
        amount = frame["amount"].astype(float)
        days = frame[amount >= ev.THRESHOLD_AMOUNT]
        if len(days) == 0:
            continue
        hit.append(
            {
                "code": item["code"],
                "name": item["name"],
                "days": len(days),
                "first_date": _day_text(days["date"].iat[0]),
                "max_amount": float(amount.max()),
            }
        )
    core_codes = {item["code"] for item in ETF_UNIVERSE}
    return {
        "scanned": len(candidates),
        "rows": hit,
        "failed": failed,
        "codes": [row["code"] for row in hit],
        "extra": [row for row in hit if row["code"] not in core_codes],
    }


def baseline_days(core: dict, index_frame: pd.DataFrame, dates) -> dict:
    """两类基线：同状态非放量日、50~100 亿普通放量日。"""
    volume_days = {row["date"] for row in ev.daily_records(core)}
    same_state = {state: [] for state in ev.STATES}
    for day in dates:
        if day in volume_days:
            continue
        same_state[ev.market_state(index_frame, day)].append(day)
    return {"same_state": same_state, "normal_volume": ev.normal_volume_days(core)}


def attach_normal_baseline(stats: dict, normal: dict) -> None:
    """把"50~100 亿普通放量日"基线并列到每一层的同一格，缺基线只标未见对照。"""
    for layer in stats["layers"]:
        minimum = (
            ev.layer_min_samples(layer["key"])
            if layer["kind"] == "level"
            else ev.MIN_LAYER_SAMPLES
        )
        for horizon in stats["horizons"]:
            cell = layer["horizons"][str(horizon)]
            base = normal["horizons"][str(horizon)]
            cell["normal_baseline_samples"] = base["samples"]
            cell["normal_baseline_rate"] = base["rate"]
            cell["normal_baseline_start"] = base["start"]
            cell["normal_baseline_end"] = base["end"]
            cell["normal_baseline_wilson95"] = base["wilson95"]
            cell["normal_verdict"] = ev.verdict_for(
                cell["rate"],
                cell["samples"],
                base["rate"],
                base["samples"],
                min_samples=minimum,
            )


def by_index_stats(events: list[dict], indices: dict) -> dict:
    """7 个注册指数的同期收益对照：结论主口径仍是沪深300。"""
    result = {}
    for code, frame in indices.items():
        entries = {}
        for level in ev.LEVELS:
            dates = [event["start_date"] for event in events if event["level"] == level]
            entries[level] = ev.layer_stats(
                frame, dates, min_samples=ev.layer_min_samples(level)
            )
        for state in ev.STATES:
            dates = [event["start_date"] for event in events if event["state"] == state]
            entries[state] = ev.layer_stats(frame, dates)
        result[code] = {"name": INDEXES.get(code, code), "layers": entries}
    return result


def segment_stats(events: list[dict], index_frame: pd.DataFrame, dates) -> dict:
    """按时间切成前后两段各自报告，避免"看了全样本才决定怎么切"。"""
    cutoff = dates[len(dates) // 2]
    result = {"cutoff": cutoff, "first": {}, "second": {}}
    for name, subset in (
        ("first", [row for row in events if row["start_date"] <= cutoff]),
        ("second", [row for row in events if row["start_date"] > cutoff]),
    ):
        for level in ev.LEVELS:
            picks = [row["start_date"] for row in subset if row["level"] == level]
            result[name][level] = ev.layer_stats(
                index_frame, picks, min_samples=ev.layer_min_samples(level)
            )
    return result


def build_questions(lists: dict, stats: dict) -> list[dict]:
    """每个被问判断都必须落到三态之一，且带样本量与统计区间。"""
    horizon = str(PRIMARY_HORIZON)
    layers = {layer["key"]: layer for layer in stats["layers"]}
    broad = lists["broad"]
    if broad.get("skipped"):
        only_six_verdict = ev.VERDICT_UNKNOWN
        only_six_evidence = "未扫描宽基全集（--skip-broad），无法判定"
    else:
        only_six_verdict = ev.VERDICT_FALSE if broad["extra"] else ev.VERDICT_TRUE
        only_six_evidence = (
            "、".join(
                f"{row['code']} {row['name']} 首次 {row['first_date']}"
                for row in broad["extra"]
            )
            or "无核心池之外的破百亿标的"
        )
    questions = [
        {
            "id": "core-six-all-break-100",
            "question": "核心 6 只在研究区间内是否都出现过单日成交额 ≥ 100 亿元",
            "verdict": ev.VERDICT_TRUE
            if lists["core"]["complete"]
            else ev.VERDICT_FALSE,
            "samples": lists["core"]["break_count"],
            "range": stats["range"],
            "evidence": "、".join(
                f"{row['code']} 首次 {row['first_date']}（{row['days']} 天）"
                for row in lists["core"]["rows"]
            ),
        },
        {
            "id": "only-six-broad-etfs",
            "question": "是否只有这 6 只宽基 ETF 单日成交破百亿",
            "verdict": only_six_verdict,
            "samples": len(broad["codes"]),
            "range": stats["range"],
            "evidence": only_six_evidence,
        },
        {
            "id": "all-six-same-day-not-single",
            "question": "核心 6 只全部同日破百亿的交易日是否不止一天（即不是孤例）",
            "verdict": ev.VERDICT_TRUE
            if len(lists["all_six"]["dates"]) >= 2
            else ev.VERDICT_FALSE,
            "samples": len(lists["all_six"]["dates"]),
            "range": stats["range"],
            "evidence": "、".join(lists["all_six"]["dates"])
            or "区间内从未 6 只全部同日破百亿",
        },
    ]
    for key, label in (
        ("L1", "L1 单只放量事件"),
        ("L2", "L2 多只放量事件"),
        ("L3", "L3 全池放量事件"),
    ):
        layer = layers[key]
        cell = layer["horizons"][horizon]
        questions.append(
            {
                "id": f"{key.lower()}-beats-same-state",
                "question": f"{label}后 {PRIMARY_HORIZON} 日胜率是否高于同状态非放量日基线",
                "verdict": _question_verdict(cell),
                "samples": cell["samples"],
                "range": [cell["start"], cell["end"]],
                "evidence": f"事件胜率 {_rate_text(cell)}，同状态基线 {_baseline_text(cell, 'baseline')}"
                + (
                    f"；{ev.VERDICT_NO_BASELINE}"
                    if not cell["baseline_samples"]
                    else ""
                ),
            }
        )
    for state in ev.STATES:
        layer = layers[state]
        cell = layer["horizons"][horizon]
        questions.append(
            {
                "id": f"state-{ev.STATES.index(state)}-beats-baseline",
                "question": f"{state}内放量事件后 {PRIMARY_HORIZON} 日胜率是否高于同状态非放量日基线",
                "verdict": _question_verdict(cell),
                "samples": cell["samples"],
                "range": [cell["start"], cell["end"]],
                "evidence": f"事件胜率 {_rate_text(cell)}，同状态基线 {_baseline_text(cell, 'baseline')}"
                + (
                    f"；{ev.VERDICT_NO_BASELINE}"
                    if not cell["baseline_samples"]
                    else ""
                ),
            }
        )
    return questions


def render_markdown(payload: dict) -> str:
    """章节固定，便于逐轮对照；每处胜率同行带 n 与区间。"""
    lines = ["# ETF 放量事件研究", ""]
    lines.append(f"- 算法版本：`{payload['algorithm_version']}`")
    lines.append(f"- 研究区间：{payload['range']['start']} ~ {payload['range']['end']}")
    lines.append(f"- 输入哈希：`{payload['input_hash']}`")
    lines.append(f"- 生成时间：{payload['generated_at']}")
    if previous := payload.get("comparison"):
        lines.append(f"- 对照旧报告：`{previous['report']}`（保留原件）")
        lines.append(f"- 旧输入哈希：`{previous['input_hash']}`")
        lines.append(
            "- 输入已变更，跨版本差异不能全部归因于算法；另见同输入对照验证记录。"
            if previous["input_changed"]
            else "- 对照报告与本报告输入哈希相同。"
        )
    lines.append("")
    lines.append("## 结论先行")
    lines.append("")
    for item in payload["questions"]:
        lines.append(
            f"- **{item['question']}** → `{item['verdict']}`"
            f"（n={item['samples']}，区间 {item['range'][0] or '—'} ~ {item['range'][1] or '—'}）"
        )
        if item["evidence"]:
            lines.append(f"  - {item['evidence']}")
    lines.append("")

    lines.append("## 冻结参数")
    lines.append("")
    lines.append("| 参数 | 冻结值 |")
    lines.append("| --- | --- |")
    for key, value in payload["preregistered"].items():
        lines.append(f"| {key} | {value} |")
    lines.append("")

    lines.append("## 名单核实（核心 6 只 vs 宽基全集）")
    lines.append("")
    lines.append(
        f"口径：单日成交额 ≥ {_amount_text(ev.THRESHOLD_AMOUNT)}，"
        f"区间 {payload['range']['start']} ~ {payload['range']['end']}，"
        "东财不复权日线、成交额单位元。"
    )
    lines.append("")
    lines.append("### 核心 6 只")
    lines.append("")
    lines.append("| 代码 | 名称 | 跟踪指数 | 破百亿天数 | 首次日期 | 区间最大成交额 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in payload["lists"]["core"]["rows"]:
        lines.append(
            f"| {row['code']} | {row['name']} | {row['index']} | {row['days']} | "
            f"{row['first_date'] or '—'} | {_amount_text(row['max_amount'])} |"
        )
    lines.append("")
    broad = payload["lists"]["broad"]
    if broad.get("skipped"):
        lines.append("### 宽基全集（本次未扫描，口径见冻结参数）")
    else:
        lines.append(f"### 宽基全集（按名称筛选，扫描 {broad['scanned']} 只）")
    lines.append("")
    lines.append("| 代码 | 名称 | 破百亿天数 | 首次日期 | 区间最大成交额 |")
    lines.append("| --- | --- | --- | --- | --- |")
    for row in broad["rows"]:
        lines.append(
            f"| {row['code']} | {row['name']} | {row['days']} | {row['first_date']} | "
            f"{_amount_text(row['max_amount'])} |"
        )
    lines.append("")
    if broad["failed"]:
        lines.append(
            f"取数失败 {len(broad['failed'])} 只（不替换为其他标的）："
            + "、".join(row["code"] for row in broad["failed"])
        )
        lines.append("")
    lines.append("### 核心池之外的反例")
    lines.append("")
    if broad["extra"]:
        for row in broad["extra"]:
            lines.append(
                f"- {row['code']} {row['name']}：首次 {row['first_date']}，"
                f"共 {row['days']} 天，峰值 {_amount_text(row['max_amount'])}"
            )
    else:
        lines.append("- 无")
    lines.append("")

    lines.append("## 事件清单")
    lines.append("")
    lines.append(
        f"6 只全部同日破百亿：{'、'.join(payload['lists']['all_six']['dates']) or '无'}"
    )
    lines.append("")
    lines.append(
        "统计级别固定为簇首日当天的级别，不随后续放量升级。结束日、天数和峰值为截至研究末日的事后描述，不参与首日收益分层。日级 L3 次数与首日为 L3 的事件簇数量不是同一口径。"
    )
    lines.append("")
    lines.append(
        "| 事件日 | 结束日 | 天数 | 首日级别 | 首日市场状态 | 峰值成交额（事后） |"
    )
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for row in payload["events"]:
        lines.append(
            f"| {row['start_date']} | {row['end_date']} | {row['days']} | {row['level']} | "
            f"{row['state']} | {_amount_text(row['peak_amount'])} |"
        )
    lines.append("")

    lines.append("## 分层统计")
    lines.append("")
    lines.append(f"主口径前瞻期 {PRIMARY_HORIZON} 日；胜率括号内为 Wilson 95% 区间。")
    for layer in payload["stats"]["layers"]:
        lines.append("")
        lines.append(
            f"### {layer['label']}（n={layer['n']}，区间 {layer['start'] or '—'} ~ {layer['end'] or '—'}）"
        )
        lines.append("")
        lines.append(
            "| 前瞻期 | 样本数 | 统计区间 | 胜率 [95%] | 平均收益 | 平均最大回撤 | 同状态基线 | 普通放量基线 | 结论 |"
        )
        lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
        for horizon in payload["stats"]["horizons"]:
            cell = layer["horizons"][str(horizon)]
            span = f"{cell['start'] or '—'} ~ {cell['end'] or '—'}"
            base_rate = _baseline_text(cell, "baseline")
            normal_rate = _baseline_text(cell, "normal_baseline")
            mean = "—" if cell["mean"] is None else f"{cell['mean']:.2%}"
            drawdown = (
                "—"
                if cell["avg_max_drawdown"] is None
                else f"{cell['avg_max_drawdown']:.2%}"
            )
            lines.append(
                f"| {horizon} 日 | {cell['samples']} | {span} | {_rate_text(cell)} | {mean} | {drawdown} | "
                f"{base_rate} | {normal_rate} | {_question_verdict(cell)} |"
            )
    lines.append("")
    lines.append("### 时间分段")
    lines.append("")
    lines.append(
        f"切分点：{payload['stats']['segments']['cutoff']}（按区间交易日数对半切，先声明后统计）"
    )
    lines.append("")
    lines.append("| 分段 | 级别 | 事件数 | 区间 | 5 日胜率 [95%] | 5 日平均收益 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for name, label in (("first", "前段"), ("second", "后段")):
        for level in ev.LEVELS:
            stat = payload["stats"]["segments"][name][level]
            cell = stat["horizons"][str(PRIMARY_HORIZON)]
            mean = "—" if cell["mean"] is None else f"{cell['mean']:.2%}"
            lines.append(
                f"| {label} | {level} | {cell['samples']} | {cell['start'] or '—'} ~ {cell['end'] or '—'} | "
                f"{_rate_text(cell)} | {mean} |"
            )
    lines.append("")
    lines.append("### 其余注册指数对照（5 日胜率）")
    lines.append("")
    codes = list(payload["stats"]["by_index"])
    lines.append(
        "| 层 | "
        + " | ".join(payload["stats"]["by_index"][code]["name"] for code in codes)
        + " |"
    )
    lines.append("| --- | " + " | ".join("---" for _ in codes) + " |")
    for key in list(ev.LEVELS) + list(ev.STATES):
        cells = []
        for code in codes:
            layer = payload["stats"]["by_index"][code]["layers"][key]
            cell = layer["horizons"][str(PRIMARY_HORIZON)]
            cells.append(_rate_text(cell))
        lines.append(f"| {key} | " + " | ".join(cells) + " |")
    lines.append("")

    lines.append("## 局限与风险")
    lines.append("")
    for note in payload["limits"]:
        lines.append(f"- {note}")
    lines.append("")
    return "\n".join(lines)


def study(start: str, end: str | None, with_broad: bool) -> dict:
    core_history, indices, boundary = load_inputs(end)
    if start > boundary:
        raise ValueError("研究起点不能晚于终点")
    basis = indices[ev.STATE_BASIS_INDEX]
    sample_dates = (
        _clip(basis, start, boundary)["date"].astype(str).str.slice(0, 10).tolist()
    )
    if not sample_dates:
        raise ValueError("研究区间内没有基准指数交易日")
    identified = ev.identify(core_history, basis)
    result = {
        "days": [row for row in identified["days"] if start <= row["date"] <= boundary],
        "events": [
            row
            for row in identified["events"]
            if start <= row["start_date"] <= boundary
        ],
    }
    # 更早历史仅用于指标和簇首日判定；名单与基线样本均限定在研究区间。
    core = {code: _clip(frame, start, boundary) for code, frame in core_history.items()}
    dates = [event["start_date"] for event in result["events"]]
    baselines = baseline_days(core, basis, sample_dates)
    stats = ev.forward_stats(result["events"], basis, baselines=baselines)
    attach_normal_baseline(stats, ev.layer_stats(basis, baselines["normal_volume"]))
    stats["range"] = [start, boundary]
    stats["by_index"] = by_index_stats(result["events"], indices)
    stats["segments"] = segment_stats(result["events"], basis, sample_dates)

    core_list = core_break_list(core)
    core_list["break_count"] = len(core_list["codes"])
    all_six = [row["date"] for row in result["days"] if row["level"] == "L3"]
    if with_broad:
        candidates = broad_candidates()
        broad = broad_break_list(candidates, boundary, start)
    else:
        broad = {
            "scanned": 0,
            "rows": [],
            "failed": [],
            "codes": [],
            "extra": [],
            "skipped": True,
        }
    paths = [ETF_DAILY_RAW / f"{item['code']}.csv" for item in ETF_UNIVERSE]
    paths += [INDEX_RAW / f"{code}.csv" for code in INDEXES]
    payload = {
        "algorithm_version": ev.ALGORITHM_VERSION,
        "generated_at": pd.Timestamp.now().isoformat(timespec="seconds"),
        "range": {"start": start, "end": boundary},
        "mode_basis_index": ev.STATE_BASIS_INDEX,
        "input_hash": _sha256(paths),
        "preregistered": {
            **ev.PREREGISTERED,
            "研究区间": [start, boundary],
            "宽基全集口径": BROAD_PATTERN.pattern,
            "主口径前瞻期(交易日)": PRIMARY_HORIZON,
        },
        "lists": {"core": core_list, "broad": broad, "all_six": {"dates": all_six}},
        "stats": stats,
        "events": [
            {
                "start_date": row["start_date"],
                "end_date": row["end_date"],
                "days": row["days"],
                "level": row["level"],
                "state": row["state"],
                "peak_amount": row["peak_amount"],
            }
            for row in result["events"]
        ],
        "event_dates": dates,
        "limits": [
            "ETF 成交额含二级市场换手与套利资金，不等于净申购，不能直接当作增量资金。",
            "2024 年以前 ETF 份额基数小，同一 100 亿阈值的含义不同；本报告结论只在研究区间内成立。",
            "首日级别固定后，日级 L3 可能发生在 L1/L2 簇的中途，不能把该簇首日回标为 L3；日级 L3 次数不等于 L3 层统计样本数。",
            "任一层有效样本不足冻结下限时不得据此采纳；成立标签只表示样本胜率高于基线，不代表统计显著性。",
            "研究起点之前的历史仅用于均线、高点及簇首日判定，不纳入事件和基线样本；研究终点之后的行情不参与本报告。",
            "市场状态只用沪深300 这一基准指数判定，其余指数仅作同期收益对照。",
            "宽基全集口径按名称前缀筛选，未核对基金合同中的跟踪指数，可能与第三方口径存在差异。",
        ],
    }
    payload["questions"] = build_questions(payload["lists"], payload["stats"])
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(
        description="ETF 放量事件研究（只写 data/research/）"
    )
    parser.add_argument(
        "--start", default=START, help="研究区间起点（默认 2024-09-24）"
    )
    parser.add_argument(
        "--end", default=None, help="研究区间终点（默认取 ETF 本地最后一天）"
    )
    parser.add_argument(
        "--out", default=STUDY_JSON, help="输出 JSON 文件名（落在 data/research/）"
    )
    parser.add_argument(
        "--previous",
        default=None,
        help="对照旧研究 JSON 文件名，仅记录输入变化，不改旧报告",
    )
    parser.add_argument(
        "--skip-broad",
        action="store_true",
        help="跳过宽基全集扫描（不触网），此时名单章节只报告核心 6 只",
    )
    args = parser.parse_args()

    payload = study(args.start, args.end, with_broad=not args.skip_broad)
    if args.previous:
        previous = json.loads((RESEARCH / args.previous).read_text(encoding="utf-8"))
        payload["comparison"] = {
            "report": args.previous,
            "input_hash": previous["input_hash"],
            "input_changed": previous["input_hash"] != payload["input_hash"],
        }
    write_json(args.out, payload)
    markdown = render_markdown(payload)
    RESEARCH.mkdir(parents=True, exist_ok=True)
    markdown_path = (RESEARCH / args.out).with_suffix(".md")
    markdown_path.write_text(markdown, encoding="utf-8")
    for item in payload["questions"]:
        print(f"{item['verdict']}：{item['question']}（n={item['samples']}）")
    print(f"已写入 {RESEARCH / args.out} 与 {markdown_path}")


if __name__ == "__main__":
    main()
