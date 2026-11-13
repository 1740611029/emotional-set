"""龙虎榜知名游资追踪：东财龙虎榜 → 席位匹配 → 游资操作聚合。

定位：**只做事后统计与展示**，不得被任何顶部/底部信号、板块趋势或候选状态
计算读取（AGENTS 第 1 条）。

数据源（akshare/东财，口径固定）：
- `stock_lhb_detail_em(start_date, end_date)` 每日上榜股票汇总；
- `stock_lhb_stock_detail_em(symbol, date, flag)` 个股买卖前 5 席位明细。
- `stock_lhb_yyb_detail_em` 按营业部 ID 查历史、非按日期批量，弃用。

统计口径：
- 仅统计**单日榜**（上榜原因含「连续」的三日榜只落原始记录、不进任何金额统计，
  避免三日累计与单日口径混加）；
- 上榜股票可能因多个原因同日重复上榜，记录按（日期+代码+原因）粒度保留；
- 席位匹配：营业部全名归一化精确匹配，未命中且配置了 addr/broker 的席位
  按「券商名前缀 + 地址核心词」二级匹配；低置信命中在产物中如实标注。

落盘：
- 原始留痕 data/raw/lhb/detail_YYYYMMDD.csv、seats_YYYYMMDD.csv（可回溯重算）；
- 聚合产物 data/lhb-traders.json（protected_write 原子写，先过 schema）。
"""
from __future__ import annotations

import argparse
import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd

from io_utils import protected_write

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW = DATA / "raw" / "lhb"
SEAT_MAP_FILE = Path(__file__).resolve().parent / "lhb_seat_map.json"
BOARD_MAP_FILE = RAW / "board-map.json"
OUTPUT_NAME = "lhb-traders.json"

CN = ZoneInfo("Asia/Shanghai")
ALGORITHM_VERSION = "lhb-traders-v1.1"

MAX_WORKERS = 3          # 席位明细并发数：太高会触发东财限流
DETAIL_SLEEP = 0.15      # 每次明细请求后的间隔（秒）
RETRY_ATTEMPTS = 3       # 单请求失败重试次数
RETRY_BACKOFF = 1.5      # 重试退避基数（秒）
BOARD_TTL_DAYS = 7       # 板块成分映射缓存有效期

DEFAULT_WINDOW_DAYS = 30  # 首次建库默认回看自然日窗口

# 三日榜/多日榜判定：上榜原因含「连续」的一律视为多日累计口径
MULTI_DAY_MARK = "连续"

# ---------- 在场（离场清仓）判定参数（2026-11-13 与用户确认口径） ----------
EXIT_MARK_DAYS = 3        # 离场标记保鲜期（交易日）：标记期内页面显示标记，超过隐藏配对
SILENT_PROBABLE = 4       # 疑似离场静默期（交易日）：最后现身起连续无记录天数
SILENT_FORCED = 8         # 强制离场兜底（交易日）：无任何卖出证据时的最后防线
HEAVY_SELL_RATIO = 0.8    # 单日大幅出货判定：卖出占当日买卖合计比 > 80%
TURNOVER_COVER_X = 5      # 换手覆盖倍数：静默期个股市场总成交额 ≥ 窗口内买入 × 5 视为有机会出完


def normalize_branch(name: str) -> str:
    """营业部全名归一化：去公司组织后缀、去尾部「证券营业部」，统一无空格。"""
    s = str(name).strip().replace(" ", "")
    for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
        s = s.replace(suffix, "")
    if s.endswith("证券营业部"):
        s = s[:-len("证券营业部")]
    return s


