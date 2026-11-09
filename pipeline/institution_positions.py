"""中金所会员持仓排名 → 机构多空单序列与次日同向/对冲对照。

定位：**只做事后统计与展示**。次日行情与关系判定字段统一以 `next_` 前缀命名，
不得被任何顶部/底部信号、板块趋势或候选状态计算读取（AGENTS 第 1 条）。

口径：
- 数据源：akshare `get_cffex_rank_table`（中金所每日前 20 会员持仓排名），品种固定 IF/IC/IH/IM；
- 多单 = 会员持买单量之和，空单 = 会员持卖单量之和，净持仓 = 多 − 空（单位：手）；
- 会员名按 `MEMBER_ALIASES` **精确匹配**归一，未命中者计入"其他机构"；
  不可用子串匹配——实测同日同时存在「中信期货」与「中信建投」。
- 大盘对照：`market_lines` 里的上证指数收盘价**只用于图表展示叠加**，
  统计基准（交易日历、次日涨跌、同向率）始终只有沪深300。
"""
from __future__ import annotations

import argparse
import json
import re
import time
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from io_utils import protected_write

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW_CFFEX = DATA / "raw" / "cffex"
BENCHMARK_CSV = DATA / "raw" / "indices-ths" / "sh000300.csv"
MARKET_INDEX_CSV = DATA / "raw" / "indices-ths" / "sh000001.csv"
OUTPUT_NAME = "institution-positions.json"

CN = ZoneInfo("Asia/Shanghai")

ALGORITHM_VERSION = "institution-positions-cffex-top20-v1.1"
VARIETIES = ("IF", "IC", "IH", "IM")
VARIETY_LABELS = {"IF": "沪深300", "IC": "中证500", "IH": "上证50", "IM": "中证1000"}
VIEWS = ("all",) + VARIETIES
GROUPS = (("guotai", "国泰君安"), ("citic", "中信期货"), ("others", "其他机构"))

# 精确匹配：键为会员在排名表中的名称，值为规范名。新增主体时在此追加并留痕。
MEMBER_ALIASES = {
    "国泰君安": "国泰君安",
    "国泰君安期货": "国泰君安",
    "中信期货": "中信期货",
}
GROUP_OF = {"国泰君安": "guotai", "中信期货": "citic"}

MAX_RANK = 20
TOTAL_RANK = 999
WINDOW_YEARS = 3
RETRY_ATTEMPTS = 3
RETRY_BACKOFF = 1.0

SAME, HEDGE, PENDING, NO_DATA = "S", "H", "P", "N"

AGGREGATION_NOTE = (
    "四品种汇总为 IF/IC/IH/IM 各合约持仓手数直接相加，未按合约乘数折算为名义金额"
    "（IF/IH 300 元/点、IC/IM 200 元/点），不可用于跨品种规模比较。"
)
SCOPE_NOTE = (
    "数据为中金所每日前 20 会员持仓排名，非全市场持仓；多单/空单均为单边持仓量，单位为手。"
)
BENCHMARK = {"code": "sh000300", "name": "沪深300"}
# 展示用叠加指数：不参与任何统计口径
MARKET_INDEX = {"code": "sh000001", "name": "上证指数"}

COLUMNS = ["date", "variety", "contract", "rank",
           "long_party_name", "long_oi", "short_party_name", "short_oi"]
# 旧缓存格式（party_name 单列，多空混在同一人）已废弃，read_cached_day 检测到旧格式会触发重抓
LEGACY_COLUMNS = ["date", "variety", "contract", "rank", "party_name", "long_oi", "short_oi"]


def normalize_name(name) -> str:
    """全角空格与首尾空白归一，供精确匹配使用。"""
    return str(name).replace("\u3000", " ").strip()


# 中金所排名表会在会员名后追加持仓簿记后缀：实测 2026 年数据为「国泰君安(代客)」，
# 2024-02-26 至 2024-04-26 期间为「中信期货(经纪)」等"(经纪)"后缀。
# 归一前先剥离后缀，避免同一主体被拆成两条记录或落入其他机构。
BOOK_SUFFIX = re.compile(r"\((?:代客|自营|客户|非期货公司会员|经纪)\)$")


def base_name(name) -> str:
    return BOOK_SUFFIX.sub("", normalize_name(name)).strip()


def canonical_member(name) -> str | None:
    return MEMBER_ALIASES.get(base_name(name))


def group_of(name) -> str:
    canonical = canonical_member(name)
    return GROUP_OF.get(canonical, "others") if canonical else "others"


def load_benchmark() -> pd.DataFrame:
    """沪深300 日线：既是"次日走势"来源，也是交易日历来源。"""
    frame = pd.read_csv(BENCHMARK_CSV, usecols=["date", "close"])
    frame = frame.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    return frame


