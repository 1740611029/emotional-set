"""宽基 ETF 申购净流入（近 6 年折线）：逐日动态建池 + 份额变化 × 单位净值。

与既有 `fetch_etf_share_flow.py`（【ETF 放量】页第一张柱状图，2025 年起、单日固化池）
完全隔离：独立产物 `data/broad-inflow-6y.json`、独立缓存目录、独立刷新入口。
既有脚本与其产物一行不改，两张图在页面上并存、各自标注口径。

口径（与既有图的关键差异，必须如实标注）：

- 宽基池**逐日动态**：每个交易日用当日交易所份额快照的「基金简称」判定，
  池子随市场自然扩张（2020-11 约 27 只 → 2026-11 约 130 只），不固化为单日基准池。
  若沿用单日固化池回算，2020 年只剩 14 只可参与、漏掉约 44%，早期总额会被系统性低估。
- 池筛选比既有图更严格：在既有 `is_broad` 白名单之上，额外排除跨境（美国/德国/日本/亚太/
  全球等）与策略风格产品（增强/成长/价值/红利/低波/等权/龙头/碳中和/民企/央视等）。
  既有图靠 5 亿规模门槛过滤这类小产品，本图按用户口径不使用规模门槛，只能靠名称排除。
- 净流入(T) = Σ_i [(份额_i(T) − 份额_i(T−1)) × 单位净值_i(T)] / 1e8（亿元），T−1 为该 ETF 上一个
  有份额记录的交易日；上市首日无前值跳过，不补零。
- 无未来函数：只用 T 与 T−1 已发布数据。
- 份额拆分/合并拦截：两种判定任一条命中即跳过该 ETF 当日贡献并重置前值——
  ① 份额单日变动 ≥2 倍或 ≤1/2；
  ② 份额明显变动（|ratio−1|>0.15）且净值反向补偿（|ratio×净值比−1|<0.15），
     典型如 510310 在 2024-09-20 份额 0.5116 倍、净值 2.004 倍。
  否则「份额×N、净值÷N」会被算成几十亿的假流入。
- **数据完整门禁**：某交易日沪深两侧份额快照缺任一侧（交易所接口对个别历史日期不返回数据），
  该日不输出，避免只剩单侧参与而写出假的低值；缺失日计入 `anomalies.incomplete_days`。
- 累计净流入：从区间首个交易日起逐日累加；池子扩张会让后段斜率天然更陡，
  不能直接读作「资金加速流入」，页面须标注。

数据源：

- 沪市份额 `fund_etf_scale_sse(date)`（逐日快照，含简称）
- 深市份额 `fund_scale_daily_szse(区间≤6个月)`（含简称）
- 单位净值主源 `fund_etf_spot_ths(date)`（同花顺逐日全 ETF 快照，历史取值已与东财逐字核对一致；
  未上市标的返回 NaN 而非假值，不会污染金额）
- 净值补丁 `fund_exchange_nav_hist_em(fund, start, end)`（东财逐只，只用于同花顺快照完全查不到的
  清盘/退市标的；该接口按页翻取 6 年历史，单只约 30 秒，故仅作补丁，不做主源）
- `data/raw/etf-share-flow/nav/*.csv` 仅作**只读**复用（2025 年起 460 天），绝不写入
- 上证指数 `data/raw/indices-ths/sh000001.csv`

份额缓存**存全市场**（不按宽基池裁剪），这样任何口径调整都能离线重算，不必重发请求。
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import akshare as ak
import pandas as pd

sys_path = str(Path(__file__).resolve().parent)
if sys_path not in sys.path:
    sys.path.insert(0, sys_path)

from fetch_etf_share_flow import is_broad  # noqa: E402  复用同一白名单，避免两套口径漂移
from fetch_market import EmRateLimitedError, call_with_retry  # noqa: E402
from rate_limiter import LIMITER, REQUEST_GAP  # noqa: E402
from trading_calendar import last_completed_trading_day  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
BASE = RAW / "broad-inflow-6y"
SHARES = BASE / "shares"
NAV_DAILY = BASE / "nav-daily"
NAV_EM = BASE / "nav-em"
OUTPUT = ROOT / "data" / "broad-inflow-6y.json"
INDEX_CSV = RAW / "indices-ths" / "sh000001.csv"
# 既有数据线的净值逐日快照：只读复用兜底，绝不写入（隔离要求）
LEGACY_NAV = RAW / "etf-share-flow" / "nav"

# 近 6 年起点（2026-11-27 回推 6 年）；采集会向前预热若干交易日，保证首日能取到前值
HISTORY_START = "2020-11-27"
WARMUP_DAYS = 10
# 份额单日变动达到 2 倍或腰斩即判为拆分/合并
SPLIT_MULTIPLE = 2.0
# 份额与净值反向补偿判定：份额明显变动且「份额比×净值比」≈1，视为份额折算
SPLIT_DEVIATION = 0.15
SPLIT_COMPENSATION = 0.15
# 东财净值补丁单轮上限：该接口按页翻取全历史，单只约 30 秒，只补清盘等个别标的
MAX_EM_PATCH = 20

# 在既有宽基白名单之上额外排除：跨境 / 策略风格 / 主题叠加（本图无规模门槛，只能靠名称收口）
STRICT_EXCLUDE_KEYWORDS = (
    # 跨境
    "美国",
    "德国",
    "法国",
    "英国",
    "日本",
    "日经",
    "亚太",
    "东南亚",
    "沙特",
    "印度",
    "越南",
    "全球",
    "国际",
    "QDII",
    "海外",
    "港股",
    "恒生",
    "中概",
    "纳斯达克",
    "标普",
    # 策略 / 风格 / 因子
    "增强",
    "增",
    "成长",
    "价值",
    "红利",
    "红低",
    "低波",
    "等权",
    "质量",
    "动量",
    "基本面",
    "龙头",
    "策略",
    "多因子",
    "ESG",
    "碳中和",
    "碳达峰",
    # 主题与行业叠加（白名单里的 50/300 等易被主题产品借用）
    "民企",
    "央视",
    "国企",
    "央企",
    "科技",
    "创新药",
    "机器人",
    "人工智能",
)

SHARE_SOURCE = "上交所ETF份额(dated快照)+深交所ETF份额(日频区间)，存全市场份额"
NAV_SOURCE = "同花顺ETF每日单位净值快照(fund_etf_spot_ths, date参数)为主；东财逐只历史净值补清盘标的"
POOL_SOURCE = "逐日按当日基金简称 + 宽基白名单（并额外排除跨境/策略产品）动态判定（point-in-time）"

NAV_EM_START = "20191201"  # 东财补丁起点：覆盖预热窗口


def is_broad_strict(name: str) -> bool:
    """本图专用池判定：既有的宽基白名单 + 更严格的排除词。不修改既有 `is_broad`。"""
    text = str(name)
    if any(word in text for word in STRICT_EXCLUDE_KEYWORDS):
        return False
    return is_broad(text)


def _index_calendar() -> list[str]:
    frame = pd.read_csv(INDEX_CSV, dtype={"date": str})
    return sorted(str(d) for d in frame["date"].tolist())


def _sleep() -> None:
    import time

    time.sleep(REQUEST_GAP)


# ---------------- 份额 ----------------


def _share_path(exchange: str, day: str) -> Path:
    return SHARES / exchange / f"{day}.csv"


def _share_ready(exchange: str, day: str) -> bool:
    """份额缓存是否存在且有内容；只有表头的空文件不算齐备。"""
    path = _share_path(exchange, day)
    if not path.exists():
        return False
    return path.stat().st_size > len("code,name,share\n")


def _read_share_day(exchange: str, day: str) -> dict[str, dict]:
    """某日某市场全市场份额 {代码: {name, share}}；无缓存返回空。"""
    path = _share_path(exchange, day)
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, dtype={"code": str})
    except Exception:  # noqa: BLE001  # 半截文件按缺失处理，下轮重取
        return {}
    result: dict[str, dict] = {}
    for code, name, share in zip(
        frame.get("code"), frame.get("name"), frame.get("share")
    ):
        if pd.isna(share):
            continue
        result[str(code)] = {
            "name": "" if pd.isna(name) else str(name),
            "share": float(share),
        }
    return result


def _write_share_day(exchange: str, day: str, rows: list[dict]) -> None:
    """只写有内容的快照；空结果不落盘，避免「空文件被当成已采集」而永久缺数。"""
    if not rows:
        return
    path = _share_path(exchange, day)
    path.parent.mkdir(parents=True, exist_ok=True)
    pd.DataFrame(rows, columns=["code", "name", "share"]).to_csv(path, index=False)


def _fetch_sse_day(day: str) -> list[dict]:
    if not LIMITER.acquire("sse", line="broad-inflow-6y-sse"):
        raise EmRateLimitedError("sse 冷却中，跳过 ETF 份额采集")
    frame = call_with_retry(ak.fund_etf_scale_sse, date=day.replace("-", ""))
    if frame is None or frame.empty:
        raise ValueError(f"上交所 {day} 份额快照为空")
    rows: list[dict] = []
    for code, name, share in zip(
        frame["基金代码"], frame["基金简称"], frame["基金份额"]
    ):
        if pd.isna(share):
            continue
        rows.append(
            {
                "code": str(code),
                "name": "" if pd.isna(name) else str(name),
                "share": float(share),
            }
        )
    return rows


def _fetch_szse_range(start: str, end: str) -> dict[str, list[dict]]:
    if not LIMITER.acquire("szse", line="broad-inflow-6y-szse"):
        raise EmRateLimitedError("szse 冷却中，跳过 ETF 份额采集")
    frame = call_with_retry(
        ak.fund_scale_daily_szse,
        start_date=start.replace("-", ""),
        end_date=end.replace("-", ""),
        symbol="ETF",
    )
    if frame is None or frame.empty:
        raise ValueError(f"深交所 {start}~{end} 份额区间为空")
    blocks: dict[str, list[dict]] = {}
    for raw_date, code, name, share in zip(
        frame["日期"], frame["基金代码"], frame["基金简称"], frame["基金份额"]
    ):
        if pd.isna(share):
            continue
        day = pd.Timestamp(raw_date).strftime("%Y-%m-%d")
        blocks.setdefault(day, []).append(
            {
                "code": str(code),
                "name": "" if pd.isna(name) else str(name),
                "share": float(share),
            }
        )
    return blocks


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


def collect_shares(start: str, end: str) -> dict:
    """按交易日采集沪/深全市场份额到 CSV；已齐备跳过，天然断点续传。"""
    calendar = [d for d in _index_calendar() if start <= d <= end]
    fetched, skipped, failed = 0, 0, 0

    missing_szse = [d for d in calendar if not _share_ready("szse", d)]
    for window_start, window_end in _six_month_windows(missing_szse):
        try:
            blocks = _fetch_szse_range(window_start, window_end)
        except EmRateLimitedError as exc:
            print(f"[broad-inflow-6y] 深市限流，停止本轮：{exc}", flush=True)
            break
        except Exception as exc:  # noqa: BLE001  # 单窗口失败不阻断整轮
            print(
                f"[broad-inflow-6y] 深市 {window_start}~{window_end} 失败：{exc}",
                flush=True,
            )
            failed += 1
            continue
        got = 0
        for day, rows in blocks.items():
            if _share_ready("szse", day):
                continue
            _write_share_day("szse", day, rows)
            got += 1
        fetched += got
        if got == 0:
            # 交易所对该窗口无返回：源无数据，记录后继续，不重复空转
            print(
                f"[broad-inflow-6y] 深市 {window_start}~{window_end} 源无数据",
                flush=True,
            )
        _sleep()

    for day in calendar:
        if _share_ready("sse", day):
            skipped += 1
            continue
        try:
            rows = _fetch_sse_day(day)
        except EmRateLimitedError as exc:
            print(
                f"[broad-inflow-6y] 沪市限流，停止本轮（已取 {fetched} 日）：{exc}",
                flush=True,
            )
            break
        except Exception as exc:  # noqa: BLE001
            print(f"[broad-inflow-6y] 沪市 {day} 失败：{exc}", flush=True)
            failed += 1
            continue
        _write_share_day("sse", day, rows)
        fetched += 1
        _sleep()
    return {
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "trading_days": len(calendar),
    }


def _candidate_codes() -> dict[str, str]:
    """所有出现过在份额快照里的宽基代码（{code: 最近一次简称}），用于净值覆盖核对。"""
    codes: dict[str, str] = {}
    for exchange in ("sse", "szse"):
        directory = SHARES / exchange
        if not directory.exists():
            continue
        for path in sorted(directory.glob("*.csv")):
            for code, item in _read_share_day(exchange, path.stem).items():
                if is_broad_strict(item["name"]):
                    codes[code] = item["name"]
    return codes


# ---------------- 净值 ----------------


def _nav_day_path(day: str) -> Path:
    return NAV_DAILY / f"{day}.csv"


def _load_nav_day(day: str) -> dict[str, float]:
    """某交易日全 ETF 单位净值 {代码: 净值}；自有缓存优先，其次只读复用既有数据线快照。"""
    for path in (_nav_day_path(day), LEGACY_NAV / f"{day}.csv"):
        if not path.exists():
            continue
        try:
            frame = pd.read_csv(path, dtype={"code": str})
        except Exception:  # noqa: BLE001
            continue
        result = {
            str(c): float(v) for c, v in zip(frame["code"], frame["nav"]) if pd.notna(v)
        }
        if result:
            return result
    return {}


def sync_nav_daily(start: str, end: str) -> dict:
    """逐交易日拉同花顺全 ETF 单位净值快照并落缓存；已缓存跳过，天然断点续传。"""
    NAV_DAILY.mkdir(parents=True, exist_ok=True)
    calendar = [d for d in _index_calendar() if start <= d <= end]
    fetched, skipped, failed = 0, 0, 0
    for day in calendar:
        if _nav_day_path(day).exists() or (LEGACY_NAV / f"{day}.csv").exists():
            skipped += 1
            continue
        if not LIMITER.acquire("ths", line="broad-inflow-6y-nav"):
            raise EmRateLimitedError("ths 冷却中，跳过 ETF 单位净值采集")
        try:
            frame = call_with_retry(ak.fund_etf_spot_ths, date=day.replace("-", ""))
        except Exception as exc:  # noqa: BLE001  # 单日失败不阻断整轮，下轮续传
            print(f"[broad-inflow-6y] 净值 {day} 失败：{exc}", flush=True)
            failed += 1
            continue
        rows = [
            {"code": str(code), "nav": float(value)}
            for code, value in zip(frame["基金代码"], frame["当前-单位净值"])
            if pd.notna(value)
        ]
        if not rows:
            failed += 1
            continue
        pd.DataFrame(rows, columns=["code", "nav"]).to_csv(
            _nav_day_path(day), index=False
        )
        fetched += 1
        _sleep()
    return {
        "fetched": fetched,
        "skipped": skipped,
        "failed": failed,
        "trading_days": len(calendar),
    }


def _nav_seen_codes(start: str, end: str) -> set[str]:
    """同花顺快照里出现过的代码集合，用于判定哪些标的需要东财补丁。"""
    seen: set[str] = set()
    for day in _index_calendar():
        if not start <= day <= end:
            continue
        seen.update(_load_nav_day(day).keys())
    return seen


def sync_nav_em(codes: list[str], end: str) -> dict:
    """东财逐只补丁：只对同花顺快照完全查不到的清盘/退市标的，单轮上限 MAX_EM_PATCH。"""
    NAV_EM.mkdir(parents=True, exist_ok=True)
    fetched, failed = 0, 0
    pending = [code for code in codes if not (NAV_EM / f"{code}.csv").exists()][
        :MAX_EM_PATCH
    ]
    for code in pending:
        try:
            frame = call_with_retry(
                ak.fund_exchange_nav_hist_em,
                fund=code,
                start_date=NAV_EM_START,
                end_date=end.replace("-", ""),
            )
        except Exception as exc:  # noqa: BLE001  # 补丁失败不影响主序列
            failed += 1
            print(f"[broad-inflow-6y] 东财补丁 {code} 失败：{exc}", flush=True)
            continue
        if frame is None or frame.empty or "单位净值" not in frame.columns:
            failed += 1
            continue
        rows = [
            {"date": pd.Timestamp(d).strftime("%Y-%m-%d"), "nav": float(v)}
            for d, v in zip(frame["净值日期"], frame["单位净值"])
            if pd.notna(v) and float(v) > 0
        ]
        if not rows:
            failed += 1
            continue
        pd.DataFrame(rows, columns=["date", "nav"]).to_csv(
            NAV_EM / f"{code}.csv", index=False
        )
        fetched += 1
        _sleep()
    return {"fetched": fetched, "failed": failed, "pending": len(pending)}


def _load_nav_em(code: str) -> dict[str, float]:
    path = NAV_EM / f"{code}.csv"
    if not path.exists():
        return {}
    try:
        frame = pd.read_csv(path, dtype={"date": str})
    except Exception:  # noqa: BLE001
        return {}
    return {
        str(d): float(v) for d, v in zip(frame["date"], frame["nav"]) if pd.notna(v)
    }


# ---------------- 计算 ----------------


def compute(start: str = HISTORY_START, end: str | None = None) -> dict:
    """逐日动态建池并计算日频/累计净流入，落独立产物。"""
    from server.snapshot import now_iso  # noqa: PLC0415  # sys.path 已配

    end = end or last_completed_trading_day()[0]
    codes = _candidate_codes()
    if not codes:
        raise ValueError("份额缓存中没有任何宽基 ETF，无法计算")

    em_nav: dict[str, dict[str, float]] = {}

    calendar_all = _index_calendar()
    earliest = min(
        (d for d in calendar_all if _share_ready("sse", d) or _share_ready("szse", d)),
        default=None,
    )
    if earliest is None:
        raise ValueError("份额缓存为空，无法计算")
    # 从最早缓存日起滚动，保证区间首日能取到 T−1 前值；输出只保留 [start, end]
    calendar = [d for d in calendar_all if earliest <= d <= end]

    nav_by_day: dict[str, dict[str, float]] = {}
    last_share: dict[str, tuple[str, float]] = {}
    last_nav: dict[str, float] = {}
    dates: list[str] = []
    daily_yi: list[float] = []
    pool_sizes: list[int] = []
    counted_sizes: list[int] = []
    split_skipped = 0
    nav_missing = 0
    incomplete_days: list[str] = []
    # 上一次「有效日」沪深两侧各自的宽基只数，用于识别接口只返回部分标的的日子
    prev_side_pool: dict[str, int] = {"sse": 0, "szse": 0}

    for position, day in enumerate(calendar):
        sse = _read_share_day("sse", day)
        szse = _read_share_day("szse", day)
        # 数据完整门禁：任一侧缺失、或某侧宽基只数较上次有效日骤降至 60% 以下
        # （交易所接口对个别日期只返回部分标的，如 2025-08-11 深市仅 10 只宽基），
        # 该日不输出，避免只有单侧/部分标的参与而写出假的低值
        side_pools = {
            side: sum(1 for item in rows.values() if is_broad_strict(item["name"]))
            for side, rows in (("sse", sse), ("szse", szse))
        }
        incomplete = any(
            count == 0
            or (prev_side_pool[side] >= 10 and count < prev_side_pool[side] * 0.6)
            for side, count in side_pools.items()
        )
        if incomplete:
            if start <= day <= end:
                incomplete_days.append(day)
            continue
        prev_side_pool = side_pools
        current: dict[str, dict] = {**sse, **szse}
        if day not in nav_by_day:
            nav_by_day[day] = _load_nav_day(day)
        day_nav = nav_by_day[day]
        prev_day = calendar[position - 1] if position > 0 else None
        total = 0.0
        counted = 0
        pool = 0
        for code, item in current.items():
            name = item["name"] or codes.get(code, "")
            if not is_broad_strict(name):
                continue
            pool += 1
            nav_value = day_nav.get(code)
            if nav_value is None or nav_value <= 0:
                if code not in em_nav:
                    em_nav[code] = _load_nav_em(code)
                nav_value = em_nav[code].get(day)
            if nav_value is None or nav_value <= 0:
                nav_missing += 1
                last_share[code] = (day, item["share"])
                continue
            share = item["share"]
            previous = last_share.get(code)
            prev_nav = last_nav.get(code)
            if previous is not None and previous[0] == prev_day and previous[1] > 0:
                ratio = share / previous[1]
                nav_ratio = (nav_value / prev_nav) if prev_nav else 1.0
                # ① 份额倍数折半/翻倍 ② 份额与净值反向补偿 —— 任一命中按份额折算处理，不计当日
                is_split = (
                    ratio >= SPLIT_MULTIPLE
                    or ratio <= 1.0 / SPLIT_MULTIPLE
                    or (
                        abs(ratio - 1.0) > SPLIT_DEVIATION
                        and abs(ratio * nav_ratio - 1.0) < SPLIT_COMPENSATION
                    )
                )
                if is_split:
                    split_skipped += 1  # 折算日不计，前值随后重置
                else:
                    total += (share - previous[1]) * nav_value
                    counted += 1
            last_share[code] = (day, share)
            last_nav[code] = nav_value
        if start <= day <= end:
            dates.append(day)
            daily_yi.append(round(total / 1e8, 2))
            pool_sizes.append(pool)
            counted_sizes.append(counted)

    if not dates:
        raise ValueError(f"{start}~{end} 区间内没有可计算的交易日")

    cumulative = 0.0
    cum_yi: list[float] = []
    for value in daily_yi:
        cumulative += value
        cum_yi.append(round(cumulative, 2))

    index_frame = pd.read_csv(INDEX_CSV, dtype={"date": str})
    closes = {
        str(d): (float(c) if pd.notna(c) else None)
        for d, c in zip(index_frame["date"], index_frame["close"])
    }
    index_close = [closes.get(day) for day in dates]

    recent60 = daily_yi[-60:]
    payload = {
        "data_state": "ready",
        "as_of": dates[-1],
        "generated_at": now_iso(),
        "coverage": {
            "start": dates[0],
            "end": dates[-1],
            "trading_days": len(dates),
            "codes_seen": len(codes),
            "min_pool": min(pool_sizes) if pool_sizes else 0,
            "max_pool": max(pool_sizes) if pool_sizes else 0,
            "min_counted": min(counted_sizes) if counted_sizes else 0,
            "max_counted": max(counted_sizes) if counted_sizes else 0,
        },
        "dates": dates,
        "daily_inflow_yi": daily_yi,
        "cum_inflow_yi": cum_yi,
        "index_close": index_close,
        "etf_count": pool_sizes,
        "stats": {
            "cum_yi": cum_yi[-1],
            "last_day_yi": daily_yi[-1],
            "last_60d_yi": round(sum(recent60), 2),
            "last_60d_days": len(recent60),
        },
        "anomalies": {
            "split_skipped": split_skipped,
            "nav_missing": nav_missing,
            "incomplete_days": len(incomplete_days),
        },
        "sources": {"share": SHARE_SOURCE, "nav": NAV_SOURCE, "pool": POOL_SOURCE},
    }
    OUTPUT.write_text(
        json.dumps(payload, ensure_ascii=False, separators=(",", ":")), encoding="utf-8"
    )
    return payload


def failed(reason: str) -> dict:
    """降级产物：字段齐全、数值空数组，前端不猜测。"""
    from server.snapshot import now_iso  # noqa: PLC0415

    return {
        "data_state": "preparing",
        "as_of": None,
        "generated_at": now_iso(),
        "coverage": {
            "start": None,
            "end": None,
            "trading_days": 0,
            "codes_seen": 0,
            "min_pool": 0,
            "max_pool": 0,
            "min_counted": 0,
            "max_counted": 0,
        },
        "dates": [],
        "daily_inflow_yi": [],
        "cum_inflow_yi": [],
        "index_close": [],
        "etf_count": [],
        "stats": {
            "cum_yi": 0.0,
            "last_day_yi": None,
            "last_60d_yi": 0.0,
            "last_60d_days": 0,
        },
        "anomalies": {"split_skipped": 0, "nav_missing": 0, "incomplete_days": 0},
        "sources": {},
        "stale": True,
        "stale_reason": reason,
    }


def update_broad_inflow_6y() -> dict:
    """增量刷新入口，供 bootstrap / 调度调用。

    与既有 `update_share_flow` 一样只返回状态、不把异常抛给调度层：
    未初始化 → uninitialized；限流 → rate_limited；源未发布 → waiting_source。
    """
    if not OUTPUT.exists():
        return {
            "status": "uninitialized",
            "as_of": None,
            "error": None,
            "reason": "6 年净流入产物缺失，需先显式建库"
            "（python -m pipeline.fetch_broad_inflow_6y build）",
        }
    try:
        previous = json.loads(OUTPUT.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        return {"status": "failed", "as_of": None, "error": f"产物读取失败：{exc}"}
    previous_as_of = str(previous.get("as_of") or "")

    try:
        end = last_completed_trading_day()[0]
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "as_of": previous_as_of or None,
            "error": f"交易日判定失败：{exc}",
        }
    if previous_as_of and end <= previous_as_of:
        return {"status": "skipped", "as_of": previous_as_of, "error": None}

    try:
        collected = collect_shares(HISTORY_START, end)
        nav = sync_nav_daily(HISTORY_START, end)
        codes = _candidate_codes()
        unseen = [c for c in codes if c not in _nav_seen_codes(HISTORY_START, end)]
        patch = sync_nav_em(unseen, end)
    except EmRateLimitedError as exc:
        return {
            "status": "rate_limited",
            "as_of": previous_as_of or None,
            "error": str(exc),
        }
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "as_of": previous_as_of or None,
            "error": f"份额/净值采集失败：{exc}",
        }

    try:
        payload = compute(HISTORY_START, end)
    except Exception as exc:  # noqa: BLE001
        return {
            "status": "failed",
            "as_of": previous_as_of or None,
            "error": f"6 年净流注重算失败：{exc}",
        }
    return {
        "status": "updated",
        "as_of": payload["as_of"],
        "previous_as_of": previous_as_of or None,
        "fetched": collected["fetched"],
        "nav_fetched": nav["fetched"],
        "em_patched": patch["fetched"],
        "last_day_yi": payload["stats"]["last_day_yi"],
        "error": None,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "target",
        nargs="?",
        default="build",
        choices=["build", "shares", "nav", "nav-em", "compute"],
    )
    parser.add_argument("--start", default=HISTORY_START)
    parser.add_argument("--end", default=None)
    args = parser.parse_args()

    end = args.end or last_completed_trading_day()[0]
    if args.target in ("build", "shares"):
        warm_start = (
            pd.Timestamp(args.start) - pd.Timedelta(days=WARMUP_DAYS)
        ).strftime("%Y-%m-%d")
        result = collect_shares(warm_start, end)
        print(
            f"[broad-inflow-6y] 份额采集：新增 {result['fetched']} 日、"
            f"跳过 {result['skipped']} 日、失败 {result['failed']} 次"
            f"（区间 {warm_start}~{end}，共 {result['trading_days']} 个交易日）",
            flush=True,
        )
        if args.target == "shares":
            return

    if args.target in ("build", "nav"):
        warm_start = (
            pd.Timestamp(args.start) - pd.Timedelta(days=WARMUP_DAYS)
        ).strftime("%Y-%m-%d")
        result = sync_nav_daily(warm_start, end)
        print(
            f"[broad-inflow-6y] 净值快照：新增 {result['fetched']} 日、"
            f"跳过 {result['skipped']} 日、失败 {result['failed']} 次",
            flush=True,
        )
        if args.target == "nav":
            return

    if args.target == "nav-em":
        codes = _candidate_codes()
        unseen = [c for c in codes if c not in _nav_seen_codes(args.start, end)]
        result = sync_nav_em(unseen, end)
        print(
            f"[broad-inflow-6y] 东财补丁：新增 {result['fetched']} 只、失败 {result['failed']} 只"
            f"（同花顺查不到的代码 {len(unseen)} 个，本轮尝试 {result['pending']} 个）",
            flush=True,
        )
        return

    payload = compute(args.start, end)
    print(
        f"[broad-inflow-6y] 6 年净流入：{payload['coverage']['trading_days']} 个交易日，"
        f"{payload['coverage']['start']}~{payload['coverage']['end']}，"
        f"累计 {payload['stats']['cum_yi']} 亿元，昨日 {payload['stats']['last_day_yi']} 亿元，"
        f"池 {payload['coverage']['min_pool']}~{payload['coverage']['max_pool']} 只，"
        f"拆分拦截 {payload['anomalies']['split_skipped']} 次，"
        f"净值缺失 {payload['anomalies']['nav_missing']} 次，"
        f"不完整日 {payload['anomalies']['incomplete_days']} 天",
        flush=True,
    )


if __name__ == "__main__":
    main()