class SeatMatcher:
    """席位 → 游资匹配器。启动时建索引并自查冲突，命中返回游资与置信度。"""

    def __init__(self, seat_map_file: Path = SEAT_MAP_FILE) -> None:
        raw = json.loads(seat_map_file.read_text(encoding="utf-8"))
        self.version = raw.get("version", "unknown")
        self.traders = {t["id"]: t for t in raw["traders"]}
        self._exact: dict[str, tuple[str, dict]] = {}
        self._fuzzy: dict[tuple[str, str], tuple[str, dict]] = {}
        for tid, trader in self.traders.items():
            for seat in trader.get("seats", []):
                key = normalize_branch(seat["full"])
                if key in self._exact and self._exact[key][0] != tid:
                    raise ValueError(f"席位精确键冲突：{key} 同时属于 "
                                     f"{self._exact[key][0]} 与 {tid}")
                self._exact[key] = (tid, seat)
                addr = seat.get("addr")
                broker = seat.get("broker")
                if addr and broker:
                    fkey = (broker, addr)
                    if fkey in self._fuzzy and self._fuzzy[fkey][0] != tid:
                        raise ValueError(f"二级匹配键冲突：{fkey} 同时属于 "
                                         f"{self._fuzzy[fkey][0]} 与 {tid}")
                    self._fuzzy[fkey] = (tid, seat)

    def match(self, branch_name: str) -> tuple[str, dict, str] | None:
        """返回 (trader_id, seat, match_level)；未命中返回 None。"""
        norm = normalize_branch(branch_name)
        hit = self._exact.get(norm)
        if hit:
            return hit[0], hit[1], "exact"
        for (broker, addr), (tid, seat) in self._fuzzy.items():
            if norm.startswith(broker) and addr in norm:
                return tid, seat, "addr"
        return None


# ---------- 拉取 ----------

def _retry_call(fn, *, label: str, errors: list[str]):
    last_exc: Exception | None = None
    for attempt in range(1, RETRY_ATTEMPTS + 1):
        try:
            return fn()
        except Exception as exc:  # noqa: BLE001  # 源接口异常种类多，统一退避重试
            last_exc = exc
            if attempt < RETRY_ATTEMPTS:
                time.sleep(RETRY_BACKOFF * attempt)
    msg = f"{label} 连续 {RETRY_ATTEMPTS} 次失败：{type(last_exc).__name__}: {last_exc}"
    errors.append(msg)
    print(f"[lhb] {msg}", flush=True)
    return None


def fetch_day(date8: str, errors: list[str]) -> bool:
    """拉取单个交易日的汇总+席位明细并落原始 CSV。返回是否拿到汇总数据。"""
    import akshare as ak  # noqa: PLC0415  # 惰性导入，聚合模式不必装 akshare 也能重算

    date10 = f"{date8[:4]}-{date8[4:6]}-{date8[6:]}"
    detail = _retry_call(lambda: ak.stock_lhb_detail_em(start_date=date8, end_date=date8),
                         label=f"汇总 {date8}", errors=errors)
    if detail is None or detail.empty:
        return False
    detail = detail.copy()
    detail["上榜日"] = date10
    detail.to_csv(RAW / f"detail_{date8}.csv", index=False, encoding="utf-8-sig")

    # 席位明细接口按（代码+日期+方向）返回该股当日全部上榜原因的合集，
    # 与请求原因无关 → 按 code 去重请求，避免同一合集重复拉取 N 倍
    codes = detail["代码"].drop_duplicates().tolist()
    total = len(codes)
    print(f"[lhb] {date10} 上榜 {total} 只（按代码去重），开始拉席位明细…", flush=True)

    frames: list[pd.DataFrame] = []
    done = 0

    def _one(code: str) -> pd.DataFrame | None:
        parts = []
        for flag in ("买入", "卖出"):
            seats = _retry_call(
                lambda c=code, f=flag: ak.stock_lhb_stock_detail_em(symbol=c, date=date8, flag=f),
                label=f"席位 {code} {flag}", errors=errors)
            if seats is not None and not seats.empty:
                seats = seats.copy()
                # 接口原生「类型」列即行级真实上榜原因（区分单日榜/多日榜），保留为准
                seats = seats.rename(columns={"类型": "上榜原因"})
                seats.insert(0, "代码", code)
                seats.insert(1, "方向", flag)
                seats.insert(2, "上榜日", date10)
                parts.append(seats)
            time.sleep(DETAIL_SLEEP)
        return pd.concat(parts, ignore_index=True) if parts else None

    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        futures = {pool.submit(_one, code): code for code in codes}
        for future in as_completed(futures):
            frame = future.result()
            done += 1
            if frame is not None:
                frames.append(frame)
            if done % 10 == 0 or done == total:
                print(f"[lhb] {date10} 席位明细进度 {done}/{total}", flush=True)

    if frames:
        pd.concat(frames, ignore_index=True).to_csv(
            RAW / f"seats_{date8}.csv", index=False, encoding="utf-8-sig")
    return True