def load_market_index() -> pd.DataFrame:
    """上证指数日线收盘：只用于图表展示叠加，不参与任何统计口径。

    文件缺失时返回空表——展示序列缺失不得阻断机构产物构建。
    """
    if not MARKET_INDEX_CSV.exists():
        return pd.DataFrame(columns=["date", "close"])
    frame = pd.read_csv(MARKET_INDEX_CSV, usecols=["date", "close"])
    return frame.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def align_closes(frame: pd.DataFrame, dates: list[str]) -> list[float | None]:
    """按既有交易日历（沪深300）对齐收盘价：非交易日留 None，前端渲染为断点。"""
    closes_by_day = dict(zip(frame["date"], frame["close"]))
    return [closes_by_day.get(day) for day in dates]


def window_start(last_day: str, years: int = WINDOW_YEARS) -> str:
    end = date.fromisoformat(last_day)
    return end.replace(year=end.year - years).isoformat()


def trading_days(frame: pd.DataFrame, start: str) -> list[str]:
    return [day for day in frame["date"].tolist() if day >= start]


def empty_frame() -> pd.DataFrame:
    return pd.DataFrame(columns=COLUMNS)


def normalize_table(day: str, table) -> pd.DataFrame:
    """把 akshare 返回的 {合约: DataFrame} 合并为统一列；非交易日空返回得到空表。

    重要：akshare 返回的 long_party_name 和 short_party_name 是 **不同的排名**，
    同一 rank 号对应的多头会员和空头会员可能完全不同。
    必须分别保留两个 party_name 列，聚合时各归各。
    """
    rows: list[pd.DataFrame] = []
    if isinstance(table, dict):
        for contract, frame in table.items():
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            block = pd.DataFrame({
                "date": day,
                "variety": frame.get("variety", pd.Series(dtype=object)).astype(str),
                "contract": str(contract),
                "rank": pd.to_numeric(frame.get("rank"), errors="coerce"),
                "long_party_name": frame.get("long_party_name"),
                "long_oi": pd.to_numeric(frame.get("long_open_interest"), errors="coerce"),
                "short_party_name": frame.get("short_party_name"),
                "short_oi": pd.to_numeric(frame.get("short_open_interest"), errors="coerce"),
            })
            rows.append(block)
    if not rows:
        return empty_frame()
    merged = pd.concat(rows, ignore_index=True)
    merged = merged[merged["variety"].isin(VARIETIES)]
    merged = merged.dropna(subset=["rank", "long_oi", "short_oi"])
    merged["rank"] = merged["rank"].astype(int)
    merged["long_oi"] = merged["long_oi"].astype(int)
    merged["short_oi"] = merged["short_oi"].astype(int)
    return merged[COLUMNS].reset_index(drop=True)


def cache_path(day: str) -> Path:
    return RAW_CFFEX / f"{day}.csv"


def read_cached_day(day: str) -> pd.DataFrame | None:
    path = cache_path(day)
    if not path.exists():
        return None
    try:
        frame = pd.read_csv(path)
    except Exception:
        return None
    if frame.empty:
        return None
    # 检测旧缓存格式（只有 party_name，没有 long/short 分离）——视为无效，触发重抓
    if "long_party_name" not in frame.columns:
        return None
    if not set(COLUMNS).issubset(frame.columns):
        return None
    return frame[COLUMNS]


def write_cached_day(day: str, frame: pd.DataFrame) -> None:
    if frame.empty:
        return
    RAW_CFFEX.mkdir(parents=True, exist_ok=True)
    frame.to_csv(cache_path(day), index=False, encoding="utf-8-sig")


def default_fetcher(day: str):
    import akshare as ak

    return ak.get_cffex_rank_table(date=day.replace("-", ""), vars_list=list(VARIETIES))


def fetch_with_retry(day: str, fetcher, *, attempts: int = RETRY_ATTEMPTS, sleep=time.sleep):
    last_error: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            return normalize_table(day, fetcher(day))
        except Exception as exc:  # 网络接口：保留最后一次错误并退避重试
            last_error = exc
            if attempt < attempts:
                sleep(RETRY_BACKOFF * attempt)
    print(f"  抓取 {day} 失败：{last_error}")
    return None


def ensure_day(day: str, *, fetcher=None, force: bool = False) -> pd.DataFrame | None:
    """优先复用缓存；无缓存才请求远端。非交易日空返回不写缓存。"""
    if not force:
        cached = read_cached_day(day)
        if cached is not None:
            return cached
    frame = fetch_with_retry(day, fetcher or default_fetcher)
    if frame is None or frame.empty:
        return None
    write_cached_day(day, frame)
    return frame


