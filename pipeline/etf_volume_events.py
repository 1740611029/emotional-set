"""ETF 放量事件识别与分层统计（纯函数，无 I/O）。

生产构建与研究脚本共用本模块，因此"图上标的事件"与"报告统计的事件"必然同源。
所有判定只使用当日及之前的数据：日级放量只看当日成交额，市场状态只看当日收盘
及之前的均线与高点，前瞻收益只用于事后统计、不回流到识别。
"""
from __future__ import annotations

import math
from collections.abc import Sequence

import pandas as pd

ALGORITHM_VERSION = "etf-volume-spike-v1.2"

# ---------- 冻结参数（预注册，禁止依据统计结果事后调优） ----------
POOL_SIZE = 6                 # 核心池标的数：L3 = 全部 6 只放量
THRESHOLD_AMOUNT = 1e10       # 放量阈值：单日成交额 ≥ 100 亿元（单位元）
BASELINE_LOW = 5e9            # 普通放量基线带下界：50 亿元
BASELINE_HIGH = 1e10          # 普通放量基线带上界：100 亿元（不含）
CLUSTER_GAP_DAYS = 2          # 放量簇合并间隔：相邻放量日间隔 ≤ 2 个交易日合并
MA_WINDOW = 120               # 趋势均线窗口：半年
HIGH_WINDOW = 250             # 回撤观察窗口：一年高点
BOTTOM_DRAWDOWN = 0.15        # 底部区：距 250 日高点回撤 ≥ 15%
UPTREND_DRAWDOWN = 0.08       # 上行区：距 250 日高点回撤 < 8%
FORWARD_HORIZONS = (1, 3, 5, 10, 20)  # 前瞻期：与既有信号评价窗口对齐
MIN_LAYER_SAMPLES = 8         # 单层样本下限，低于此只标"样本不足"
MIN_L3_SAMPLES = 3            # L3 单独下结论的下限
STATE_BASIS_INDEX = "sh000300"  # 市场状态唯一基准指数：沪深300

LEVELS = ("L1", "L2", "L3")
LEVEL_RANK = {"L1": 1, "L2": 2, "L3": 3}
STATE_BOTTOM = "下跌底部区"
STATE_UPTREND = "趋势上行区"
STATE_NEUTRAL = "中性过渡区"
STATES = (STATE_BOTTOM, STATE_UPTREND, STATE_NEUTRAL)

VERDICT_TRUE = "成立"
VERDICT_FALSE = "不成立"
VERDICT_UNKNOWN = "样本不足，暂不能判断"
VERDICT_NO_BASELINE = "未见基线对照"

PREREGISTERED = {
    "algorithm_version": ALGORITHM_VERSION,
    "放量阈值(元)": THRESHOLD_AMOUNT,
    "普通放量基线带(元)": [BASELINE_LOW, BASELINE_HIGH],
    "簇合并间隔(交易日)": CLUSTER_GAP_DAYS,
    "状态基准指数": STATE_BASIS_INDEX,
    "趋势均线窗口": MA_WINDOW,
    "回撤观察窗口": HIGH_WINDOW,
    "底部区回撤阈值": BOTTOM_DRAWDOWN,
    "上行区回撤阈值": UPTREND_DRAWDOWN,
    "前瞻期(交易日)": list(FORWARD_HORIZONS),
    "单层样本下限": MIN_LAYER_SAMPLES,
    "L3样本下限": MIN_L3_SAMPLES,
    "状态集合": list(STATES),
    "事件级别口径": "簇首日当日级别（后续放量不回标）",
}


def _day_text(value) -> str:
    return str(value)[:10]


def _amount_series(frame: pd.DataFrame) -> pd.Series:
    dates = frame["date"].astype(str).str.slice(0, 10).to_numpy()
    values = pd.to_numeric(frame["amount"], errors="coerce").to_numpy()
    return pd.Series(values, index=dates, dtype="float64")


