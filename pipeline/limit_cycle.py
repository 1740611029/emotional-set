"""连板周期展示产物：只读东财涨停池，生成 data/limit-cycle.json。

口径（用户 2026-11-13 确认）：
- 数据源 akshare 东方财富涨停股池；涨停认定以接口收录为准（收盘封板），
  含主板 / 创业板 / 科创板 / 北交所，不额外剔除 ST 或次新股；
- 连板数直接采用接口「连板数」字段，不自行判定连续性；
- 五阶段不设阈值、不做阶段归类；相位曲线只画已发生交易日的客观数据；
- 晋级率 = 昨日 2 板个股中今日仍在涨停池的家数 / 昨日 2 板总家数，
  样本量为累计分母，统计区间为有效交易日区间，逐日累计而非单日包装；
- 任一接口失败或无数据即整日跳过，不补 0、不插值、不向前填充。

本模块只写展示产物，不参与任何信号计算，也不改动既有算法链路。
"""
from __future__ import annotations

import json
import socket
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from pathlib import Path

import akshare as ak
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
CALENDAR = DATA / "raw" / "trade-calendar.json"
OUTPUT = DATA / "limit-cycle.json"

LOOKBACK_DAYS = 30
MAX_WORKERS = 4
CURVE_MAX_BOARDS = 10
STAGES = ["冰点", "修复", "发酵", "高潮", "退潮"]

socket.setdefaulttimeout(10)

METHODOLOGY = (
    "数据源：akshare 东方财富涨停股池 stock_zt_pool_em / 昨日涨停池 stock_zt_pool_previous_em / "
    "炸板股池 stock_zt_pool_zbgc_em。涨停认定以接口收录为准（收盘封板），含主板、创业板、科创板、"
    "北交所，不额外剔除 ST 与次新股；连板数直接采用接口「连板数」字段，不自行判定连续性。"
    "晋级率 = 昨日 2 板个股中今日仍在涨停池的家数 / 昨日 2 板总家数，样本量为累计分母，"
    "统计区间为有效交易日区间。情绪温度展示当日涨停家数及其在有效区间内的百分位排名，"
    "不含任何主观权重。五阶段色带仅作版式参考，不参与判定；页面不给出阶段归类结论。"
    "东财股池历史不可回溯，无长样本，本页仅作展示，不参与任何信号计算，"
    "数据不足以支撑阈值调参或统计推断。"
)


def _trading_days(limit: int) -> list[str]:
    """取截至今日的近 limit 个交易日；日历缺列时回退为空列表，不臆造日期。"""
    payload = json.loads(CALENDAR.read_text(encoding="utf-8"))
    dates = payload.get("dates") if isinstance(payload, dict) else None
    if not isinstance(dates, list):
        raise ValueError("交易日历缺少 dates")
    today = date.today().isoformat()
    eligible = [str(d)[:10] for d in dates if str(d)[:10] <= today]
    return eligible[-limit:]


def _fetch_day(day: str) -> tuple[str, pd.DataFrame | None, pd.DataFrame | None]:
    """单日涨停池 + 昨日涨停池；失败返回 None，由调用方整日跳过。"""
    compact = day.replace("-", "")
    try:
        zt = ak.stock_zt_pool_em(date=compact)
    except Exception:
        return day, None, None
    if zt is None or zt.empty or "连板数" not in zt.columns:
        return day, None, None
    try:
        prev = ak.stock_zt_pool_previous_em(date=compact)
    except Exception:
        prev = None
    return day, zt, prev


def _codes(frame: pd.DataFrame) -> set[str]:
    return set(frame["代码"].astype(str).str.strip().str.zfill(6))


def _seal_time(raw: object) -> str | None:
    text = str(raw).strip()
    if len(text) == 6 and text.isdigit():
        return f"{text[:2]}:{text[2:4]}"
    return text[:5] if text and text.lower() != "nan" else None


def _promote_counts(zt: pd.DataFrame, prev: pd.DataFrame | None) -> tuple[int, int] | None:
    """2→3 晋级：分母=昨日 2 板家数，分子=其中今日仍在涨停池的家数。"""
    if prev is None or prev.empty or "昨日连板数" not in prev.columns or "代码" not in prev.columns:
        return None
    boards = pd.to_numeric(prev["昨日连板数"], errors="coerce")
    second = prev[boards == 2]
    if second.empty:
        return None
    denominator = len(_codes(second))
    numer = len(_codes(second) & _codes(zt))
    return numer, denominator


def _stocks(zt: pd.DataFrame) -> list[dict]:
    rows: list[dict] = []
    ordered = zt.sort_values(["连板数", "代码"], ascending=[False, True])
    for _, row in ordered.iterrows():
        try:
            boards = int(row["连板数"])
        except (TypeError, ValueError):
            continue
        seal = pd.to_numeric(row.get("封板资金"), errors="coerce")
        broken = pd.to_numeric(row.get("炸板次数"), errors="coerce")
        rows.append({
            "code": str(row["代码"]).strip().zfill(6),
            "name": str(row["名称"]).strip(),
            "boards": boards,
            "sector": str(row.get("所属行业", "")).strip() or None,
            "seal_amount": round(float(seal) / 10000) if pd.notna(seal) else None,
            "first_seal": _seal_time(row.get("首次封板时间")),
            "status": "回封" if pd.notna(broken) and float(broken) > 0 else "封住",
        })
    return rows