def aggregate_day(frame: pd.DataFrame) -> dict:
    """按品种汇总三家分组，并用 rank=999 合计行做守恒校验。

    核心逻辑：多头按 long_party_name 归集，空头按 short_party_name 归集。
    同一 rank 号的多头会员和空头会员可能完全不同，不可混用。
    """
    ranked = frame[
        (frame["rank"] >= 1) & (frame["rank"] <= MAX_RANK)
        & frame["long_party_name"].notna() & frame["short_party_name"].notna()
    ].copy()
    totals = frame[frame["rank"] == TOTAL_RANK]

    result: dict[str, dict] = {"varieties": {}, "unmapped": set()}
    # 收集未映射的会员名（多头 + 空头）
    all_names = set(ranked["long_party_name"].unique()) | set(ranked["short_party_name"].unique())
    result["unmapped"] = {
        base_name(name) for name in all_names
        if canonical_member(name) is None
    }

    for variety in VARIETIES:
        block = ranked[ranked["variety"] == variety]
        if block.empty:
            continue
        # 多头：按 long_party_name 归集
        long_per_member = block.groupby("long_party_name", as_index=False)["long_oi"].sum()
        long_per_member["group"] = long_per_member["long_party_name"].map(group_of)
        long_by_group = long_per_member.groupby("group")["long_oi"].sum()
        # 空头：按 short_party_name 归集
        short_per_member = block.groupby("short_party_name", as_index=False)["short_oi"].sum()
        short_per_member["group"] = short_per_member["short_party_name"].map(group_of)
        short_by_group = short_per_member.groupby("group")["short_oi"].sum()

        groups = {
            gid: {
                "long": int(long_by_group[gid]) if gid in long_by_group.index else 0,
                "short": int(short_by_group[gid]) if gid in short_by_group.index else 0,
            }
            for gid, _ in GROUPS
        }
        total_row = totals[totals["variety"] == variety]
        expected_long = int(total_row["long_oi"].sum())
        expected_short = int(total_row["short_oi"].sum())
        actual_long = sum(groups[gid]["long"] for gid, _ in GROUPS)
        actual_short = sum(groups[gid]["short"] for gid, _ in GROUPS)
        if (actual_long, actual_short) != (expected_long, expected_short):
            raise ValueError(
                f"{variety} 分组守恒校验失败：多单 {actual_long}/{expected_long}，空单 {actual_short}/{expected_short}"
            )
        result["varieties"][variety] = {
            "groups": groups,
            "total_long": expected_long,
            "total_short": expected_short,
        }
    return result


def day_views(aggregated: dict) -> dict[str, dict[str, dict]]:
    """展开为 {view: {group: {long, short}}}；`all` 为各品种手数直接相加。"""
    views: dict[str, dict[str, dict]] = {view: {gid: {"long": 0, "short": 0} for gid, _ in GROUPS} for view in VIEWS}
    for variety, payload in aggregated["varieties"].items():
        for gid, _ in GROUPS:
            values = payload["groups"][gid]
            views[variety][gid] = dict(values)
            views["all"][gid]["long"] += values["long"]
            views["all"][gid]["short"] += values["short"]
    return views


def classify(net_change, next_ret) -> str:
    if net_change is None or next_ret is None or net_change == 0:
        return PENDING
    if next_ret == 0:
        return PENDING
    return SAME if (net_change > 0) == (next_ret > 0) else HEDGE


def build_series(dates: list[str], day_map: dict[str, dict], closes: list[float | None]) -> dict:
    next_returns: list[float | None] = []
    for index in range(len(dates)):
        if index + 1 < len(dates) and closes[index] and closes[index + 1]:
            next_returns.append(closes[index + 1] / closes[index] - 1)
        else:
            next_returns.append(None)

    series: dict[str, dict] = {}
    for gid, _ in GROUPS:
        series[gid] = {}
        for view in VIEWS:
            long_arr: list[int | None] = []
            short_arr: list[int | None] = []
            net_arr: list[int | None] = []
            chg_arr: list[int | None] = []
            rel_arr: list[str] = []
            for day in dates:
                views = day_map.get(day)
                values = views.get(view, {}).get(gid) if views else None
                if values is None:
                    long_arr.append(None)
                    short_arr.append(None)
                    net_arr.append(None)
                    continue
                long_arr.append(values["long"])
                short_arr.append(values["short"])
                net_arr.append(values["long"] - values["short"])
            for index in range(len(dates)):
                current = net_arr[index]
                previous = net_arr[index - 1] if index > 0 else None
                chg = None if current is None or previous is None else current - previous
                chg_arr.append(chg)
                rel_arr.append(NO_DATA if current is None else classify(chg, next_returns[index]))
            series[gid][view] = {
                "long": long_arr,
                "short": short_arr,
                "net": net_arr,
                "chg": chg_arr,
                "next_ret": next_returns,
                "relation": rel_arr,
            }
    return series