def fetch_range(start8: str, end8: str, *, force: bool = False) -> list[str]:
    """逐日拉取 [start, end] 内有数据的交易日。返回成功落盘的日期列表（YYYYMMDD）。"""
    RAW.mkdir(parents=True, exist_ok=True)
    fetched: list[str] = []
    cur = datetime.strptime(start8, "%Y%m%d").date()
    end = datetime.strptime(end8, "%Y%m%d").date()
    while cur <= end:
        date8 = cur.strftime("%Y%m%d")
        # 汇总与席位明细都完整落盘才算已完成，缺一重拉（此前中断可能只留下汇总）
        if ((RAW / f"detail_{date8}.csv").exists()
                and (RAW / f"seats_{date8}.csv").exists() and not force):
            fetched.append(date8)
            cur += timedelta(days=1)
            continue
        errors: list[str] = []
        if fetch_day(date8, errors):
            fetched.append(date8)
        cur += timedelta(days=1)
    return fetched


# ---------- 板块映射 ----------

def ensure_board_map(*, force: bool = False) -> dict:
    """个股代码 → 行业板块（独立展示口径）。

    主源：新浪行业板块（稳定、不限流，覆盖约 65%）。
    增强：每日更新时静默探测一次东财行业板块（push2），解封则自动增量补全
    新浪未覆盖的个股（覆盖率可提升至 95%+）；限流中则无感跳过，不影响产物。
    口径说明（用户 2026-11-12 确认）：龙虎榜页面的板块归属与【板块趋势】页面
    的申万体系是两套口径、不互相对照、互不引用。
    """
    import akshare as ak  # noqa: PLC0415

    stale_cache: dict | None = None
    if BOARD_MAP_FILE.exists():
        stale_cache = json.loads(BOARD_MAP_FILE.read_text(encoding="utf-8"))
        if not force:
            built = datetime.strptime(stale_cache["built_at"][:10], "%Y-%m-%d").date()
            if (datetime.now(CN).date() - built).days < BOARD_TTL_DAYS:
                return stale_cache["map"]
    print("[lhb] 拉取新浪行业板块成分映射…", flush=True)
    try:
        spot = ak.stock_sector_spot(indicator="新浪行业")
    except Exception as exc:  # noqa: BLE001
        if stale_cache:
            print(f"[lhb] 新浪板块列表拉取失败（{exc}），降级用旧缓存"
                  f"（built_at={stale_cache['built_at'][:10]}）", flush=True)
            return stale_cache["map"]
        print(f"[lhb] 新浪板块列表拉取失败且无缓存（{exc}），降级为空映射，"
              f"板块字段留空，恢复后 --aggregate-only --force-board 补", flush=True)
        return {}
    mapping: dict[str, dict] = {}
    failed = 0
    for _, row in spot.iterrows():
        label = str(row["label"])
        board_name = str(row["板块"])
        cons = None
        for attempt in range(1, RETRY_ATTEMPTS + 1):
            try:
                cons = ak.stock_sector_detail(sector=label)
                break
            except Exception as exc:  # noqa: BLE001
                if attempt < RETRY_ATTEMPTS:
                    time.sleep(RETRY_BACKOFF * attempt)
        if cons is None or cons.empty:
            failed += 1
            print(f"[lhb] 板块 {board_name} 成分拉取失败，跳过", flush=True)
        else:
            for _, stock in cons.iterrows():
                code = str(stock["code"]).zfill(6)
                mapping.setdefault(code, {"name": str(stock["name"]), "board": board_name})
        time.sleep(0.15)
    ok_boards = len(spot) - failed
    if ok_boards <= len(spot) // 2 and stale_cache:
        print(f"[lhb] 板块映射过半失败（{failed}/{len(spot)}），降级用旧缓存", flush=True)
        return stale_cache["map"]

    # 东财增强：解封则增量补全新浪未覆盖个股；限流中静默跳过
    source = "akshare stock_sector_spot/stock_sector_detail（新浪行业，独立展示口径）"
    try:
        boards = ak.stock_board_industry_name_em()
    except Exception:  # noqa: BLE001  # push2 限流为常态，静默降级
        print("[lhb] 东财板块增强未可用（限流中），保持新浪口径覆盖", flush=True)
        boards = None
    if boards is not None:
        print("[lhb] 东财板块可用，增量补全新浪未覆盖个股…", flush=True)
        patched = 0
        for _, row in boards.iterrows():
            board_name = str(row["板块名称"])
            try:
                cons = ak.stock_board_industry_cons_em(symbol=board_name)
            except Exception:  # noqa: BLE001
                continue
            for _, stock in cons.iterrows():
                code = str(stock["代码"]).zfill(6)
                if code not in mapping:
                    mapping[code] = {"name": str(stock["名称"]), "board": board_name}
                    patched += 1
            time.sleep(0.4)
        source = "新浪行业打底 + 东财行业板块增量补全（独立展示口径）"
        print(f"[lhb] 东财增量补全 {patched} 只个股", flush=True)

    payload = {"built_at": datetime.now(CN).isoformat(timespec="seconds"),
               "source": source,
               "board_count": ok_boards, "stock_count": len(mapping), "map": mapping}
    BOARD_MAP_FILE.parent.mkdir(parents=True, exist_ok=True)
    BOARD_MAP_FILE.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
    print(f"[lhb] 板块映射落盘：新浪 {ok_boards} 板块 / 共 {len(mapping)} 只个股"
          f"（新浪失败 {failed}）", flush=True)
    return payload["map"]