def _percentile(values: list[int], current: int) -> int:
    """当日值在区间内的百分位排名（≤ 当前值的占比），纯排序、无参数。"""
    if not values:
        return 0
    return round(sum(1 for v in values if v <= current) / len(values) * 100)


def build_payload() -> dict:
    days = _trading_days(LOOKBACK_DAYS)
    with ThreadPoolExecutor(max_workers=MAX_WORKERS) as pool:
        fetched = list(pool.map(_fetch_day, days))
    valid = [(day, zt, prev) for day, zt, prev in fetched if zt is not None]
    valid.sort(key=lambda item: item[0])
    if not valid:
        raise RuntimeError("近 30 个交易日内东财涨停池全部返回空数据或异常")

    history: list[dict] = []
    promote_num = 0
    promote_den = 0
    promote_days = 0
    for day, zt, prev in valid:
        boards = pd.to_numeric(zt["连板数"], errors="coerce").dropna().astype(int)
        history.append({
            "date": day,
            "limit_up": int(len(zt)),
            "max_board": int(boards.max()) if len(boards) else 0,
            "continuous": int((boards >= 2).sum()),
        })
        counts = _promote_counts(zt, prev)
        if counts:
            promote_num += counts[0]
            promote_den += counts[1]
            promote_days += 1

    last_day, last_zt, _ = valid[-1]
    last = history[-1]
    previous = history[-2] if len(history) > 1 else None
    limit_values = [row["limit_up"] for row in history]

    boards_series = pd.to_numeric(last_zt["连板数"], errors="coerce").dropna().astype(int)
    max_board = last["max_board"]
    ladder = [{"board": level, "count": int((boards_series == level).sum())}
              for level in range(1, max_board + 1)]

    leader = last_zt[pd.to_numeric(last_zt["连板数"], errors="coerce") == max_board]
    leader_code = str(leader.sort_values("代码").iloc[0]["代码"]).strip().zfill(6) if not leader.empty else None

    broken_count = None
    try:
        broken = ak.stock_zt_pool_zbgc_em(date=last_day.replace("-", ""))
        if broken is not None and not broken.empty:
            broken_count = int(len(broken))
    except Exception:
        broken_count = None

    rate = round(promote_num / promote_den * 100, 1) if promote_den else None
    window = f"{history[0]['date']} ~ {last_day}（{promote_days} 个有效交易日累计）" if promote_den else None

    # 当日客观读数只放当日可观测值。晋级率属带样本量的统计口径，
    # 按用户要求收进页面默认折叠区，不占首屏；metrics 中仍完整保留比率 / n / 区间。
    readouts = [
        {"label": "最高板", "value": f"{max_board} 板", "sample": None},
        {"label": "连板家数", "value": str(last["continuous"]), "sample": None},
        {"label": "涨停家数", "value": str(last["limit_up"]), "sample": None},
    ]

    return {
        "data_state": "ready",
        "as_of": last_day,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "methodology": METHODOLOGY,
        "coverage": {
            "requested_days": len(days),
            "valid_days": len(valid),
            "start": history[0]["date"],
            "end": last_day,
            "note": "东财股池历史不可回溯；无数据的交易日整日跳过，不补 0、不插值。",
        },
        "phase": {
            "stages": STAGES,
            "curve_metric": "每日最高连板高度",
            "curve_unit": "板",
            "curve_max": CURVE_MAX_BOARDS,
            "observed": [{"date": row["date"], "value": row["max_board"]} for row in history],
            "readouts": readouts,
        },
        "temperature": {
            "metric": "当日涨停家数",
            "unit": "家",
            "value": last["limit_up"],
            "change": last["limit_up"] - previous["limit_up"] if previous else None,
            "percentile": _percentile(limit_values, last["limit_up"]),
            "series": [{"date": row["date"], "value": row["limit_up"]}
                       for row in history],
            "formula": "当日涨停家数 = 东财涨停池行数；分位为该值在有效交易日区间内的百分位排名，无主观权重。",
        },
        "metrics": {
            "max_board": max_board,
            "max_board_leader": leader_code,
            "continuous_count": last["continuous"],
            "continuous_change": last["continuous"] - previous["continuous"] if previous else None,
            "limit_up_count": last["limit_up"],
            "broken_count": broken_count,
            "promote_rate": rate,
            "promote_sample": promote_den if promote_den else None,
            "promote_window": window,
        },
        "ladder": ladder,
        "stocks": _stocks(last_zt),
    }


def main() -> int:
    payload = build_payload()
    OUTPUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"已写入 {OUTPUT}")
    print(f"覆盖 {payload['coverage']['valid_days']}/{payload['coverage']['requested_days']} 个交易日"
          f" · 区间 {payload['coverage']['start']} ~ {payload['coverage']['end']}")
    print(f"截至 {payload['as_of']}：涨停 {payload['metrics']['limit_up_count']} 家"
          f" · 最高 {payload['metrics']['max_board']} 板"
          f" · 连板 {payload['metrics']['continuous_count']} 家"
          f" · 2→3 晋级率 {payload['metrics']['promote_rate']}% (n={payload['metrics']['promote_sample']})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
