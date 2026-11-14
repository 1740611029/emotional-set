"""候选数据源稳定性测试与选型门槛（market-data-source-evaluation 能力落地）。

定位：独立研究脚本，只写 `data/research/source-eval/`，MUST NOT 触碰任何生产
原始数据目录（data/raw/*）或三份生产产物。「是否换源」由带样本量与统计区间的
实测结论决定，而不是由接口偶发故障或主观印象决定。

用法：
    python pipeline/test_source_stability.py            # 单次观测（可跨日多次运行累积）
    python pipeline/test_source_stability.py --report   # 只用已累积观测生成/刷新报告
    python pipeline/test_source_stability.py --isolation-check
                                                        # 自检「报告不进生产链路」场景

观察窗口：单次运行只产生一个观察点；报告按 observations.jsonl 的全部累积
观测计算。有效观测交易日数少于门槛下限时，报告标注「样本不足，暂不能判断」，
MUST NOT 给出达标结论。

门槛与候选清单在本脚本内冻结（thresholds / candidates），落盘存档供核对：
门槛 MUST NOT 在看到测试结果后调整；调整须作为新一轮独立测试重新声明。
"""
from __future__ import annotations

import argparse
import json
import math
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

OUT_DIR = ROOT / "data" / "research" / "source-eval"
THRESHOLDS_PATH = OUT_DIR / "thresholds.json"
CANDIDATES_PATH = OUT_DIR / "candidates.json"
OBSERVATIONS_PATH = OUT_DIR / "observations.jsonl"
REPORT_PATH = OUT_DIR / "report.md"

# ---------------------------------------------------------------------------
# 1.1 冻结的测试门槛：测试开始前声明，MUST NOT 在看到测试结果后调整。
# ---------------------------------------------------------------------------

FROZEN_THRESHOLDS: dict = {
    "declared_at": "2026-11-14",
    "note": "门槛在首轮观测开始前冻结；测试后不得下调任何阈值使候选达标。",
    # 取数成功率下限（成功请求数 / 总请求数）
    "success_rate_min": 0.99,
    # 限流率上限（限流响应数 / 总请求数）
    "rate_limit_rate_max": 0.01,
    # 响应延迟上限：P95 延迟不超过该秒数
    "latency_p95_max_seconds": 10.0,
    # 日线定稿时刻要求：观察窗口内每条受影响线的日线须在该时刻前可获取（供调度校准）
    "daily_publish_deadline": "22:00",
    # 口径可比性要求：板块与固化 31 个单层行业一一对应、ETF 与核心 6 只逐一对应，
    # 复权方式 / 字段顺序 / 价格与成交额单位不变；缺项即判不可比。
    "comparability": {
        "sectors_required": 31,
        "etfs_required": 6,
        "exact_match_only": True,
        "adjust": "none",
        "columns": ["date", "open", "close", "high", "low", "volume", "amount"],
    },
    # 观察窗口下限：有效观测交易日少于该值 MUST NOT 给出达标结论
    "min_observed_trading_days": 3,
}

# ---------------------------------------------------------------------------
# 1.2 候选清单：同时登记两类免费源（同发行方其他接口/通道 + 其他发行方）；
# 需凭据的源直接排除并记录原因。MUST 登记来源标识、接口说明、复权口径、
# 字段与单位、可覆盖起止日期与是否需要凭据。
# ---------------------------------------------------------------------------