def amount_matrix(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """日期 × 代码 的成交额矩阵（元）；日期升序，缺失标的当日为 NaN。"""
    matrix = pd.DataFrame({code: _amount_series(frame) for code, frame in frames.items()})
    return matrix.sort_index()


def classify_level(hits: int) -> str | None:
    """按当日命中核心池的只数定级：1 只 L1，2~5 只 L2，6 只 L3。"""
    if hits <= 0:
        return None
    if hits == 1:
        return "L1"
    if hits < POOL_SIZE:
        return "L2"
    return "L3"


def daily_records(frames: dict[str, pd.DataFrame],
                  threshold: float = THRESHOLD_AMOUNT) -> list[dict]:
    """逐日识别放量：只由当日各标成交额判定，日期升序。"""
    matrix = amount_matrix(frames)
    records = []
    for day, row in matrix.iterrows():
        hit = [code for code in matrix.columns
               if pd.notna(row[code]) and float(row[code]) >= threshold]
        if not hit:
            continue
        records.append({
            "date": _day_text(day),
            "level": classify_level(len(hit)),
            "etf_codes": hit,
            "total_amount": float(sum(float(row[code]) for code in hit)),
        })
    return records


def normal_volume_days(frames: dict[str, pd.DataFrame], *,
                       low: float = BASELINE_LOW, high: float = BASELINE_HIGH,
                       threshold: float = THRESHOLD_AMOUNT) -> list[str]:
    """普通放量日基线：任一标成交额落在 [50 亿, 100 亿) 且当日无标达标阈值。"""
    matrix = amount_matrix(frames)
    days = []
    for day, row in matrix.iterrows():
        values = [float(value) for value in row if pd.notna(value)]
        if not values or any(value >= threshold for value in values):
            continue
        if any(low <= value < high for value in values):
            days.append(_day_text(day))
    return days


def merge_clusters(days: Sequence[str], hits: Sequence[str],
                   gap: int = CLUSTER_GAP_DAYS) -> list[dict]:
    """把放量日按交易日间隔合并成簇；事件日取簇首日，不用成交额最高日回填。"""
    positions = {_day_text(day): index for index, day in enumerate(days)}
    ordered = sorted({_day_text(day) for day in hits})
    clusters: list[list[str]] = []
    for day in ordered:
        if not clusters:
            clusters.append([day])
            continue
        previous = positions.get(clusters[-1][-1])
        current = positions.get(day)
        if previous is not None and current is not None and current - previous <= gap:
            clusters[-1].append(day)
        else:
            clusters.append([day])
    return [{
        "start_date": cluster[0], "end_date": cluster[-1],
        "days": len(cluster), "dates": list(cluster),
    } for cluster in clusters]


def _history_frame(history) -> pd.DataFrame:
    """统一成 date(字符串)/close 两列，保持来源顺序。"""
    if not isinstance(history, pd.DataFrame):
        raise TypeError("history 必须是含 date/close 列的 DataFrame")
    frame = pd.DataFrame({
        "date": history["date"].astype(str).str.slice(0, 10),
        "close": pd.to_numeric(history["close"], errors="coerce"),
    })
    return frame.sort_values("date").reset_index(drop=True)


def market_state(history, day: str) -> str:
    """事件日收盘即可确定的市场状态；只用 day 及之前的 MA120 与 250 日高点。"""
    frame = _history_frame(history)
    window = frame[frame["date"] <= _day_text(day)]
    if window.empty:
        return STATE_NEUTRAL
    close = window["close"]
    last = float(close.iat[-1])
    ma = close.rolling(MA_WINDOW).mean().iat[-1]
    high = close.rolling(HIGH_WINDOW).max().iat[-1]
    if pd.isna(ma) or pd.isna(high) or not high or not last:
        return STATE_NEUTRAL
    drawdown = 1 - last / float(high)
    if last <= float(ma) and drawdown >= BOTTOM_DRAWDOWN:
        return STATE_BOTTOM
    if last > float(ma) and drawdown < UPTREND_DRAWDOWN:
        return STATE_UPTREND
    return STATE_NEUTRAL


def wilson_interval(successes: int, samples: int, z: float = 1.96):
    """胜率的 Wilson 95% 区间；无样本时返回 (None, None)。"""
    if samples <= 0:
        return None, None
    phat = successes / samples
    denominator = 1 + z * z / samples
    centre = (phat + z * z / (2 * samples)) / denominator
    spread = z * math.sqrt(phat * (1 - phat) / samples + z * z / (4 * samples * samples)) / denominator
    return max(centre - spread, 0.0), min(centre + spread, 1.0)


def forward_returns(index_frame: pd.DataFrame, dates: Sequence[str],
                    horizons: Sequence[int] = FORWARD_HORIZONS) -> dict[str, dict]:
    """每个日期每个前瞻期的主基准收益与期内最大回撤；末端样本不足时为 None。"""
    frame = _history_frame(index_frame)
    closes = frame["close"].to_numpy(dtype="float64")
    positions = {day: index for index, day in enumerate(frame["date"])}
    result: dict[str, dict] = {}
    for day in dates:
        position = positions.get(_day_text(day))
        entry: dict[str, dict] = {}
        for horizon in horizons:
            value: dict[str, float | None] = {"return": None, "max_drawdown": None}
            if position is not None and position + horizon < len(closes):
                base = closes[position]
                if base:
                    window = closes[position + 1: position + horizon + 1]
                    value = {
                        "return": float(closes[position + horizon] / base - 1),
                        "max_drawdown": float(min(0.0, window.min() / base - 1)),
                    }
            entry[str(horizon)] = value
        result[_day_text(day)] = entry
    return result


def layer_stats(index_frame: pd.DataFrame, dates: Sequence[str],
                horizons: Sequence[int] = FORWARD_HORIZONS,
                min_samples: int = MIN_LAYER_SAMPLES) -> dict:
    """一层样本的前瞻统计：样本量、区间、胜率、均值、最大回撤与 Wilson 区间。"""
    ordered = sorted({_day_text(day) for day in dates})
    returns = forward_returns(index_frame, ordered, horizons)
    stats: dict[str, dict] = {}
    for horizon in horizons:
        valid_dates = [day for day in ordered
                       if returns[day][str(horizon)]["return"] is not None]
        pairs = [(returns[day][str(horizon)]["return"], returns[day][str(horizon)]["max_drawdown"])
                 for day in valid_dates]
        samples = len(pairs)
        wins = sum(1 for value, _ in pairs if value > 0)
        low, high = wilson_interval(wins, samples)
        stats[str(horizon)] = {
            "samples": samples,
            "start": valid_dates[0] if valid_dates else None,
            "end": valid_dates[-1] if valid_dates else None,
            "wins": wins,
            "rate": (wins / samples) if samples else None,
            "mean": (sum(value for value, _ in pairs) / samples) if samples else None,
            "avg_max_drawdown": (sum(drawdown for _, drawdown in pairs) / samples) if samples else None,
            "wilson95": [low, high],
            "small_sample": samples < min_samples,
        }
    return {
        "n": len(ordered),
        "start": ordered[0] if ordered else None,
        "end": ordered[-1] if ordered else None,
        "horizons": stats,
    }


def verdict_for(rate, samples: int, baseline_rate, baseline_samples: int, *,
                min_samples: int = MIN_LAYER_SAMPLES) -> str:
    """结论三态：样本不足 / 未见基线对照 / 成立或不成立。"""
    if rate is None or samples < min_samples:
        return VERDICT_UNKNOWN
    if not baseline_samples or baseline_rate is None:
        return VERDICT_NO_BASELINE
    return VERDICT_TRUE if rate > baseline_rate else VERDICT_FALSE


def layer_min_samples(key: str) -> int:
    """L3 只需 3 次即可单独描述，其余层需要 8 个事件。"""
    return MIN_L3_SAMPLES if key == "L3" else MIN_LAYER_SAMPLES


def forward_stats(events: Sequence[dict], index_frame: pd.DataFrame, *,
                  horizons: Sequence[int] = FORWARD_HORIZONS,
                  baselines: dict | None = None) -> dict:
    """按级别与市场状态分层的前瞻统计，并与两类基线对照后给出三态结论。

    events: 事件簇清单（需含 start_date / level / state）。
    baselines: {"same_state": {state: [日期]}, "normal_volume": [日期]}；
               缺省视为"未见基线对照"，不会给出"成立"。
    """
    events = list(events)
    layers = []
    for level in LEVELS:
        dates = [event["start_date"] for event in events if event["level"] == level]
        states = sorted({event["state"] for event in events if event["level"] == level})
        layer = layer_stats(index_frame, dates, horizons, layer_min_samples(level))
        layer["key"] = level
        layer["kind"] = "level"
        layer["label"] = f"{level} 放量事件"
        layer["states"] = states
        _attach_verdicts(layer, states, index_frame, horizons, baselines,
                         min_samples=layer_min_samples(level))
        layers.append(layer)
    for state in STATES:
        dates = [event["start_date"] for event in events if event["state"] == state]
        layer = layer_stats(index_frame, dates, horizons)
        layer["key"] = state
        layer["kind"] = "state"
        layer["label"] = f"{state} 放量事件"
        layer["states"] = [state]
        _attach_verdicts(layer, [state], index_frame, horizons, baselines)
        layers.append(layer)

    baseline_stats = None
    normal_stats = None
    if baselines:
        baseline_stats = {
            state: layer_stats(index_frame, baselines.get("same_state", {}).get(state, []), horizons)
            for state in STATES
        }
        normal_stats = layer_stats(index_frame, baselines.get("normal_volume", []), horizons)
    return {"horizons": list(horizons), "layers": layers,
            "baselines": {"same_state": baseline_stats, "normal_volume": normal_stats}}


def _attach_verdicts(layer: dict, states: Sequence[str], index_frame: pd.DataFrame,
                     horizons: Sequence[int], baselines: dict | None, *,
                     min_samples: int = MIN_LAYER_SAMPLES) -> None:
    """为每一层每个前瞻期写入与"同状态非放量日"基线对照后的三态结论。"""
    baseline_stats = None
    if baselines:
        same_state = baselines.get("same_state") or {}
        baseline_dates = sorted({day for state in states for day in same_state.get(state, [])})
        baseline_stats = layer_stats(index_frame, baseline_dates, horizons)
    for horizon in horizons:
        cell = layer["horizons"][str(horizon)]
        baseline_cell = (baseline_stats or {}).get("horizons", {}).get(str(horizon), {})
        cell["verdict"] = verdict_for(
            cell["rate"], cell["samples"], baseline_cell.get("rate"),
            baseline_cell.get("samples", 0), min_samples=min_samples,
        )
        cell["baseline_samples"] = baseline_cell.get("samples", 0)
        cell["baseline_rate"] = baseline_cell.get("rate")
        cell["baseline_start"] = baseline_cell.get("start")
        cell["baseline_end"] = baseline_cell.get("end")
        cell["baseline_wilson95"] = baseline_cell.get("wilson95", [None, None])


def identify(frames: dict[str, pd.DataFrame], index_frame: pd.DataFrame,
             *, threshold: float = THRESHOLD_AMOUNT) -> dict:
    """逐日放量与事件簇：事件日、级别、状态在簇首日固定，不因后续放量回标。

    end_date、days、peak_amount 仅描述截至输入末日已发生的簇过程，
    可随新数据延长或增大，不用于首日的收益分层。
    """
    days = daily_records(frames, threshold)
    calendar = sorted({_day_text(day) for frame in frames.values()
                       for day in frame["date"].astype(str)})
    clusters = merge_clusters(calendar, [row["date"] for row in days])
    by_date = {row["date"]: row for row in days}
    for row in days:
        row["state"] = market_state(index_frame, row["date"])
    events = []
    for cluster in clusters:
        members = [by_date[day] for day in cluster["dates"]]
        events.append({
            "start_date": cluster["start_date"], "end_date": cluster["end_date"],
            "days": cluster["days"],
            "level": by_date[cluster["start_date"]]["level"],
            "state": market_state(index_frame, cluster["start_date"]),
            "peak_amount": max(row["total_amount"] for row in members),
        })
    return {"days": days, "events": events}