# ---------- 聚合 ----------

def _read_seats() -> pd.DataFrame:
    frames = []
    for path in sorted(RAW.glob("seats_*.csv")):
        try:
            frames.append(pd.read_csv(path, dtype={"代码": str}))
        except Exception as exc:  # noqa: BLE001
            print(f"[lhb] 原始席位文件读取失败 {path.name}：{exc}", flush=True)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _read_details() -> pd.DataFrame:
    frames = []
    for path in sorted(RAW.glob("detail_*.csv")):
        try:
            frames.append(pd.read_csv(path, dtype={"代码": str}))
        except Exception as exc:  # noqa: BLE001
            print(f"[lhb] 原始汇总文件读取失败 {path.name}：{exc}", flush=True)
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def _compute_positions(
    records: dict[str, list[dict]], trading_dates: list[str], detail: pd.DataFrame,
) -> dict[str, list[dict]]:
    """按（游资, 个股）计算窗口内在场状态（只用单日榜记录，与统计口径一致）。

    判定规则（2026-11-13 与用户确认）：
    - exited_confirmed 确认清仓：
      C1 卖出覆盖：窗口内累计卖出 ≥ 累计买入（证据日=最后一笔卖出日）；
      C2 大幅出货：最后一条记录卖出占当日买卖合计 > 80%，且该股此后再上榜过
        （有效观察：游资未现身买方——若现身会产生新记录，last_seen 会后移）。
    - exited_probable 疑似离场：最后现身 ≥4 个交易日静默，且静默期内该股再上榜过、
      或静默期个股市场总成交额 ≥ 窗口内买入 ×5（换手覆盖推断，兜底
      "卖出日未触发上榜/卖出量未进前五"的结构性盲区）。
    - exited_forced 强制离场：最后现身 ≥8 个交易日无任何记录（时间兜底，
      保证状态机完备；因标记保鲜仅 3 日，页面可见效果与"超期隐藏"一致）。
    - active 在场：以上均不满足。
    exit_date 为判定成立日：确认清仓=清仓证据日；疑似/强制=静默达标交易日。
    前端在 exit_date 起 EXIT_MARK_DAYS 个交易日内显示标记，超过则隐藏该配对
    （产物数据完整保留，仅页面过滤）。窗口前买入不可见，判定仅基于窗口内数据。
    """
    date_idx = {d: i for i, d in enumerate(trading_dates)}
    # 个股维度索引：全部上榜日集合（触发式披露的全部观察窗口）+ 每日市场总成交额
    stock_days: dict[str, set[str]] = {}
    stock_amt: dict[tuple[str, str], float] = {}
    for _, row in detail.iterrows():
        code = str(row["代码"]).zfill(6)
        day = str(row["上榜日"])
        stock_days.setdefault(code, set()).add(day)
        amt = row.get("市场总成交额")
        if amt is not None and not pd.isna(amt):
            stock_amt[(code, day)] = float(amt)

    positions: dict[str, list[dict]] = {}
    for tid, recs in records.items():
        by_code: dict[str, list[dict]] = {}
        for rec in recs:
            if rec["is_multi_day"]:
                continue
            by_code.setdefault(rec["code"], []).append(rec)
        if not by_code:
            continue
        rows_out: list[dict] = []
        for code, rows in by_code.items():
            rows.sort(key=lambda r: r["date"])
            last_seen = rows[-1]["date"]
            buy_total = sum(r["buy"] for r in rows)
            sell_total = sum(r["sell"] for r in rows)
            status, exit_date, exit_type = "active", None, None

            # C1 卖出覆盖买入 → 确认清仓
            if buy_total > 0 and sell_total >= buy_total:
                sell_dates = [r["date"] for r in rows if r["sell"] > 0]
                if sell_dates:
                    status, exit_date, exit_type = "exited_confirmed", sell_dates[-1], "sell_cover"
            # C2 单日大幅出货 + 此后该股再上榜（游资未现身买方）→ 确认清仓
            if status != "exited_confirmed":
                last = rows[-1]
                denom = last["buy"] + last["sell"]
                later_days = [d for d in stock_days.get(code, ()) if d > last_seen]
                if denom > 0 and last["sell"] / denom > HEAVY_SELL_RATIO and later_days:
                    status, exit_date, exit_type = "exited_confirmed", last_seen, "heavy_sell"
            # 静默期判定：疑似（4 日 + 有效观察）/ 强制（8 日兜底）
            silent_days = 0
            if status == "active" and trading_dates and last_seen in date_idx:
                silent_days = date_idx[trading_dates[-1]] - date_idx[last_seen]
                if silent_days >= SILENT_FORCED:
                    idx = min(date_idx[last_seen] + SILENT_FORCED, len(trading_dates) - 1)
                    status, exit_date, exit_type = "exited_forced", trading_dates[idx], "forced"
                elif silent_days >= SILENT_PROBABLE:
                    after_days = sorted(d for d in stock_days.get(code, ()) if d > last_seen)
                    covered = sum(stock_amt.get((code, d), 0.0) for d in after_days)
                    if after_days or (buy_total > 0 and covered >= buy_total * TURNOVER_COVER_X):
                        idx = min(date_idx[last_seen] + SILENT_PROBABLE, len(trading_dates) - 1)
                        status, exit_date, exit_type = "exited_probable", trading_dates[idx], "silent"
            rows_out.append({
                "code": code, "name": rows[-1]["name"],
                "status": status, "exit_type": exit_type,
                "last_seen": last_seen, "exit_date": exit_date,
                "buy_total": round(buy_total, 2), "sell_total": round(sell_total, 2),
                "silent_days": silent_days,
            })
        rows_out.sort(key=lambda p: p["last_seen"], reverse=True)
        positions[tid] = rows_out
    return positions


