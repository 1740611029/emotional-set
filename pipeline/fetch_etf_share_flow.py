"""宽基 ETF 申购净流入数据线：全市场宽基池份额日变化 × 单位净值 → 逐日净流入。

设计要点（口径见 openspec/specs/etf-share-flow/spec.md）：
- 纯展示层：只读交易所份额与单位净值，落 `data/etf-share-flow.json`，不参与任何信号计算。
- 无未来函数：T 日净流入只用 T 日份额/单位净值与 T−1 日份额，均为 T 收盘后已发布数据。
- 数据源：沪市份额 `fund_etf_scale_sse(date)`、深市份额 `fund_scale_daily_szse(区间)`、
  单位净值 `fund_etf_spot_ths(date)`（某日全 ETF 快照，带 date 参数即取该日历史值）。
- 宽基池固化：按简称白名单从某基准日快照筛选并落 manifest，之后按代码取数，不逐日重建名单。
- 换源/换口径等于换一套历史，必须先在 openspec 留痕并经用户确认。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd

# 兼容 `python -m pipeline.fetch_etf_share_flow` 与直接执行：pipeline 扁平 import 需要自身在 path 上
sys_path = str(Path(__file__).resolve().parent)
if sys_path not in sys.path:  # noqa: SIM108
    sys.path.insert(0, sys_path)

from fetch_market import EmRateLimitedError, call_with_retry
from rate_limiter import LIMITER, REQUEST_GAP
from trading_calendar import last_completed_trading_day

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
SHARE_FLOW_RAW = RAW / "etf-share-flow"
MANIFEST = SHARE_FLOW_RAW / "universe.json"
OUTPUT = ROOT / "data" / "etf-share-flow.json"
INDEX_CSV = RAW / "indices-ths" / "sh000001.csv"

SHARE_SOURCE = "上交所ETF份额(dated快照)+深交所ETF份额(日频区间)"
NAV_SOURCE = "同花顺ETF每日单位净值快照(fund_etf_spot_ths, date参数)"
# 建库历史起点，与 main 的 --start 默认值一致；增量刷新只读此口径，不随交易日漂移
HISTORY_START = "2025-01-01"

# 宽基白名单 / 排除名单（与 spec 一致）。名称匹配用「包含」语义。
BROAD_KEYWORDS = (
    "沪深300", "中证300", "300ETF", "中证A500", "A500", "中证500", "500ETF",
    "中证1000", "1000ETF", "中证2000", "2000ETF", "上证50", "50ETF", "科创50",
    "科创板50", "科创100", "创业板ETF", "创业板指", "创业板50", "深证100",
    "上证综指", "上证180", "中证A50", "A50ETF", "中小100", "MSCI中国", "富时A50",
)
EXCLUDE_KEYWORDS = (
    "恒生", "恒指", "中概", "港股", "海外", "纳斯达克", "标普", "日经", "黄金",
    "豆粕", "有色", "稀土", "半导体", "芯片", "医药", "军工", "证券", "券商",
    "银行", "地产", "煤炭", "食品", "消费", "新能源", "光伏", "酒", "AI", "通信",
    "计算机", "电子", "债券", "国债", "可转债",
)


def is_broad(name: str) -> bool:
    """简称是否命中宽基白名单且不含排除词。纯函数，便于单测。"""
    text = str(name)
    if any(word in text for word in EXCLUDE_KEYWORDS):
        return False
    return any(word in text for word in BROAD_KEYWORDS)


def _read_index_calendar() -> list[str]:
    frame = pd.read_csv(INDEX_CSV, dtype={"date": str})
    return sorted(frame["date"].tolist())


def _sse_shares(day: str) -> dict[str, float]:
    """沪市某交易日份额 {代码: 份额}；限速冷却中抛错，由调用方止损。"""
    if not LIMITER.acquire("sse", line="etf-share-sse"):
        raise EmRateLimitedError("sse 冷却中，跳过 ETF 份额采集")
    frame = call_with_retry(ak.fund_etf_scale_sse, date=day.replace("-", ""))
    if frame is None or frame.empty:
        raise ValueError(f"上交所 {day} 份额快照为空")
    return {str(code): float(share) for code, share in
            zip(frame["基金代码"], frame["基金份额"]) if pd.notna(share)}


def _szse_shares_range(start: str, end: str) -> dict[str, dict[str, float]]:
    """深市区间份额 {日期: {代码: 份额}}；深交所接口区间上限 6 个月。"""
    if not LIMITER.acquire("szse", line="etf-share-szse"):
        raise EmRateLimitedError("szse 冷却中，跳过 ETF 份额采集")
    frame = call_with_retry(
        ak.fund_scale_daily_szse,
        start_date=start.replace("-", ""), end_date=end.replace("-", ""), symbol="ETF",
    )
    if frame is None or frame.empty:
        raise ValueError(f"深交所 {start}~{end} 份额区间为空")
    result: dict[str, dict[str, float]] = {}
    for raw_date, code, share in zip(frame["日期"], frame["基金代码"], frame["基金份额"]):
        if pd.isna(share):
            continue
        day = pd.Timestamp(raw_date).strftime("%Y-%m-%d")
        result.setdefault(day, {})[str(code)] = float(share)
    return result


def _nav_cache_path(day: str) -> Path:
    return SHARE_FLOW_RAW / "nav" / f"{day}.csv"


def _nav_snapshot(day: str, *, cached_only: bool = False) -> dict[str, float]:
    """某交易日全 ETF 单位净值 {代码: 单位净值}；落盘缓存，重建不重复请求。"""
    path = _nav_cache_path(day)
    if path.exists():
        frame = pd.read_csv(path, dtype={"code": str})
        return {str(c): float(v) for c, v in zip(frame["code"], frame["nav"]) if pd.notna(v)}
    if cached_only:
        return {}
    if not LIMITER.acquire("ths", line="etf-nav"):
        raise EmRateLimitedError("ths 冷却中，跳过 ETF 单位净值采集")
    frame = call_with_retry(ak.fund_etf_spot_ths, date=day.replace("-", ""))
    if frame is None or frame.empty:
        raise ValueError(f"同花顺 {day} 单位净值快照为空")
    result: dict[str, float] = {}
    for raw_code, value in zip(frame["基金代码"], frame["当前-单位净值"]):
        if pd.notna(value):
            result[str(raw_code)] = float(value)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame([{"code": c, "nav": v} for c, v in result.items()]).to_csv(path, index=False)
    return result


def build_universe(base_day: str) -> dict:
    """用基准日全市场份额快照 + 单位净值，按白名单固化宽基池。"""
    SHARE_FLOW_RAW.mkdir(parents=True, exist_ok=True)
    sse = _sse_shares(base_day)
    nav = _nav_snapshot(base_day)
    end = pd.Timestamp(base_day)
    start = (end - timedelta(days=170)).strftime("%Y-%m-%d")
    szse = _szse_shares_range(start, base_day).get(base_day, {})

    # 名称从同花顺带名称的快照取（份额接口只返回代码→份额，不含名称）
    frame = call_with_retry(ak.fund_etf_spot_ths, date=base_day.replace("-", ""))
    names = {str(c): str(n) for c, n in zip(frame["基金代码"], frame["基金名称"])}
    merged: dict[str, dict] = {}
    for code, share in {**sse, **szse}.items():
        name = names.get(code, "")
        if not is_broad(name):
            continue
        unit_nav = nav.get(code)
        if unit_nav is None or unit_nav <= 0:
            continue  # 无有效净值不纳入，避免金额不可折算
        aum_yi = share * unit_nav / 1e8
        if aum_yi < 5:  # 规模过小不纳入，避免长尾干扰且减少请求
            continue
        merged[code] = {
            "code": code, "name": name,
            "exchange": "sse" if code in sse else "szse",
            "base_share": share, "base_nav": unit_nav,
            "base_aum_yi": round(aum_yi, 2),
        }
    universe = sorted(merged.values(), key=lambda item: -item["base_aum_yi"])
    manifest = {
        "source": f"{SHARE_SOURCE}; {NAV_SOURCE}",
        "base_day": base_day,
        "broad_keywords": list(BROAD_KEYWORDS),
        "exclude_keywords": list(EXCLUDE_KEYWORDS),
        "min_aum_yi": 5,
        "universe": universe,
    }
    MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return manifest


def _share_cache_path(exchange: str, day: str) -> Path:
    return SHARE_FLOW_RAW / "shares" / exchange / f"{day}.csv"


def collect_shares(start: str, end: str, manifest: dict) -> dict:
    """逐交易日采集份额快照到 CSV；已落盘的交易日跳过，支持断点续传。"""
    SHARE_FLOW_RAW.mkdir(parents=True, exist_ok=True)
    universe = manifest["universe"]
    sse_codes = {item["code"] for item in universe if item["exchange"] == "sse"}
    szse_codes = {item["code"] for item in universe if item["exchange"] == "szse"}
    calendar = [day for day in _read_index_calendar() if start <= day <= end]
    fetched, skipped = 0, 0

    # 深市按 6 个月区间批量取，减少请求
    szse_days = [d for d in calendar if not _share_cache_path("szse", d).exists()]
    for batch_start, batch_end in _six_month_windows(szse_days):
        try:
            blocks = _szse_shares_range(batch_start, batch_end)
        except EmRateLimitedError as exc:
            print(f"[etf-share-flow] 深市限流，停止本轮：{exc}", flush=True)
            break
        for day, mapping in blocks.items():
            path = _share_cache_path("szse", day)
            if path.exists():
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            rows = [{"code": c, "share": mapping[c]} for c in szse_codes if c in mapping]
            pd.DataFrame(rows).to_csv(path, index=False)
            fetched += 1
        time_sleep()

    # 沪市逐日快照（无区间接口）
    for day in calendar:
        path = _share_cache_path("sse", day)
        if path.exists():
            skipped += 1
            continue
        try:
            mapping = _sse_shares(day)
        except EmRateLimitedError as exc:
            print(f"[etf-share-flow] 沪市限流，停止本轮（已取 {fetched} 日）：{exc}", flush=True)
            break
        path.parent.mkdir(parents=True, exist_ok=True)
        rows = [{"code": c, "share": mapping[c]} for c in sse_codes if c in mapping]
        pd.DataFrame(rows).to_csv(path, index=False)
        fetched += 1
        time_sleep()
    return {"fetched": fetched, "skipped": skipped, "trading_days": len(calendar)}


def _six_month_windows(days: list[str]):
    if not days:
        return
    cursor = pd.Timestamp(days[0])
    group: list[str] = []
    for day in days:
        stamp = pd.Timestamp(day)
        if (stamp - cursor).days > 180:
            yield group[0], group[-1]
            group, cursor = [], stamp
        group.append(day)
    if group:
        yield group[0], group[-1]


def time_sleep() -> None:
    import time
    time.sleep(REQUEST_GAP)


def _read_share(exchange: str, day: str) -> dict[str, float]:
    path = _share_cache_path(exchange, day)
    if not path.exists():
        return {}
    frame = pd.read_csv(path, dtype={"code": str})
    return {str(c): float(s) for c, s in zip(frame["code"], frame["share"]) if pd.notna(s)}


def compute(start: str | None = None, end: str | None = None) -> dict:
    """从落盘份额与净值计算逐日净流入；产物由 as_of/generated_at 固定。"""
    from server.snapshot import now_iso  # noqa: PLC0415  # sys.path 已配

    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    universe = manifest["universe"]
    calendar = [d for d in _read_index_calendar()
                if (start is None or d >= start) and (end is None or d <= end)]
    if not calendar:
        raise ValueError("净流入计算区间内无交易日")

    nav: dict[str, dict[str, float]] = {}
    for day in calendar:
        try:
            nav[day] = _nav_snapshot(day)
        except EmRateLimitedError as exc:
            print(f"[etf-share-flow] 净值限流，停止本轮：{exc}", flush=True)
            raise
        time_sleep()

    # 每只 ETF 的前一有份额交易日：用日历序定位，缺失日按最近已落盘的前一日
    last_share: dict[str, tuple[str, float]] = {}
    dates: list[str] = []
    inflow_yuan: list[float] = []
    coverage: list[int] = []
    for position, day in enumerate(calendar):
        current: dict[str, float] = {}
        for exchange in ("sse", "szse"):
            current.update(_read_share(exchange, day))
        day_nav = nav[day]
        prev_day = calendar[position - 1] if position > 0 else None
        total = 0.0
        counted = 0
        for item in universe:
            code = item["code"]
            share = current.get(code)
            if share is None:
                continue
            nav_value = day_nav.get(code)
            if nav_value is None or nav_value <= 0:
                continue
            previous = last_share.get(code)
            # 只用「上一个交易日」的份额求差；跨日缺口不把多日变化塞进单日，上市首日跳过
            if previous is not None and previous[0] == prev_day:
                total += (share - previous[1]) * nav_value
                counted += 1
            last_share[code] = (day, share)
        dates.append(day)
        inflow_yuan.append(round(total, 2))
        coverage.append(counted)

    index_frame = pd.read_csv(INDEX_CSV, dtype={"date": str})
    index_closes = {str(d): (float(c) if pd.notna(c) else None)
                    for d, c in zip(index_frame["date"], index_frame["close"])}
    index_close = [index_closes.get(day) for day in dates]

    inflow_yi = [round(value / 1e8, 2) for value in inflow_yuan]
    recent60 = inflow_yi[-60:]
    payload = {
        "data_state": "ready",
        "as_of": dates[-1],
        "generated_at": now_iso(),
        "coverage": {
            "start": dates[0], "end": dates[-1], "trading_days": len(dates),
            "etf_count": len(universe),
            "min_counted": min(coverage) if coverage else 0,
            "max_counted": max(coverage) if coverage else 0,
        },
        "dates": dates,
        "net_inflow_yi": inflow_yi,
        "index_close": index_close,
        "stats": {
            "last_day_yi": inflow_yi[-1] if inflow_yi else None,
            "last_60d_yi": round(sum(recent60), 2),
            "last_60d_days": len(recent60),
        },
        "universe": [{"code": i["code"], "name": i["name"], "index": i.get("index", "")}
                     for i in universe],
        "sources": {"share": SHARE_SOURCE, "nav": NAV_SOURCE},
    }
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    return payload


def failed(reason: str) -> dict:
    """降级产物：字段齐全、数值空数组，前端不猜测。"""
    from server.snapshot import now_iso  # noqa: PLC0415
    return {
        "data_state": "preparing", "as_of": None, "generated_at": now_iso(),
        "coverage": {"start": None, "end": None, "trading_days": 0,
                     "etf_count": 0, "min_counted": 0, "max_counted": 0},
        "dates": [], "net_inflow_yi": [], "index_close": [],
        "stats": {"last_day_yi": None, "last_60d_yi": 0.0, "last_60d_days": 0},
        "universe": [], "sources": {}, "stale": True, "stale_reason": reason,
    }


def _share_cache_ready(exchange: str, day: str) -> bool:
    """份额缓存是否存在且有内容（只有表头的空文件不算齐备）。"""
    path = _share_cache_path(exchange, day)
    return path.exists() and path.stat().st_size > len("code,share\n") + 1


def update_share_flow() -> dict:
    """展示层增量刷新入口，供 bootstrap / 调度调用。

    与 `build` 子命令的关键区别：只读已固化的 universe，绝不重建宽基池
    （重建会换基准日 = 换一套历史，口径必须固定）；只把「沪深两市份额都齐备」
    的交易日交给 compute，缺任一侧就不推进 as_of，避免份额缺口被写成假的 0 净流入。
    未初始化 / 限流 / 源未发布一律返回状态，不抛给调用方，由调度层告警。
    """
    if not MANIFEST.exists() or not OUTPUT.exists():
        return {"status": "uninitialized", "as_of": None, "error": None,
                "reason": "宽基池清单或净流入产物缺失，需先显式建库"}
    try:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "failed", "as_of": None, "error": f"产物读取失败：{exc}"}

    previous_as_of = str(previous.get("as_of") or "")
    start = str((previous.get("coverage") or {}).get("start") or HISTORY_START)
    try:
        end = last_completed_trading_day()[0]
        calendar = [d for d in _read_index_calendar() if start <= d <= end]
    except Exception as exc:  # noqa: BLE001  # 展示层，失败只告警
        return {"status": "failed", "as_of": previous_as_of or None,
                "error": f"交易日判定失败：{exc}"}
    if previous_as_of and end <= previous_as_of:
        return {"status": "skipped", "as_of": previous_as_of, "error": None}

    # 已落盘的交易日自动跳过；限流时 collect_shares 内部止损，不抛异常
    try:
        collected = collect_shares(start, end, manifest)
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "as_of": previous_as_of or None,
                "error": f"份额采集失败：{exc}"}

    ready = [d for d in calendar
             if _share_cache_ready("sse", d) and _share_cache_ready("szse", d)]
    if not ready:
        return {"status": "no_share_data", "as_of": previous_as_of or None,
                "fetched": collected["fetched"], "error": None}
    ready_end = ready[-1]
    if previous_as_of and ready_end <= previous_as_of:
        return {"status": "waiting_source", "as_of": previous_as_of,
                "fetched": collected["fetched"], "error": None,
                "reason": f"份额仅齐备至 {ready_end}，源尚未发布"}

    try:
        payload = compute(start, ready_end)
    except EmRateLimitedError as exc:
        return {"status": "rate_limited", "as_of": previous_as_of or None,
                "fetched": collected["fetched"], "error": str(exc)}
    except Exception as exc:  # noqa: BLE001
        return {"status": "failed", "as_of": previous_as_of or None,
                "fetched": collected["fetched"], "error": f"净流入重算失败：{exc}"}
    return {"status": "updated", "as_of": payload["as_of"],
            "previous_as_of": previous_as_of or None,
            "fetched": collected["fetched"],
            "last_day_yi": payload["stats"]["last_day_yi"], "error": None}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default="build",
                        choices=["build", "universe", "shares", "compute"])
    parser.add_argument("--start", default=HISTORY_START)
    parser.add_argument("--end", default=None)
    parser.add_argument("--base-day", default=None)
    args = parser.parse_args()

    end = args.end or last_completed_trading_day()[0]
    if args.target in ("build", "universe"):
        manifest = build_universe(args.base_day or end)
        print(f"[etf-share-flow] 宽基池固化 {len(manifest['universe'])} 只（基准日 {manifest['base_day']}）")
        if args.target == "universe":
            return
    else:
        manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))

    if args.target in ("build", "shares"):
        result = collect_shares(args.start, end, manifest)
        print(f"[etf-share-flow] 份额采集：新增 {result['fetched']} 日、跳过 {result['skipped']} 日")
        if args.target == "shares":
            return

    payload = compute(args.start, end)
    print(f"[etf-share-flow] 净流入序列：{payload['coverage']['trading_days']} 个交易日，"
          f"截至 {payload['as_of']}，昨日 {payload['stats']['last_day_yi']} 亿元")


if __name__ == "__main__":
    main()