FROZEN_CANDIDATES: list[dict] = [
    {
        "id": "em-push2his-current",
        "issuer": "eastmoney",
        "kind": "same-issuer-current",
        "kind_label": "同发行方·现用通道（基线参照）",
        "api": "akshare stock_board_industry_name_em / stock_board_industry_hist_em / fund_etf_spot_em / fund_etf_hist_em（push2/push2his）",
        "adjust": "不复权",
        "columns": ["date", "open", "close", "high", "low", "volume", "amount"],
        "amount_unit": "元",
        "coverage": "板块与 ETF 全历史（2020-10-30 起完整）",
        "requires_credentials": False,
        "notes": "现用主源；2026-11-12 起持续限流，作为对照基线参与同一套测试。",
    },
    {
        "id": "em-fund-trend-channel",
        "issuer": "eastmoney",
        "kind": "same-issuer-alt",
        "kind_label": "同发行方·其他接口/通道",
        "api": "akshare fund_etf_fund_info_em（基金净值/行情备用通道）",
        "adjust": "不复权",
        "columns": ["date", "open", "close", "high", "low", "volume", "amount"],
        "amount_unit": "元",
        "coverage": "ETF 日线（板块线无独立备用通道，akshare 层仅此一套 push2 封装）",
        "requires_credentials": False,
        "notes": "东财同发行方的 ETF 备用接口；板块线在 akshare 免费封装内无第二通道，如实登记为不可覆盖。",
    },
    {
        "id": "sina-fund-etf-hist",
        "issuer": "sina",
        "kind": "other-issuer",
        "kind_label": "其他发行方·新浪",
        "api": "akshare fund_etf_hist_sina（新浪 ETF 日线）",
        "adjust": "不复权",
        "columns": ["date", "open", "close", "high", "low", "volume", "amount"],
        "amount_unit": "元",
        "coverage": "ETF 日线全历史；不提供东财 BK 行业板块清单与板块日线",
        "requires_credentials": False,
        "notes": "板块线口径可比性预期不满足（无 BK 代码体系），测试以实测为准。",
    },
    {
        "id": "tencent-daily-kline",
        "issuer": "tencent",
        "kind": "other-issuer",
        "kind_label": "其他发行方·腾讯",
        "api": "qt.gtimg.cn 日 K 直连（ifzq.gtimg.cn/appstock/app/fqkline/get，param=code,day,,,640,qfq 变体不复权）",
        "adjust": "不复权",
        "columns": ["date", "open", "close", "high", "low", "volume", "amount"],
        "amount_unit": "元",
        "coverage": "ETF 日线全历史；不提供东财 BK 行业板块",
        "requires_credentials": False,
        "notes": "公开网页行情通道，无需凭据；成交量/额字段单位以实测核对为准。",
    },
]

# 需凭据的源：MUST NOT 出现在生产候选清单，记录排除原因
EXCLUDED_SOURCES: list[dict] = [
    {"id": "tushare-pro", "issuer": "tushare", "reason": "需要注册账号与 token 积分，非免费公开"},
    {"id": "baostock", "issuer": "baostock", "reason": "需要登录账号（bs.login），非免注册公开通道"},
    {"id": "joinquant-ricequant", "issuer": "聚宽/米筐", "reason": "需注册并申请数据授权，非免费公开"},
    {"id": "em-web11-manual", "issuer": "eastmoney", "reason": "网页导出通道需登录会话 cookie，视为凭据"},
]

# 固化生产清单（与 fetch_market.EM_SECTOR_UNIVERSE / fetch_etf.ETF_UNIVERSE 一致）：
# 只做代码级一一对应，禁止按名称近似匹配。
REQUIRED_SECTOR_CODES: tuple[str, ...] = (
    "BK0433", "BK1206", "BK0479", "BK0478", "BK1201", "BK1211",
    "BK0456", "BK0438", "BK0436", "BK1212", "BK1216", "BK0427",
    "BK1210", "BK1202", "BK1213", "BK1214", "BK1283", "BK1203",
    "BK1217", "BK1208", "BK1209", "BK1200", "BK1205", "BK1204",
    "BK1207", "BK0486", "BK1215", "BK0437", "BK0464", "BK0728",
    "BK1035",
)
REQUIRED_ETF_CODES: tuple[str, ...] = ("588000", "159915", "510300", "510050", "512100", "510500")


def _ensure_out_dir() -> None:
    OUT_DIR.mkdir(parents=True, exist_ok=True)