def summarize(dates: list[str], series: dict) -> dict:
    stats: dict[str, dict] = {}
    for gid, _ in GROUPS:
        stats[gid] = {}
        for view in VIEWS:
            relation = series[gid][view]["relation"]
            same = relation.count(SAME)
            hedge = relation.count(HEDGE)
            pending = relation.count(PENDING)
            no_data = relation.count(NO_DATA)
            decided = same + hedge
            decided_days = [day for day, code in zip(dates, relation) if code in (SAME, HEDGE)]
            stats[gid][view] = {
                "rate": (same / decided) if decided else None,
                "rate_unit": "fraction",
                "decided": decided,
                "same": same,
                "hedge": hedge,
                "pending": pending,
                "no_data": no_data,
                "small_sample": decided < 30,
                "start": decided_days[0] if decided_days else None,
                "end": decided_days[-1] if decided_days else None,
            }
    return stats


def build_payload(*, limit: int | None = None, fetcher=None, force: bool = False,
                  benchmark: pd.DataFrame | None = None,
                  market_index: pd.DataFrame | None = None) -> dict:
    bench = benchmark if benchmark is not None else load_benchmark()
    if bench.empty:
        raise RuntimeError("缺少沪深300 日线，无法确定交易日历")
    last_day = bench["date"].iloc[-1]
    start = window_start(last_day)
    dates = trading_days(bench, start)
    if limit:
        dates = dates[-limit:]

    closes_by_day = dict(zip(bench["date"], bench["close"]))
    closes = [closes_by_day.get(day) for day in dates]

    # 展示叠加序列：与 benchmark 物理隔离，统计计算一律不读它
    index_frame = market_index if market_index is not None else load_market_index()
    index_closes = align_closes(index_frame, dates)
    market_lines = [{**BENCHMARK, "close": closes}]
    if any(value is not None for value in index_closes):
        market_lines.insert(0, {**MARKET_INDEX, "close": index_closes})

    day_map: dict[str, dict] = {}
    missing: list[str] = []
    unmapped: set[str] = set()
    conservation = 0
    for index, day in enumerate(dates, start=1):
        frame = ensure_day(day, fetcher=fetcher, force=force)
        if frame is None or frame.empty:
            missing.append(day)
            continue
        aggregated = aggregate_day(frame)
        conservation += len(aggregated["varieties"])
        unmapped |= aggregated["unmapped"]
        day_map[day] = day_views(aggregated)
        if index % 100 == 0:
            print(f"  已处理 {index}/{len(dates)} 个交易日")

    series = build_series(dates, day_map, closes)
    stats = summarize(dates, series)
    as_of = max(day_map) if day_map else None

    return {
        "as_of": as_of,
        "generated_at": datetime.now(CN).isoformat(timespec="seconds"),
        "algorithm_version": ALGORITHM_VERSION,
        "window": {"start": dates[0] if dates else None, "end": dates[-1] if dates else None,
                   "years": WINDOW_YEARS},
        "varieties": [{"code": code, "name": VARIETY_LABELS[code]} for code in VARIETIES],
        "institutions": [
            {"id": gid, "name": name,
             "note": ("前 20 会员中该主体的全部持仓" if gid != "others"
                      else "前 20 会员中除国泰君安、中信期货外的合计")}
            for gid, name in GROUPS
        ],
        "aggregation_note": AGGREGATION_NOTE,
        "scope_note": SCOPE_NOTE,
        "benchmark": {**BENCHMARK, "close": closes},
        "market_lines": market_lines,
        "dates": dates,
        "series": series,
        "stats": stats,
        "quality": {
            "missing_dates": missing,
            "unmatched_members": sorted(unmapped),
            "conservation_checked": conservation,
            "source": "akshare.get_cffex_rank_table",
            "cached_days": len(day_map),
            "trading_days": len(dates),
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="构建机构多空单产物（中金所前 20 会员持仓排名）")
    parser.add_argument("--limit", type=int, default=None, help="仅构建最近 N 个交易日（调试用）")
    parser.add_argument("--force", action="store_true", help="忽略缓存重新抓取")
    parser.add_argument("--dry-run", action="store_true", help="只构建不落盘")
    args = parser.parse_args()

    started = time.time()
    payload = build_payload(limit=args.limit, force=args.force)
    elapsed = time.time() - started
    print(f"构建完成：{payload['quality']['cached_days']}/{payload['quality']['trading_days']} 个交易日，"
          f"缺失 {len(payload['quality']['missing_dates'])} 个，耗时 {elapsed:.1f}s")
    if args.dry_run:
        print(json.dumps({gid: payload["stats"][gid]["all"] for gid, _ in GROUPS}, ensure_ascii=False, indent=2))
        return
    protected_write(OUTPUT_NAME, payload, compact_arrays=True)


if __name__ == "__main__":
    main()