def aggregate(matcher: SeatMatcher | None = None, board_map: dict | None = None) -> dict:
    """扫描全部原始留痕，匹配席位并聚合为游资操作产物。"""
    matcher = matcher or SeatMatcher()
    board_map = board_map if board_map is not None else ensure_board_map()

    detail = _read_details()
    seats = _read_seats()
    if detail.empty or seats.empty:
        raise RuntimeError("原始留痕为空：先执行拉取（--rebuild 或 --daily）再聚合")

    # 汇总信息索引：(date, code) → 收盘价/涨跌幅（同股同日唯一，与上榜原因无关）
    quote_idx: dict[tuple[str, str], dict] = {}
    for _, row in detail.iterrows():
        key = (str(row["上榜日"]), str(row["代码"]).zfill(6))
        quote_idx[key] = {
            "name": str(row["名称"]),
            "close": None if pd.isna(row.get("收盘价")) else float(row["收盘价"]),
            "pct_chg": None if pd.isna(row.get("涨跌幅")) else float(row["涨跌幅"]),
        }

    unmatched: set[str] = set()
    # 席位层去重：同一（日期+代码+营业部）的行在多个上榜原因/买卖榜间重复披露且金额一致，
    # 且必须按单日榜/多日榜分组隔离——多日榜行是多日累计口径，绝不能与单日榜互比或混入统计；
    # 同组内取买入+卖出合计最大的一行作为该席位当日披露
    seat_dedupe: dict[tuple[str, str, str, bool], dict] = {}
    for _, row in seats.iterrows():
        hit = matcher.match(str(row["交易营业部名称"]))
        if hit is None:
            unmatched.add(str(row["交易营业部名称"]))
            continue
        tid, seat, level = hit
        date10 = str(row["上榜日"])
        code = str(row["代码"]).zfill(6)
        branch_norm = normalize_branch(row["交易营业部名称"])
        reason = str(row["上榜原因"])
        is_multi = MULTI_DAY_MARK in reason
        buy = 0.0 if pd.isna(row.get("买入金额")) else float(row["买入金额"])
        sell = 0.0 if pd.isna(row.get("卖出金额")) else float(row["卖出金额"])
        key = (date10, code, branch_norm, is_multi)
        prev = seat_dedupe.get(key)
        if prev is None or buy + sell > prev["buy"] + prev["sell"]:
            seat_dedupe[key] = {
                "tid": tid, "seat": seat, "level": level,
                "date": date10, "code": code, "branch": branch_norm,
                "buy": buy, "sell": sell, "reason": reason, "is_multi_day": is_multi,
            }

    # 聚合到（游资, 日期, 代码, 是否多日榜）粒度；同一记录合并多席位与多上榜原因
    records: dict[str, list[dict]] = {tid: [] for tid in matcher.traders}
    rec_map: dict[tuple[str, str, str, bool], dict] = {}
    for d in seat_dedupe.values():
        tid = d["tid"]
        key = (tid, d["date"], d["code"], d["is_multi_day"])
        rec = rec_map.get(key)
        if rec is None:
            quote = quote_idx.get((d["date"], d["code"]), {})
            rec = {
                "date": d["date"], "code": d["code"],
                "name": quote.get("name", ""),
                "reasons": [d["reason"]],
                "is_multi_day": d["is_multi_day"],
                "buy": 0.0, "sell": 0.0,
                "close": quote.get("close"), "pct_chg": quote.get("pct_chg"),
                "board": board_map.get(d["code"], {}).get("board"),
                "seat_hits": [], "confidence": d["seat"].get("confidence", "low"),
            }
            rec_map[key] = rec
            records[tid].append(rec)
        rec["buy"] += d["buy"]
        rec["sell"] += d["sell"]
        hit_label = f"{d['branch']}({d['level']})"
        if hit_label not in rec["seat_hits"]:
            rec["seat_hits"].append(hit_label)
        if d["reason"] not in rec["reasons"]:
            rec["reasons"].append(d["reason"])
        # 多席位命中时置信度取最低，如实反映模糊性
        order = {"high": 0, "medium": 1, "low": 2}
        if order.get(d["seat"].get("confidence", "low"), 2) > order.get(rec["confidence"], 2):
            rec["confidence"] = d["seat"].get("confidence", "low")

    for rec in rec_map.values():
        rec["net"] = round(rec["buy"] - rec["sell"], 2)
        rec["buy"] = round(rec["buy"], 2)
        rec["sell"] = round(rec["sell"], 2)

    # 每游资 records 按日期倒序、同日净额绝对值倒序（最近操作在前，卡片/详情直接取头部）
    for tid in records:
        records[tid].sort(key=lambda r: (r["date"], abs(r["net"])), reverse=True)

    trading_dates = sorted({str(d) for d in detail["上榜日"].unique()})

    # 在场状态判定（游资×个股，只用单日榜记录）：确认清仓 / 疑似离场 / 强制离场 / 在场
    positions = _compute_positions(records, trading_dates, detail)

    # 统计口径：只累计单日榜
    traders_payload = []
    for tid, trader in matcher.traders.items():
        single = [r for r in records[tid] if not r["is_multi_day"]]
        stats = {
            "active_days": len({r["date"] for r in single}),
            "records": len(single),
            "stocks": len({r["code"] for r in single}),
            "buy_total": round(sum(r["buy"] for r in single), 2),
            "sell_total": round(sum(r["sell"] for r in single), 2),
            "net_total": round(sum(r["net"] for r in single), 2),
        }
        traders_payload.append({
            "id": tid, "name": trader["name"], "full_name": trader.get("full_name", trader["name"]),
            "tier": trader.get("tier", ""), "style": trader.get("style", ""),
            "confidence": trader.get("confidence", "medium"), "note": trader.get("note", ""),
            "seats": trader.get("seats", []),
            "stats": stats, "records": records[tid],
            "positions": positions.get(tid, []),
        })
    daily: dict[str, list[dict]] = {d: [] for d in trading_dates}
    for tp in traders_payload:
        for rec in tp["records"]:
            if rec["is_multi_day"]:
                continue
            daily[rec["date"]].append({
                "trader": tp["name"], "trader_id": tp["id"], "tier": tp["tier"],
                "code": rec["code"], "name": rec["name"],
                "buy": rec["buy"], "sell": rec["sell"], "net": rec["net"],
                "pct_chg": rec["pct_chg"], "board": rec["board"],
                "confidence": rec["confidence"],
            })
    for rows in daily.values():
        rows.sort(key=lambda r: -abs(r["net"]))

    payload = {
        "as_of": trading_dates[-1] if trading_dates else None,
        "generated_at": datetime.now(CN).isoformat(timespec="seconds"),
        "algorithm_version": ALGORITHM_VERSION,
        "seat_map_version": matcher.version,
        "scope_note": "金额统计仅基于单日榜（上榜原因含「连续」的多日累计榜只存记录不参与统计）；"
                      "同股同席位跨上榜原因/买卖榜的重复披露已按当日去重；"
                      "板块归属为独立展示口径（新浪行业打底、东财解封后自动补全），与板块趋势页面申万体系不对照；"
                      "席位为公开资料整理非官方认证，低置信命中已在记录级标注；"
                      "在场判定（游资×个股，仅单日榜）：窗口内累计卖出≥累计买入、或最后记录卖出占比>80%且该股此后再上榜未现身买方→确认清仓；"
                      "最后现身≥4个交易日静默且静默期内该股再上榜或换手覆盖（静默期市场总成交额≥窗口内买入×5）→疑似离场；"
                      "≥8个交易日无记录→强制离场；离场标记保留3个交易日，之后该配对页面隐藏（数据完整保留）；"
                      "判定仅基于窗口内数据，窗口前买入不可见",
        "trading_dates": trading_dates,
        "traders": traders_payload,
        "daily": daily,
        "quality": {
            "unmatched_branches": sorted(unmatched),
            "unmatched_count": len(unmatched),
        },
    }
    return payload