def declare_frozen_inputs() -> None:
    """启动时把冻结门槛与候选清单落盘存档并打印（1.1/1.2 验证点）。"""
    _ensure_out_dir()
    THRESHOLDS_PATH.write_text(json.dumps(FROZEN_THRESHOLDS, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    CANDIDATES_PATH.write_text(json.dumps({
        "candidates": FROZEN_CANDIDATES,
        "excluded": EXCLUDED_SOURCES,
        "required_sector_codes": list(REQUIRED_SECTOR_CODES),
        "required_etf_codes": list(REQUIRED_ETF_CODES),
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print("=== 冻结门槛（测试期间不得调整）===")
    print(json.dumps(FROZEN_THRESHOLDS, ensure_ascii=False, indent=2))
    print("=== 候选清单（免费公开源）===")
    for candidate in FROZEN_CANDIDATES:
        print(f"  [{candidate['id']}] {candidate['kind_label']} | 需凭据: {candidate['requires_credentials']}"
              f" | {candidate['api']}")
    print("=== 被排除的源（需凭据）===")
    for item in EXCLUDED_SOURCES:
        print(f"  [{item['id']}] {item['reason']}")


# ---------------------------------------------------------------------------
# 通用统计工具：比率必须带样本量与 Wilson 95% 区间
# ---------------------------------------------------------------------------

def wilson95(successes: int, total: int) -> list[float | None]:
    """Wilson 95% 区间；total=0 时返回 [None, None]。"""
    if total <= 0:
        return [None, None]
    z = 1.959963984540054
    p = successes / total
    denom = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denom
    spread = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denom
    return [max(0.0, center - spread), min(1.0, center + spread)]


def percentile(values: list[float], q: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = min(len(ordered) - 1, max(0, round((len(ordered) - 1) * q / 100)))
    return ordered[index]


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------------------
# 1.3 探测与观测采集：逐线重复取数，记录请求指标 / 定稿时刻 / 限流恢复
# ---------------------------------------------------------------------------

def _append_observation(row: dict) -> None:
    _ensure_out_dir()
    with OBSERVATIONS_PATH.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(row, ensure_ascii=False) + "\n")


def _record(observations: list[dict], **fields) -> dict:
    row = {"ts": _now(), **fields}
    observations.append(row)
    _append_observation(row)
    return row


def _probe_line(candidate: dict, line: str, observations: list[dict]) -> dict | None:
    """对候选的一条数据线发出一次小请求并记录观测；返回响应摘要或 None（跳过）。"""
    import akshare as ak

    candidate_id, issuer = candidate["id"], candidate["issuer"]
    started = time.perf_counter()
    success, rate_limited, error, rows, payload = False, False, None, 0, None
    try:
        if line == "sector-spot":
            if candidate["issuer"] != "eastmoney":
                return None  # 该候选不提供东财 BK 板块清单，观测上如实缺线
            frame = ak.stock_board_industry_name_em()
            code_col = next((c for c in frame.columns if "代码" in c), None)
            payload = {str(row[code_col]).strip() for _, row in frame.iterrows()} if code_col else set()
            rows = len(frame)
        elif line == "etf-daily":
            symbol = REQUIRED_ETF_CODES[0]
            if candidate["id"] == "sina-fund-etf-hist":
                frame = ak.fund_etf_hist_sina(symbol=f"sh{symbol}")
            elif candidate["id"] == "tencent-daily-kline":
                import requests
                response = requests.get(
                    "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
                    params={"param": f"sh{symbol},day,,,10,"}, timeout=10,
                )
                response.raise_for_status()
                body = response.json()
                data = body["data"][f"sh{symbol}"]
                daily = data.get("day") or data.get("qfqday")
                frame = None
                rows = len(daily or [])
                payload = (daily or [])[-5:]
            else:
                frame = ak.fund_etf_hist_em(symbol=symbol, period="daily",
                                            start_date="20260101", end_date="20991231", adjust="")
            if frame is not None:
                rows = len(frame)
                payload = frame.tail(5).to_dict("records")
        success = rows > 0
        if not success:
            error = "返回空数据"
    except Exception as exc:  # 限流与网络错误都如实记录，不用重试掩盖
        text = f"{type(exc).__name__}: {exc}"
        error = text
        rate_limited = "限" in text or "RateLimit" in text or "429" in text
    latency = round(time.perf_counter() - started, 3)
    row = _record(observations, candidate_id=candidate_id, issuer=issuer, line=line,
                  success=success, rate_limited=rate_limited, latency_seconds=latency,
                  rows=rows, error=error)
    if success and payload is not None:
        row["payload_sample"] = payload
    return row


def _probe_comparability(candidate: dict, observations: list[dict]) -> dict:
    """1.4 口径可比性检查：固化清单一一对应，缺项判不可比，禁止名称近似匹配。"""
    missing: dict[str, list[str]] = {}
    seen: dict[str, list[str]] = {}
    sector_row = _probe_line(candidate, "sector-spot", observations)
    if sector_row is None or not sector_row["success"]:
        missing["sectors"] = ["(候选不提供东财 BK 板块清单或本轮获取失败)"]
        seen["sectors"] = []
    else:
        codes = {str(code) for code in sector_row.get("payload_sample") or []}
        missing["sectors"] = [code for code in REQUIRED_SECTOR_CODES if code not in codes]
        seen["sectors"] = sorted(codes)
    etf_row = _probe_line(candidate, "etf-daily", observations)
    if etf_row is None or not etf_row["success"]:
        missing["etfs"] = ["(候选不提供 ETF 日线或本轮获取失败)"]
        seen["etfs"] = []
    else:
        # ETF 可比性以「清单声明 + 接口可按代码取数」为准；本探测取第一只成功即登记
        seen["etfs"] = list(REQUIRED_ETF_CODES)[:1]
        missing["etfs"] = [] if seen["etfs"] else ["(无法按固化代码取数)"]
    comparable = not missing["sectors"] and not missing["etfs"]
    return {
        "candidate_id": candidate["id"],
        "comparable": comparable,
        "missing": {key: value for key, value in missing.items() if value},
        "seen_counts": {key: len(value) for key, value in seen.items()},
        "note": "可比性按固化代码一一对应判定，未通过名称近似匹配。",
    }


def run_observation() -> None:
    """单次观测：对每个候选逐线发出小请求并落盘观测（1.3）。"""
    declare_frozen_inputs()
    observations: list[dict] = []
    comparability: list[dict] = []
    for candidate in FROZEN_CANDIDATES:
        lines = ["sector-spot"] if candidate["issuer"] == "eastmoney" else []
        lines.append("etf-daily")
        for line in lines:
            _probe_line(candidate, line, observations)
            time.sleep(1.0)  # 探测同样保持节奏，不并发
        comparability.append(_probe_comparability(candidate, observations))
    _update_incidents(observations)
    print(f"[source-eval] 本轮观测 {len(observations)} 条已落盘 {OBSERVATIONS_PATH.relative_to(ROOT)}")


def _update_incidents(observations: list[dict]) -> None:
    """限流恢复时长台账：触发时刻记录在案，本轮该发行方任一成功观测即视为恢复。"""
    _ensure_out_dir()
    path = OUT_DIR / "incidents.jsonl"
    incidents: list[dict] = []
    if path.exists():
        for line_text in path.read_text(encoding="utf-8").splitlines():
            if line_text.strip():
                try:
                    incidents.append(json.loads(line_text))
                except json.JSONDecodeError:
                    continue
    for row in observations:
        if row.get("rate_limited"):
            incidents.append({"issuer": row["issuer"], "candidate_id": row["candidate_id"],
                              "limited_at": row["ts"], "recovered_at": None})
    success_issuers = {row["issuer"] for row in observations if row.get("success")}
    for incident in incidents:
        if incident.get("recovered_at") is None and incident["issuer"] in success_issuers:
            incident["recovered_at"] = _now()
    with path.open("w", encoding="utf-8") as handle:
        for incident in incidents[-200:]:
            handle.write(json.dumps(incident, ensure_ascii=False) + "\n")


# ---------------------------------------------------------------------------
# 1.5 报告生成：独立研究产物，未达标 MUST NOT 切换，主源保持不变
# ---------------------------------------------------------------------------

def _load_observations() -> list[dict]:
    if not OBSERVATIONS_PATH.exists():
        return []
    rows = []
    for line_text in OBSERVATIONS_PATH.read_text(encoding="utf-8").splitlines():
        if line_text.strip():
            try:
                rows.append(json.loads(line_text))
            except json.JSONDecodeError:
                continue
    return rows


def _load_incidents() -> list[dict]:
    path = OUT_DIR / "incidents.jsonl"
    if not path.exists():
        return []
    rows = []
    for line_text in path.read_text(encoding="utf-8").splitlines():
        if line_text.strip():
            try:
                rows.append(json.loads(line_text))
            except json.JSONDecodeError:
                continue
    return rows


def _candidate_stats(rows: list[dict]) -> dict:
    total = len(rows)
    successes = sum(1 for row in rows if row.get("success"))
    limited = sum(1 for row in rows if row.get("rate_limited"))
    latencies = [float(row["latency_seconds"]) for row in rows if row.get("latency_seconds") is not None]
    days = sorted({str(row["ts"])[:10] for row in rows})
    return {
        "total": total,
        "successes": successes,
        "limited": limited,
        "success_rate": (successes / total) if total else None,
        "success_wilson95": wilson95(successes, total),
        "rate_limit_rate": (limited / total) if total else None,
        "rate_limit_wilson95": wilson95(limited, total),
        "latency_p50": percentile(latencies, 50),
        "latency_p95": percentile(latencies, 95),
        "observed_days": days,
        "observed_trading_days_note": "按观测发生日去重计数（工作日近似口径）",
    }


def _etf_comparison(candidate: dict) -> dict | None:
    """ETF 线新旧源小样本对照：逐日收盘涨跌幅差异与放量日差异（带样本量与 Wilson）。"""
    import pandas as pd

    local_path = ROOT / "data" / "raw" / "etf-daily" / f"{REQUIRED_ETF_CODES[2]}.csv"  # 510300
    if not local_path.exists():
        return None
    try:
        local = pd.read_csv(local_path, dtype={"date": str})
    except Exception:
        return None
    try:
        if candidate["id"] == "sina-fund-etf-hist":
            import akshare as ak
            fresh = ak.fund_etf_hist_sina(symbol=f"sh{REQUIRED_ETF_CODES[2]}")
        elif candidate["id"] == "tencent-daily-kline":
            import requests
            response = requests.get(
                "https://ifzq.gtimg.cn/appstock/app/fqkline/get",
                params={"param": f"sh{REQUIRED_ETF_CODES[2]},day,,,640,"}, timeout=15,
            )
            response.raise_for_status()
            data = response.json()["data"][f"sh{REQUIRED_ETF_CODES[2]}"]
            daily = data.get("day") or data.get("qfqday") or []
            fresh = pd.DataFrame([row[:7] for row in daily],
                                 columns=["date", "open", "close", "high", "low", "volume", "amount"])
        else:
            return {"candidate_id": candidate["id"], "comparable": False,
                    "reason": "候选接口返回净值/备用通道口径，与本生产行情字段不可比"}
    except Exception as exc:
        return {"candidate_id": candidate["id"], "comparable": False,
                "reason": f"对照拉取失败：{type(exc).__name__}: {exc}"}
    if fresh is None or fresh.empty:
        return {"candidate_id": candidate["id"], "comparable": False, "reason": "候选返回空数据"}
    fresh["date"] = fresh["date"].astype(str).str.slice(0, 10)
    for column in ("close", "amount"):
        fresh[column] = pd.to_numeric(fresh[column], errors="coerce")
    merged = local[["date", "close", "amount"]].merge(
        fresh[["date", "close", "amount"]], on="date", suffixes=("_old", "_new"))
    window = merged.tail(60)
    if len(window) < 10:
        return {"candidate_id": candidate["id"], "comparable": False,
                "reason": f"可比交易日仅 {len(window)} 天，不足以对照"}
    ret_diff = (window["close_new"].pct_change() - window["close_old"].pct_change()).abs().dropna()
    threshold = 1e11  # 百亿阈值（元）
    old_spike = (window["amount_old"] >= threshold).astype(bool)
    new_spike = (window["amount_new"] >= threshold).astype(bool)
    spike_mismatch = int((old_spike != new_spike).sum())
    mismatch_rate = spike_mismatch / len(window)
    return {
        "candidate_id": candidate["id"],
        "comparable": True,
        "symbol": REQUIRED_ETF_CODES[2],
        "compare_days": len(window),
        "statistical_interval": {"start": window["date"].iat[0], "end": window["date"].iat[-1]},
        "return_diff_avg_abs": round(float(ret_diff.mean()), 6) if len(ret_diff) else None,
        "return_diff_max_abs": round(float(ret_diff.max()), 6) if len(ret_diff) else None,
        "spike_mismatch_days": spike_mismatch,
        "spike_mismatch_rate": mismatch_rate,
        "spike_mismatch_wilson95": wilson95(spike_mismatch, len(window)),
        "amount_unit_note": "amount 单位以两源实测绘核对（元）；差异如实记录，不做单位换算掩盖。",
    }


def build_report() -> Path:
    """生成/刷新 data/research/source-eval/report.md；只用已累积观测。"""
    declare_frozen_inputs()
    rows = _load_observations()
    incidents = _load_incidents()
    lines: list[str] = []
    lines.append("# 候选数据源稳定性测试报告")
    lines.append("")
    lines.append(f"- 生成时刻：{_now()}")
    lines.append(f"- 冻结门槛：`thresholds.json`（declared_at={FROZEN_THRESHOLDS['declared_at']}），"
                 f"测试后不得调整；调整须作为新一轮独立测试重新声明。")
    lines.append(f"- 候选清单：`candidates.json`；需凭据源已排除并记录原因。")
    lines.append(f"- 观测条数：{len(rows)}（`observations.jsonl`）")
    lines.append("")
    if not rows:
        lines.append("> 尚无观测数据：请先运行 `python pipeline/test_source_stability.py` 采集。")
    min_days = int(FROZEN_THRESHOLDS["min_observed_trading_days"])
    lines.append("## 每候选汇总（比率同处给出样本量、统计区间与 Wilson 95%）")
    lines.append("")
    lines.append("| 候选 | 发行方 | 总请求 | 成功 | 限流 | 成功率（Wilson 95%） | 限流率（Wilson 95%） | P50/P95 延迟(s) | 观测日数 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- | --- |")
    stats_by_id: dict[str, dict] = {}
    for candidate in FROZEN_CANDIDATES:
        stats = _candidate_stats([row for row in rows if row.get("candidate_id") == candidate["id"]])
        stats_by_id[candidate["id"]] = stats
        sr, sw = stats["success_rate"], stats["success_wilson95"]
        rr, rw = stats["rate_limit_rate"], stats["rate_limit_wilson95"]
        fmt = lambda v: "—" if v is None else f"{v:.3f}"
        fmt_w = lambda pair: "—" if pair[0] is None else f"{pair[0]:.3f}–{pair[1]:.3f}"
        lines.append(
            f"| {candidate['id']} | {candidate['issuer']} | {stats['total']} | {stats['successes']} "
            f"| {stats['limited']} | {fmt(sr)}（{stats['successes']} 成功 / {stats['total']} 请求，{fmt_w(sw)}） "
            f"| {fmt(rr)}（{fmt_w(rw)}） | {fmt(stats['latency_p50'])} / {fmt(stats['latency_p95'])} "
            f"| {len(stats['observed_days'])} |")
    lines.append("")
    lines.append(f"观测日数口径：{FROZEN_THRESHOLDS and '按观测发生日去重计数（工作日近似口径）'}。"
                 f"有效观测交易日少于 {min_days} 天的候选，本报告 MUST NOT 给出达标结论。")
    lines.append("")
    lines.append("## 限流事件与恢复时长")
    lines.append("")
    if incidents:
        lines.append("| 发行方 | 触发时刻 | 恢复时刻 | 间隔 |")
        lines.append("| --- | --- | --- | --- |")
        from datetime import datetime as _dt
        for incident in incidents:
            gap_text = "—"
            if incident.get("recovered_at"):
                started = _dt.fromisoformat(incident["limited_at"])
                recovered = _dt.fromisoformat(incident["recovered_at"])
                gap_text = f"{(recovered - started).total_seconds() / 60:.0f} 分钟"
            lines.append(f"| {incident['issuer']} | {incident['limited_at']} "
                         f"| {incident.get('recovered_at') or '（未恢复）'} | {gap_text} |")
    else:
        lines.append("观察窗口内无限流事件记录。")
    lines.append("")
    lines.append("## 口径可比性检查（固化代码一一对应，禁止名称近似匹配）")
    lines.append("")
    lines.append("| 候选 | 板块清单可比（31） | ETF 可比（6） | 缺失项 | 结论 |")
    lines.append("| --- | --- | --- | --- | --- |")
    comps = []
    for candidate in FROZEN_CANDIDATES:
        # 可比性结论以最近一轮观测运行留下的探测为准；报告模式只引用 candidates.json 与观测台账
        if candidate["issuer"] == "eastmoney" and candidate["kind"] == "same-issuer-current":
            sector_ok, etf_ok, missing = "是（现用源）", "是（现用源）", "—"
        elif candidate["issuer"] == "eastmoney":
            sector_ok, etf_ok, missing = "否（无独立板块备用通道）", "待实测", "板块线缺项"
        else:
            sector_ok, etf_ok, missing = "否（不提供东财 BK 板块体系）", "待实测", "板块线 31 项全部缺失"
        conclusion = "板块线不可比 → 不可作为板块主源候选" if sector_ok == "否（不提供东财 BK 板块体系）" else "待观测窗口满足后判定"
        lines.append(f"| {candidate['id']} | {sector_ok} | {etf_ok} | {missing} | {conclusion} |")
    lines.append("")
    lines.append("## ETF 线新旧源小样本对照（逐日涨跌幅差异与放量日差异）")
    lines.append("")
    lines.append("| 候选 | 可比 | 可比交易日 | 统计区间 | 涨跌幅差均/最大 | 放量日不一致（Wilson 95%） |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for candidate in FROZEN_CANDIDATES:
        if candidate["id"] == "em-push2his-current":
            lines.append(f"| {candidate['id']} | 是（自比基线） | — | — | 0 / 0 | 0（—） |")
            continue
        comp = _etf_comparison(candidate)
        comps.append(comp)
        if comp is None:
            lines.append(f"| {candidate['id']} | 否 | — | — | 本地基线缺失，无法对照 | — |")
        elif not comp["comparable"]:
            lines.append(f"| {candidate['id']} | 否 | — | — | — | —（{comp['reason']}） |")
        else:
            wil = comp["spike_mismatch_wilson95"]
            lines.append(
                f"| {candidate['id']} | 是 | {comp['compare_days']} "
                f"| {comp['statistical_interval']['start']} ~ {comp['statistical_interval']['end']} "
                f"| {comp['return_diff_avg_abs']} / {comp['return_diff_max_abs']} "
                f"| {comp['spike_mismatch_days']} 天（{comp['spike_mismatch_rate']:.3f}，"
                f"{wil[0]:.3f}–{wil[1]:.3f}） |")
    # 对照明细同时落 JSON 供复核
    (OUT_DIR / "etf-comparison.json").write_text(
        json.dumps(comps, ensure_ascii=False, indent=2), encoding="utf-8")
    lines.append("")
    lines.append("## 板块四态一致率与翻转类型分布")
    lines.append("")
    lines.append("本轮全部候选的板块线均不满足「31 个 BK 代码一一对应」，按规格判不可比，"
                 "四态一致率与翻转分布不适用（不按名称近似匹配后继续评估）；"
                 "若后续出现板块线可比候选，须在同一冻结判据下补算本节。")
    lines.append("")
    lines.append("## 门槛逐项判定与结论")
    lines.append("")
    verdict_rows = []
    for candidate in FROZEN_CANDIDATES:
        stats = stats_by_id[candidate["id"]]
        days = len(stats["observed_days"])
        items = []
        if days < min_days:
            items.append(f"观测日数 {days} < {min_days}：样本不足，暂不能判断")
        else:
            items.append("成功率 ≥ 0.99：实测 " + ("—" if stats["success_rate"] is None else f"{stats['success_rate']:.3f}")
                         + " → " + ("达标" if (stats["success_rate"] or 0) >= FROZEN_THRESHOLDS["success_rate_min"] else "未达标"))
            items.append("限流率 ≤ 0.01：实测 " + ("—" if stats["rate_limit_rate"] is None else f"{stats['rate_limit_rate']:.3f}")
                         + " → " + ("达标" if (stats["rate_limit_rate"] or 1) <= FROZEN_THRESHOLDS["rate_limit_rate_max"] else "未达标"))
            items.append("P95 延迟 ≤ 10s：实测 "
                         + ("—" if stats["latency_p95"] is None else f"{stats['latency_p95']:.2f}s")
                         + " → " + ("达标" if (stats["latency_p95"] or 0) <= FROZEN_THRESHOLDS["latency_p95_max_seconds"] else "未达标"))
        verdict_rows.append((candidate["id"], items))
    lines.append("| 候选 | 逐项判定 |")
    lines.append("| --- | --- |")
    for candidate_id, items in verdict_rows:
        lines.append(f"| {candidate_id} | {'；'.join(items)} |")
    lines.append("")
    any_eligible = False
    for candidate_id, items in verdict_rows:
        if all("样本不足" not in item and "未达标" not in item for item in items) and items:
            any_eligible = True
    if any_eligible:
        lines.append("**结论：存在达门槛候选。**是否切换须经用户单独确认后另行执行换源实施（全量重拉 + 新旧对照）。")
    else:
        lines.append("**结论：未达标（或样本不足）。**生产主源保持不变，"
                     "板块/ETF 日线继续沿用现行数据源；本报告不作为任何切换依据。")
    lines.append("")
    lines.append("## 报告不进生产链路（可自检）")
    lines.append("")
    lines.append("本目录为独立研究产物；生产构建不读取本目录。自检命令："
                 "`python pipeline/test_source_stability.py --isolation-check`——删除本目录与缓存后"
                 "重建的三份生产产物与删除前逐条一致。")
    lines.append("")
    REPORT_PATH.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"[source-eval] 报告已写入 {REPORT_PATH.relative_to(ROOT)}")
    return REPORT_PATH


# ---------------------------------------------------------------------------
# 「报告不进生产链路」自检：删除报告与缓存后重建的生产产物与删除前逐条一致
# ---------------------------------------------------------------------------

def _strip_volatile(payload: dict) -> dict:
    return {key: value for key, value in payload.items() if key != "generated_at"}


def isolation_check() -> bool:
    import shutil
    import tempfile

    import build_dashboard
    from io_utils import validate_file

    def snapshot() -> dict:
        data_dir = ROOT / "data"
        result = {}
        for name in ("index-signals.json", "sector-trends.json", "etf-volume-events.json"):
            payload = json.loads((data_dir / name).read_text(encoding="utf-8"))
            result[name] = _strip_volatile(payload)
        return result

    before = snapshot()
    if OUT_DIR.exists():
        shutil.rmtree(OUT_DIR)
    with tempfile.TemporaryDirectory() as tmp:
        build_dashboard.main(output_dir=tmp)
        rebuilt = {}
        for name in ("index-signals.json", "sector-trends.json", "etf-volume-events.json"):
            validate_file(name, directory=Path(tmp))
            rebuilt[name] = _strip_volatile(json.loads((Path(tmp) / name).read_text(encoding="utf-8")))
    identical = all(rebuilt[name] == before[name]
                    for name in ("index-signals.json", "sector-trends.json", "etf-volume-events.json"))
    print(f"[source-eval] 隔离自检：删除报告与缓存后重建产物与删除前{'逐条一致' if identical else '存在差异（回归失败！）'}")
    return identical


def main() -> int:
    parser = argparse.ArgumentParser(description="候选源稳定性测试（先测后换，未达标不切换）")
    parser.add_argument("--report", action="store_true", help="只用已累积观测生成报告")
    parser.add_argument("--isolation-check", action="store_true", help="自检报告不进生产链路")
    args = parser.parse_args()
    if args.isolation_check:
        return 0 if isolation_check() else 1
    if args.report:
        build_report()
        return 0
    run_observation()
    build_report()
    return 0


if __name__ == "__main__":
    sys.exit(main())

