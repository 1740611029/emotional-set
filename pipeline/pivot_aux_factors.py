"""辅助因子（离线调研专用）：只加分、不否决、当日可得；绝不改变信号集合。

设计（见 openspec/changes/index-pivot-signal-research/design.md D4）：
- 每个因子对每个交易日输出【方向感知】的 0–1 得分（bottom / top 两列），得分越高越支持
  该方向的拐点；得分缺失（数据不足或未发布）一律记 NaN，MUST NOT 前向补值。
- 因子只用于给已生成的信号"加分 + 分级（A/B/C）+ 辅助增益对照列"，MUST NOT 参与信号生成，
  MUST NOT 否决价格结构已成立的拐点候选。
- 每个因子登记 P0/P1/P2 可得性分级与出处；外围（隔夜标普500）当前无本地数据源，按 P2 记 NaN。
- 所有分位数用 `swing_indicators.rolling_percentile`（严格向前看），保证 T 日得分只用 T 日及
  之前数据。

本模块的因子装配会读取本地行情/外部数据（只读），打分本身是纯计算。
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from external_data import load_series
from swing_indicators import divergence, rolling_percentile

ROOT = Path(__file__).resolve().parents[1]
ETF_SHARE_SERIES = ROOT / "data" / "raw" / "etf-shares" / "szse-159919.csv"

# 资金/情绪/技术/宽度/外围 —— 名称 → 出处与可得性分级（P0/P1/P2）。
AUX_REGISTRY: dict[str, dict] = {
    "margin_flow": {"category": "资金面", "grade": "P0",
                    "source": "external_data:margin_balance（两融融资余额 20 日变化率分位）",
                    "notes": "去杠杆到位支持底、加杠杆高潮支持顶"},
    "etf_share": {"category": "资金面", "grade": "P1",
                  "source": "data/raw/etf-shares/szse-159919.csv（沪深300ETF 份额 20 日变化率分位）",
                  "notes": "份额历史自 2023-10 起，不足 5 年按 P1；净申购支持底、净赎回支持顶"},
    "qvix": {"category": "情绪面", "grade": "P0",
             "source": "external_data:qvix_50（期权隐含波动率分位）",
             "notes": "高波动恐慌支持底、低波动亢奋支持顶"},
    "divergence": {"category": "技术面", "grade": "P0",
                   "source": "swing_indicators.divergence（RSI/MACD/OBV 顶底背离）",
                   "notes": "复用现有背离口径，底背离支持底、顶背离支持顶"},
    "breadth": {"category": "宽度", "grade": "P1",
                "source": "breadth_table:breadth20（东财行业板块站上 20 日线占比分位）",
                "notes": "宽度极度收缩支持底、极度扩张支持顶"},
    "external_spx": {"category": "外围", "grade": "P2",
                     "source": "隔夜标普500（本地无数据源）",
                     "notes": "无 5 年历史 + 当日已发布数据，按 P2 全程记 NaN，不补值、不作更换结论主增益"},
}

WEIGHT_GRID = (0.05, 0.10, 0.15)


def _direction_scores(rank: pd.Series) -> pd.DataFrame:
    """把 [0,1] 分位序列转成 (bottom=低分位支持底, top=高分位支持顶) 的方向得分。"""
    return pd.DataFrame({"bottom": 1.0 - rank, "top": rank}, index=rank.index)


def _to_value_series(path_or_frame) -> pd.Series:
    if isinstance(path_or_frame, pd.Series):
        series = path_or_frame
    else:
        series = path_or_frame.set_index("date")["value"]
    series.index = pd.to_datetime(series.index)
    return series.astype(float).sort_index()


def score_margin_flow(calendar: pd.DatetimeIndex) -> pd.DataFrame:
    frame = load_series("margin_balance")
    if frame is None:
        return _empty(calendar)
    balance = _to_value_series(frame).reindex(calendar)
    change = balance / balance.shift(20) - 1.0
    rank = rolling_percentile(change, min_periods=60)
    return _direction_scores(rank)


def score_etf_share(calendar: pd.DatetimeIndex) -> pd.DataFrame:
    if not ETF_SHARE_SERIES.exists():
        return _empty(calendar)
    raw = pd.read_csv(ETF_SHARE_SERIES)
    column = "基金份额" if "基金份额" in raw.columns else raw.columns[-1]
    shares = pd.to_numeric(raw[column], errors="coerce")
    series = pd.Series(shares.to_numpy(), index=pd.to_datetime(raw["日期"])).sort_index()
    series = series.groupby(level=0).last().reindex(calendar)
    change = series.pct_change(20)
    rank = rolling_percentile(change, min_periods=60)
    # 份额净申购（高分位）代表逆势加仓，支持底：方向与 margin 相反
    return pd.DataFrame({"bottom": rank, "top": 1.0 - rank}, index=calendar)


def score_qvix(calendar: pd.DatetimeIndex) -> pd.DataFrame:
    frame = load_series("qvix_50")
    if frame is None:
        return _empty(calendar)
    qvix = _to_value_series(frame).reindex(calendar)
    rank = rolling_percentile(qvix, min_periods=60)
    # 高波动支持底
    return pd.DataFrame({"bottom": rank, "top": 1.0 - rank}, index=calendar)


def score_divergence(index_frame: pd.DataFrame) -> pd.DataFrame:
    work = index_frame.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.sort_values("date").reset_index(drop=True)
    table = divergence(work)
    table.index = pd.DatetimeIndex(work["date"])
    return pd.DataFrame({"bottom": table["divergence_bottom"], "top": table["divergence_top"]})


def score_breadth(breadth: pd.DataFrame | None, calendar: pd.DatetimeIndex) -> pd.DataFrame:
    if breadth is None or "breadth20" not in breadth:
        return _empty(calendar)
    aligned = breadth["breadth20"].reindex(calendar)
    rank = rolling_percentile(aligned, min_periods=60)
    return _direction_scores(rank)  # 宽度低分位（收缩）支持底


def score_external_spx(calendar: pd.DatetimeIndex) -> pd.DataFrame:
    return _empty(calendar)  # 无数据源：全程 NaN，不补值


def _empty(calendar: pd.DatetimeIndex) -> pd.DataFrame:
    return pd.DataFrame({"bottom": np.nan, "top": np.nan}, index=calendar)


def build_aux_scores(index_frame: pd.DataFrame, breadth: pd.DataFrame | None = None) -> dict[str, pd.DataFrame]:
    """装配全部辅助因子的方向得分（键=因子名，值=以日历为索引的 bottom/top DataFrame）。"""
    calendar = pd.DatetimeIndex(pd.to_datetime(index_frame["date"])).sort_values()
    return {
        "margin_flow": score_margin_flow(calendar),
        "etf_share": score_etf_share(calendar),
        "qvix": score_qvix(calendar),
        "breadth": score_breadth(breadth, calendar),
        "external_spx": score_external_spx(calendar),
        "divergence": score_divergence(index_frame).reindex(calendar),
    }


def aux_gain_at(scores: dict[str, pd.DataFrame], day: str, kind: str, weights: dict[str, float]) -> dict:
    """某一交易日某方向的辅助增益 = Σ 权重×方向得分；缺失因子按 0 计入、不惩罚也不臆造。"""
    stamp = pd.Timestamp(day)
    contributions: dict[str, float] = {}
    total = 0.0
    for name, weight in weights.items():
        frame = scores.get(name)
        value = np.nan
        if frame is not None and stamp in frame.index:
            value = float(frame.at[stamp, kind])
        if not np.isfinite(value):
            contributions[name] = None  # 缺失记 None，不补值
            continue
        contributions[name] = round(weight * value, 6)
        total += weight * value
    return {"aux_gain": round(total, 6), "contributions": contributions,
            "available_factors": [n for n, c in contributions.items() if c is not None]}


def grade_from_gain(aux_gain: float, thresholds=(0.06, 0.12)) -> str:
    """辅助增益只映射到分级标签（A/B/C），MUST NOT 改变信号是否成立。"""
    low, high = thresholds
    if aux_gain >= high:
        return "A"
    if aux_gain >= low:
        return "B"
    return "C"


def annotate_signals(signals: list[dict], scores: dict[str, pd.DataFrame],
                     weights: dict[str, float], thresholds=(0.06, 0.12)) -> list[dict]:
    """给信号附加 aux_gain / aux_grade / aux_contributions 三列；不改动 date/type/price/数量。

    返回新的记录列表（浅拷贝 + 追加字段），保证辅助只加分不否决、不改信号集合。
    """
    annotated: list[dict] = []
    for signal in signals:
        gain = aux_gain_at(scores, signal["date"], signal["type"], weights)
        record = dict(signal)
        record["aux_gain"] = gain["aux_gain"]
        record["aux_grade"] = grade_from_gain(gain["aux_gain"], thresholds)
        record["aux_contributions"] = gain["contributions"]
        record["aux_available"] = gain["available_factors"]
        annotated.append(record)
    return annotated