def rebuild_output() -> dict:
    payload = aggregate()
    protected_write(OUTPUT_NAME, payload)
    stats = {t["name"]: t["stats"]["records"] for t in payload["traders"]}
    active = {k: v for k, v in stats.items() if v > 0}
    exited = sum(
        1 for t in payload["traders"]
        for p in t.get("positions", []) if p["status"] != "active")
    print(f"[lhb] 聚合完成：{len(payload['trading_dates'])} 个交易日，"
          f"{len(active)}/{len(stats)} 位游资有命中记录，"
          f"离场/清仓配对 {exited} 个，"
          f"未匹配营业部 {payload['quality']['unmatched_count']} 个", flush=True)
    return payload


# ---------- 增量入口 ----------

def latest_raw_date() -> str | None:
    dates = [p.stem.replace("detail_", "") for p in RAW.glob("detail_*.csv")]
    return max(dates) if dates else None


def run_daily_update(*, force_board: bool = False) -> dict:
    """服务端调度入口：补齐最近未拉取日期并重算产物。返回简要结果。"""
    last = latest_raw_date()
    today = datetime.now(CN).strftime("%Y%m%d")
    if last is None:
        start = (datetime.now(CN) - timedelta(days=DEFAULT_WINDOW_DAYS)).strftime("%Y%m%d")
    else:
        start = (datetime.strptime(last, "%Y%m%d") + timedelta(days=1)).strftime("%Y%m%d")
    if start > today:
        return {"status": "up_to_date", "last": last}
    errors: list[str] = []
    fetched = fetch_range(start, today)
    print(f"[lhb] 日更拉取 {start}-{today}：{len(fetched)} 个交易日有数据", flush=True)
    ensure_board_map(force=force_board)
    payload = rebuild_output()
    return {"status": "ok", "last": payload["as_of"], "fetched": fetched, "errors": errors}


def main() -> None:
    parser = argparse.ArgumentParser(description="龙虎榜知名游资数据管道")
    parser.add_argument("--rebuild", action="store_true", help="全量重建（默认近 30 自然日）")
    parser.add_argument("--daily", action="store_true", help="增量补齐到今天并重算")
    parser.add_argument("--start", help="起始日期 YYYYMMDD")
    parser.add_argument("--end", help="结束日期 YYYYMMDD")
    parser.add_argument("--force-board", action="store_true", help="强制刷新板块映射")
    parser.add_argument("--aggregate-only", action="store_true", help="只用本地原始留痕重算")
    args = parser.parse_args()

    RAW.mkdir(parents=True, exist_ok=True)
    if args.aggregate_only:
        ensure_board_map(force=args.force_board)
        rebuild_output()
        return
    if args.daily:
        run_daily_update(force_board=args.force_board)
        return

    end = args.end or datetime.now(CN).strftime("%Y%m%d")
    start = args.start or (datetime.now(CN) - timedelta(days=DEFAULT_WINDOW_DAYS)).strftime("%Y%m%d")
    t0 = time.time()
    fetched = fetch_range(start, end)
    print(f"[lhb] 拉取完成：{len(fetched)} 个交易日，耗时 {time.time() - t0:.0f}s", flush=True)
    ensure_board_map(force=args.force_board)
    rebuild_output()


if __name__ == "__main__":
    main()
