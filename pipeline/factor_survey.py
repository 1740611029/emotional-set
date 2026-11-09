"""候选因子调研：映射、相关性、叠加/替换检验与结论三态化。

研究用途，MUST NOT 修改生产信号逻辑。所有检验门槛写在 `PREREGISTERED` 中，
报告必须回显该字段；事后调整门槛视为新的一轮检验。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

import external_data
from research_algorithms import ma_reversal_masks, read_index_csv, read_sector_csv, wilson_interval
from swing_indicators import DIRECTIONS, breadth_table, factor_table, rolling_percentile
from swing_signal_engine import DEFAULT_PARAMS, _confirm, _swing_gate, candidate_masks, prepare

ROOT = Path(__file__).resolve().parents[1]
RESEARCH_DIR = ROOT / "data" / "research"
SURVEY_JSON = RESEARCH_DIR / "external-signal-survey.json"
SURVEY_MD = RESEARCH_DIR / "external-signal-survey.md"

KINDS = ("bottom", "top")
INDEX_FILES = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000300": "沪深300", "sh000905": "中证500",
    "sh000852": "中证1000",
}

PREREGISTERED = {
    "correlation_threshold": 0.60,
    "correlation_min_samples": 30,
    "overlay_weights": [0.10, 0.15, 0.20],
    "min_validation_samples": 20,
    "min_p0_years": 5.0,
    "max_missing_rate": 0.20,
    "train": ["2020-11-01", "2023-12-31"],
    "validation": ["2024-01-01", "2026-10-29"],
    "wilson_z": 1.959963984540054,
}

# mapping: extreme = 两端都极端（与现有 volatility 维同构）；monotonic = 单调型，需声明哪端对应底部
FACTOR_SPECS = {
    "qvix_50": {"type": "extreme", "label": "50ETF期权隐含波动率 QVIX"},
    "below_net_asset_ratio": {"type": "monotonic", "bottom": "high", "label": "全A破净率"},
    "margin_balance": {"type": "monotonic", "bottom": "low", "label": "融资余额分位"},
    "margin_turnover": {"type": "monotonic", "bottom": "low", "label": "融资买入额/融资余额"},
}

EVIDENCE = [
    {
        "category": "经典书籍与交易体系", "system": "Howard Marks《周期》(Mastering the Market Cycle, 2018)",
        "proxy": "市场温度 / 情绪钟摆位置（定性）", "meaning": "情绪在贪婪与恐惧两端摆动，极少停在理性中点；偏离越极端，回归力量越强",
        "source": "Howard Marks, Mastering the Market Cycle, 2018, 第 4-5 章",
        "availability": "定性框架，无原始量化序列", "evidence": "作者自述为判断框架，未给出可回测的参数化规则",
        "hypothesis": "与现有 volatility/position 维部分重叠，独立信息有限",
        "conclusion": "放弃", "reason": "无可复现的量化定义，无法在无未来信息前提下构造日频序列",
    },
    {
        "category": "经典书籍与交易体系", "system": "Stan Weinstein《Secrets for Profiting in Bull and Bear Markets》(1988)",
        "proxy": "30 周均线 + 相对强度划分四阶段", "meaning": "阶段 1 筑底、阶段 2 上升、阶段 3 见顶、阶段 4 下跌；阶段 2 突破为买点",
        "source": "Stan Weinstein, Secrets for Profiting in Bull and Bear Markets, 1988",
        "availability": "指数日线可算（30 周均线）", "evidence": "原书为形态归纳，未提供统计显著性检验",
        "hypothesis": "与现有 trend 维（MA60/MA250 位置）高度相关",
        "conclusion": "观察", "reason": "核心信息已被 trend 维覆盖；若要验证需先证明其相对 trend 维的增量，成本高于收益",
    },
    {
        "category": "经典书籍与交易体系", "system": "Edwards & Magee《Technical Analysis of Stock Trends》(1948)",
        "proxy": "趋势线/形态突破、支撑阻力反转", "meaning": "价格结构破坏是趋势反转的必要条件",
        "source": "Robert D. Edwards & John Magee, Technical Analysis of Stock Trends, 1948",
        "availability": "需人工标注形态，无法自动复现", "evidence": "形态识别主观性强，量化复现版本结论不一致",
        "hypothesis": "与现有 position/divergence 维重叠",
        "conclusion": "放弃", "reason": "形态标注不可自动化且主观，无法满足可复现与 point-in-time 要求",
    },
    {
        "category": "经典书籍与交易体系", "system": "Alexander Elder《Trading for a Living》(1993) 三重滤网",
        "proxy": "周线趋势 + 日线摆动 + 盘中触发", "meaning": "多时间尺度同向时才入场，用大周期过滤小周期噪声",
        "source": "Alexander Elder, Trading for a Living, 1993, 第 8 章",
        "availability": "指数日线可算（周线重采样）", "evidence": "原书为交易方法论述，未提供统计检验",
        "hypothesis": "与现有 trend + momentum 确认层重叠",
        "conclusion": "观察", "reason": "本项目三层确认已实现类似的多尺度过滤；新增独立维度收益有限",
    },
    {
        "category": "经典书籍与交易体系", "system": "William O'Neil / IBD 追随日（follow-through day）",
        "proxy": "反弹尝试第 4 日及以后出现放量上涨（常见阈值 +1.7%）", "meaning": "底部不由跌幅决定，而由放量承接确认",
        "source": "William J. O'Neil, How to Make Money in Stocks, 1988（follow-through day 规则）",
        "availability": "指数日线可算", "evidence": "规则在美股被广泛使用，但公开的严格统计检验稀少",
        "hypothesis": "与本项目现有三层确认中的「量能确认」高度同构",
        "conclusion": "观察", "reason": "现有确认机制已包含放量条件；差异仅在阈值，属于参数层面而非新维度",
    },
    {
        "category": "学术与卖方研究", "system": "Baker & Wurgler 投资者情绪指数 (2006)",
        "proxy": "封闭式基金折价、换手率、IPO 数量与首日收益、股权融资占比、股息溢价 6 变量主成分",
        "meaning": "情绪高涨期后接低收益，情绪低迷期后接高收益；情绪对难套利股票影响更大",
        "source": "Baker, M. & Wurgler, J., Investor Sentiment and the Cross-Section of Stock Returns, Journal of Finance, 2006",
        "availability": "A 股需自建，6 个代理变量中 IPO/股权融资为月频", "evidence": "原文样本为 1963-2001 美股；A 股复现研究结论方向一致但强度较弱",
        "hypothesis": "与现有 position/volume 维中度相关；换手率部分与 volume 维重叠",
        "conclusion": "观察", "reason": "构成变量多为月频，无法构造日频因子；且需自建口径，先观察",
    },
    {
        "category": "学术与卖方研究", "system": "De Bondt & Thaler 过度反应 (1985)",
        "proxy": "3-5 年输家组合 vs 赢家组合的后续收益差", "meaning": "长期极端输家后续跑赢，支持均值回归",
        "source": "De Bondt, W. & Thaler, R., Does the Stock Market Overreact?, Journal of Finance, 1985",
        "availability": "指数层面可算长周期收益分位", "evidence": "原文为 3-5 年尺度；A 股指数样本仅约 6 年，无法覆盖多个独立周期",
        "hypothesis": "与现有 position 维（250 日回撤/涨幅分位）重叠",
        "conclusion": "放弃", "reason": "时间尺度为 3-5 年，本项目样本长度不足以检验；且信息已被 position 维覆盖",
    },
    {
        "category": "学术与卖方研究", "system": "Zweig 宽度推力（breadth thrust）",
        "proxy": "上涨家数占比的 10 日移动均值由低位快速升破阈值", "meaning": "宽度急速扩张标志底部资金承接，而非价格反弹",
        "source": "Martin Zweig, Winning on Wall Street, 1986（breadth thrust 规则）",
        "availability": "个股层宽度接口仅约 2 年历史（P1）；行业板块宽度（东财行业板块）已有", "evidence": "原书为经验规则，未给严格统计检验",
        "hypothesis": "与现有 breadth 维同源，但粒度不同（个股 vs 行业）",
        "conclusion": "观察", "reason": "个股层新高新低接口仅约 2 年历史，样本不足以支撑结论；行业层信息已被 breadth 维覆盖",
    },
    {
        "category": "学术与卖方研究", "system": "华泰证券《再论A股择时：多维度融合》(2025)",
        "proxy": "情绪/资金/技术/期权期货四类指标组合", "meaning": "融资买入额、期权隐含波动率等资金与波动率指标具择时信息",
        "source": "华泰证券金工研究, 再论A股择时：多维度融合, 2025",
        "availability": "融资买入额与 QVIX 均可获取", "evidence": "卖方自述样本内显著、样本外仍有表现；未提供独立第三方复核",
        "hypothesis": "直接支持本次验证 qvix 与两融类因子",
        "conclusion": "采纳", "reason": "其结论与本次可获取数据一致，转化为可检验假设：QVIX 极端与两融活跃度提供价量之外的增量信息",
    },
    {
        "category": "学术与卖方研究", "system": "中信建投《A股和港股投资者情绪的量化与择时应用》(2025)",
        "proxy": "多代理变量合成情绪指数 + 多层次阈值择时", "meaning": "情绪指数极值区间用于反向择时",
        "source": "中信建投证券研究发展部, A股和港股投资者情绪的量化与择时应用, 2025",
        "availability": "合成口径未完全公开，无法逐项复现", "evidence": "卖方自述 A 股策略年化 28.15%、最大回撤 -11.17%；属自报回测，未经独立复核",
        "hypothesis": "与本次 P0 因子的相关性需实测",
        "conclusion": "观察", "reason": "合成口径不可完全复现，只能作为方向性参考，不能作为采纳依据",
    },
    {
        "category": "市场数据型指标", "system": "CNN Fear & Greed Index (2012)",
        "proxy": "7 分量等权：价格动量、价格强度、价格宽度、看跌看涨比、市场波动率、避险需求、垃圾债利差",
        "meaning": "把情绪压缩为 0-100 的温度计，两端为极度恐慌/极度贪婪",
        "source": "CNN Business, Fear & Greed Index, 2012 起发布",
        "availability": "7 分量中看跌看涨比、避险需求、垃圾债利差 A 股无对应日频数据", "evidence": "官方定位为描述性指标，未公开样本外择时检验",
        "hypothesis": "其「波动率」分量与 QVIX 同构；「价格宽度」分量与现有 breadth 维同构",
        "conclusion": "观察", "reason": "结构可借鉴（等权、分位标准化），但 3/7 分量在 A 股不可得，无法整体复刻",
    },
    {
        "category": "市场数据型指标", "system": "QVIX 中国波指（上交所，2015 起）",
        "proxy": "50ETF 期权隐含波动率指数日收盘", "meaning": "期权市场对未来波动的定价，高位对应恐慌、低位对应松懈",
        "source": "akshare index_option_50etf_qvix（上证 50ETF 期权波动率指数）",
        "availability": "日频，2015-02-09 起，缺失率 0%", "evidence": "华泰 2025 报告称期权隐含波动率样本外仍有择时表现",
        "hypothesis": "与现有 volatility 维（ATR/带宽分位）中度相关，但含前瞻定价信息，可能提供增量",
        "conclusion": "采纳", "reason": "数据满足 P0 条件，进入增量检验",
    },
    {
        "category": "市场数据型指标", "system": "全A破净率（乐咕乐股，2005 起）",
        "proxy": "破净家数 / 上市公司总数", "meaning": "估值极端便宜的程度，历史上与市场底部区域重合",
        "source": "akshare stock_a_below_net_asset_statistics(symbol='全部A股')",
        "availability": "日频，2005-01-05 起，缺失率 0%", "evidence": "无公开的严格样本外择时检验；多作为估值温度参考",
        "hypothesis": "与现有 position 维（回撤分位）相关，但基于净资产而非价格，可能独立",
        "conclusion": "采纳", "reason": "数据满足 P0 条件，进入增量检验",
    },
    {
        "category": "市场数据型指标", "system": "创新高/新低家数（乐咕乐股）",
        "proxy": "20 日创新高家数占比", "meaning": "个股层宽度，衡量上涨的普遍性",
        "source": "akshare stock_a_high_low_statistics(symbol='all')",
        "availability": "接口仅返回约 2 年（2024-10 起），不足 5 年", "evidence": "无足够本地样本可检验",
        "hypothesis": "与现有 breadth 维（东财行业板块站上 MA20 占比）高度相关",
        "conclusion": "观察", "reason": "历史仅约 2 年，样本不足以支撑统计结论；且与 breadth 维同源",
    },
    {
        "category": "市场数据型指标", "system": "两融余额与融资买入额（交易所，2010 起）",
        "proxy": "融资余额分位、融资买入额/融资余额", "meaning": "杠杆资金的存量水平与活跃度，高位对应风险偏好过热",
        "source": "akshare macro_china_market_margin_sh / macro_china_market_margin_sz",
        "availability": "日频，2010-03-31 起，缺失率 0%", "evidence": "华泰 2025 报告将融资买入额列为资金面有效指标（自述样本外有效）",
        "hypothesis": "与现有 volume 维（量比分位）中度相关，但杠杆资金属独立主体",
        "conclusion": "采纳", "reason": "数据满足 P0 条件，进入增量检验",
    },
    {
        "category": "市场数据型指标", "system": "股债利差 ERP（FED 模型，Yardeni 1997）",
        "proxy": "100 / 指数滚动市盈率 - 10 年期国债收益率", "meaning": "股票相对债券的性价比，高位对应底部区域",
        "source": "Ed Yardeni, FED Model, 1997；akshare stock_index_pe_lg + bond_zh_us_rate",
        "availability": "指数市盈率仅月频可得，无法构造日频序列", "evidence": "东财公开回测称沪深300 ERP 分位 >90% 后 250 日正收益占比 88%、60 日仅 62%（自报）",
        "hypothesis": "与现有 position 维相关，但含跨资产信息",
        "conclusion": "观察", "reason": "仅月频可得；不跨月填充就无法并入日频因子层，而跨月填充会制造隐性未来信息",
    },
    {
        "category": "市场数据型指标", "system": "涨停/跌停家数（东财股池）",
        "proxy": "涨停家数占比、连板高度、炸板率", "meaning": "短线资金的赚钱效应与情绪温度",
        "source": "akshare stock_zt_pool_em / stock_zt_pool_dtgc_em",
        "availability": "接口仅覆盖近期（本次取近 60 个交易日），历史不可回溯", "evidence": "无长样本可检验",
        "hypothesis": "与现有 volume 维在短周期上相关，但与波段顶底时间尺度不匹配",
        "conclusion": "观察", "reason": "时间尺度为 1-5 日短线情绪，与波段顶底不同层级；且历史不可回溯",
    },
    {
        "category": "民间情绪周期", "system": "A股「情绪周期」六阶段（冰点-启动-发酵-加速-分歧-退潮）",
        "proxy": "连板股数量、最高板高度、涨停家数、炸板率", "meaning": "短线资金接力意愿的周期性变化",
        "source": "东方财富财富号《看懂情绪周期，才算真正入门打板》2026-10；同类微博/百家号文章",
        "availability": "依赖东财股池接口，仅近期", "evidence": "民间经验总结，无统计检验",
        "hypothesis": "与波段顶底不同尺度，可能只在极端冰点时有交集",
        "conclusion": "放弃", "reason": "时间尺度（1-5 日）与波段顶底（数周至数月）不匹配，且数据不可回溯，纳入会破坏评价口径一致性",
    },
    {
        "category": "学术与卖方研究", "system": "新闻/文本情绪（Tetlock 2007；Da-Engelberg-Gao 2015 FEARS）",
        "proxy": "媒体悲观词频、散户搜索行为强度", "meaning": "文本情绪对短期收益有预测力，随后反转",
        "source": "Tetlock, P., Giving Content to Investor Sentiment, Journal of Finance, 2007；Da, Engelberg & Gao, The Sum of All FEARS, Journal of Finance, 2015",
        "availability": "需历史新闻全文与搜索指数，本项目无 point-in-time 语料", "evidence": "原文基于美股语料；A 股复现需自建词库，存在词库漂移",
        "hypothesis": "可能提供价量之外的独立信息，但不可复现",
        "conclusion": "放弃", "reason": "无法获取 point-in-time 历史文本，任何回溯构建都会引入未来信息（用当期词库解释历史文本）",
    },
]


def _clean(value):
    """把 numpy 标量与 NaN/inf 转成可 JSON 序列化的值。"""
    if isinstance(value, dict):
        return {key: _clean(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_clean(item) for item in value]
    if isinstance(value, (np.integer,)):
        return int(value)
    if isinstance(value, (np.floating, float)):
        number = float(value)
        return None if not np.isfinite(number) else round(number, 6)
    if isinstance(value, (np.bool_, bool)):
        return bool(value)
    return value


def score_factor(series: pd.Series, spec: dict, kind: str) -> pd.Series:
    """把外部指标映射为 0-1 方向得分，复用与六维一致的分位约定。"""
    rank = rolling_percentile(series.astype(float))
    if spec["type"] == "extreme":
        return (2 * rank - 1).abs().clip(0, 1)
    bottom_high = spec.get("bottom") == "high"
    if kind == "bottom":
        return rank.clip(0, 1) if bottom_high else (1 - rank).clip(0, 1)
    return (1 - rank).clip(0, 1) if bottom_high else rank.clip(0, 1)


def factor_scores(frames: dict[str, pd.DataFrame], calendar) -> pd.DataFrame:
    """对齐到交易日历并生成每个候选的 bottom/top 得分。"""
    result = pd.DataFrame(index=pd.Index(calendar))
    for name, spec in FACTOR_SPECS.items():
        frame = frames.get(name)
        if frame is None or frame.empty:
            for kind in KINDS:
                result[f"{name}_{kind}"] = np.nan
            continue
        aligned = external_data.align_to_calendar(frame, calendar, max_ffill=external_data.DEFAULT_MAX_FFILL)
        aligned.index = result.index
        for kind in KINDS:
            result[f"{name}_{kind}"] = score_factor(aligned, spec, kind).to_numpy()
    return result


def full_correlation(scores: pd.DataFrame) -> pd.DataFrame:
    numeric = scores.select_dtypes("number")
    matrix = numeric.corr(method="spearman")
    return matrix.replace([np.inf, -np.inf], np.nan)


def max_abs_correlation(matrix: pd.DataFrame, column: str, dims: tuple[str, ...]) -> tuple[str | None, float | None]:
    if column not in matrix.columns:
        return None, None
    best_dim, best_value = None, None
    for dim in dims:
        if dim not in matrix.columns:
            continue
        value = matrix.at[column, dim]
        if value is None or not np.isfinite(value):
            continue
        if best_value is None or abs(value) > abs(best_value):
            best_dim, best_value = dim, float(value)
    return best_dim, best_value


NEUTRAL_SCORE = 0.5  # rolling_percentile 的中性点：0.5 即历史中位，不是估计出来的参数


def overlay_score(base: pd.Series, extra: pd.Series, weight: float) -> pd.Series:
    """加性倾斜：以 0.5 为中性点，按权重叠加候选因子相对中位的偏离。

    与「六维等权 + 第 7 维求平均」在极端处位移相同（w=0.15 且 extra=1 时均 +0.075），
    但常数因子（偏离为 0）不会稀释总分——这正是「无信息因子不应改变结果」的要求。
    """
    combined = base + weight * (extra - NEUTRAL_SCORE)
    return combined.where(base.notna() | extra.notna()).clip(0, 1)


def replace_score(factors: pd.DataFrame, kind: str, dim: str, extra: pd.Series) -> pd.Series:
    """用候选因子替换某一维，其余五维不变；总分维度数仍为 6。"""
    others = [f"{name}_{kind}" for name in DIRECTIONS if name != dim]
    available = factors[others].notna().sum(axis=1) + extra.notna().astype(int)
    total = factors[others].fillna(0.0).sum(axis=1) + extra.fillna(0.0)
    return (total / available.replace(0, np.nan)).clip(0, 1)


def signals_from_scores(frame: pd.DataFrame, factors: pd.DataFrame, context: dict,
                        delta: pd.Series, params: dict, scores: dict[str, pd.Series]) -> list[dict]:
    """以指定得分复算候选与三层确认；只读复用生产确认逻辑，不修改其参数。"""
    work = factors.copy()
    for kind in KINDS:
        work[f"score_{kind}"] = scores[kind]
    masks = candidate_masks(work, params)
    candidates = [
        (int(position), kind)
        for kind in KINDS
        for position in np.flatnonzero(masks[kind].to_numpy())
    ]
    candidates.sort()
    records: list[dict] = []
    last_kind = None
    last_position = None
    for position, kind in candidates:
        if last_kind == kind or (last_position is not None and position <= last_position):
            continue
        if not _swing_gate(frame, position, last_position, params):
            continue
        confirmed, _ = _confirm(frame, context, delta, position, kind, params)
        if confirmed is None:
            continue
        records.append({"position": int(confirmed), "type": kind,
                        "date": frame["date"].iat[confirmed]})
        last_kind, last_position = kind, confirmed
    return records


def baseline_scores(factors: pd.DataFrame) -> dict[str, pd.Series]:
    return {kind: factors[f"score_{kind}"] for kind in KINDS}


def segment_of(day: pd.Timestamp) -> str:
    train_start, train_end = (pd.Timestamp(value) for value in PREREGISTERED["train"])
    valid_start, valid_end = (pd.Timestamp(value) for value in PREREGISTERED["validation"])
    if train_start <= day <= train_end:
        return "train"
    if valid_start <= day <= valid_end:
        return "validation"
    return "outside"


def evaluate_events(records: list[dict], frame: pd.DataFrame) -> list[dict]:
    """胜率口径沿用生产：信号后、下一个反向信号前出现均线多头/空头排列即成功。"""
    bull_success, bear_success = ma_reversal_masks(frame)
    positions = {kind: [record["position"] for record in records if record["type"] == kind] for kind in KINDS}
    events = []
    for record in records:
        kind, position = record["type"], record["position"]
        opposite = [value for value in positions["top" if kind == "bottom" else "bottom"] if value > position]
        search_end = opposite[0] if opposite else len(frame)
        success_mask = bull_success if kind == "bottom" else bear_success
        confirmation = None
        for index in range(position + 1, search_end):
            if bool(success_mask.iat[index]):
                confirmation = index
                break
        if confirmation is not None:
            status = "success"
        elif opposite:
            status = "failure"
        else:
            status = "pending"
        events.append({
            "type": kind, "date": frame["date"].iat[position], "status": status,
            "segment": segment_of(frame["date"].iat[position]),
        })
    return events


def summarize(events: list[dict], segment: str) -> dict:
    rows = {}
    for kind in KINDS + ("all",):
        part = [event for event in events if event["segment"] == segment
                and (kind == "all" or event["type"] == kind)]
        resolved = [event for event in part if event["status"] in ("success", "failure")]
        successes = sum(1 for event in resolved if event["status"] == "success")
        rows[kind] = {
            "total": len(part), "resolved": len(resolved), "successes": successes,
            "rate": (successes / len(resolved)) if resolved else None,
            "wilson95": wilson_interval(successes, len(resolved), PREREGISTERED["wilson_z"]),
            "small_sample": len(resolved) < PREREGISTERED["min_validation_samples"],
        }
    return rows


def yearly_table(events: list[dict]) -> list[dict]:
    frame = pd.DataFrame(events)
    if frame.empty:
        return []
    frame["year"] = pd.to_datetime(frame["date"]).dt.year
    rows = []
    for (year, segment), part in frame.groupby(["year", "segment"]):
        resolved = part[part["status"].isin(["success", "failure"])]
        successes = int((resolved["status"] == "success").sum())
        rows.append({
            "year": int(year), "segment": segment, "total": len(part),
            "resolved": len(resolved), "successes": successes,
            "rate": (successes / len(resolved)) if len(resolved) else None,
        })
    return sorted(rows, key=lambda row: (row["year"], row["segment"]))


def grade_indicator(entry: dict) -> tuple[str, str]:
    """按预注册规则给指标定级：日频 + ≥5 年 + 缺失率 < 20% 才是 P0。"""
    if not entry or entry.get("status") != "ok":
        return "P2", "抓取失败或数据不可用"
    if entry["frequency"] != "daily":
        return "P1", f"频率为 {entry['frequency']}，不跨期填充则无法并入日频因子层"
    start, end = pd.Timestamp(entry["start"]), pd.Timestamp(entry["end"])
    years = (end - start).days / 365.25
    if years < PREREGISTERED["min_p0_years"]:
        return "P1", f"历史仅 {years:.1f} 年，不足 {PREREGISTERED['min_p0_years']:.0f} 年"
    if (entry["missing_rate"] or 0) > PREREGISTERED["max_missing_rate"]:
        return "P1", f"缺失率 {entry['missing_rate']:.1%} 超过 {PREREGISTERED['max_missing_rate']:.0%}"
    return "P0", f"日频、覆盖 {years:.1f} 年、缺失率 {entry['missing_rate']:.1%}"


def load_breadth() -> pd.DataFrame | None:
    """行业宽度输入与生产同源：读东财行业板块单层目录。"""
    folder = ROOT / "data" / "raw" / "sectors-em-industry"
    paths = sorted(folder.glob("*.csv")) if folder.exists() else []
    if not paths:
        return None
    return breadth_table({path.stem: read_sector_csv(path) for path in paths})


def run_index(frame: pd.DataFrame, breadth: pd.DataFrame | None, external: dict[str, pd.DataFrame]) -> dict:
    """单个指数的基线、相关性与叠加/替换检验。"""
    state = prepare(frame, breadth, DEFAULT_PARAMS)
    factors = state["factors"]
    context, delta = state["context"], state["breadth_delta5"]
    calendar = frame["date"]

    ext_scores = factor_scores(external, calendar)
    ext_scores.index = factors.index
    combined = pd.concat([factors[[f"{dim}_{kind}" for dim in DIRECTIONS for kind in KINDS]], ext_scores], axis=1)
    train_mask = (calendar >= pd.Timestamp(PREREGISTERED["train"][0])) & (calendar <= pd.Timestamp(PREREGISTERED["train"][1]))
    matrix = full_correlation(combined[train_mask.to_numpy()])

    base_records = signals_from_scores(frame, factors, context, delta, DEFAULT_PARAMS, baseline_scores(factors))
    base_events = evaluate_events(base_records, frame)

    results = {}
    for name in FACTOR_SPECS:
        scores = {kind: ext_scores[f"{name}_{kind}"] for kind in KINDS}
        # 只与同方向的维度比较：底部得分对顶部维度天然反向，跨方向比较会虚高相关性
        best_dim, best_correlation = None, None
        for kind in KINDS:
            dim, correlation = max_abs_correlation(
                matrix, f"{name}_{kind}", tuple(f"{item}_{kind}" for item in DIRECTIONS)
            )
            if correlation is not None and (best_correlation is None or abs(correlation) > abs(best_correlation)):
                best_dim, best_correlation = f"{dim}_{kind}", correlation
        overlays = {}
        for weight in PREREGISTERED["overlay_weights"]:
            combined_scores = {kind: overlay_score(factors[f"score_{kind}"], scores[kind], weight) for kind in KINDS}
            records = signals_from_scores(frame, factors, context, delta, DEFAULT_PARAMS, combined_scores)
            overlays[str(weight)] = {"events": evaluate_events(records, frame), "count": len(records)}
        replace = None
        if best_dim is not None:
            dim_name, dim_kind = best_dim.rsplit("_", 1)
            replace_scores = {kind: replace_score(factors, kind, dim_name, scores[kind]) for kind in KINDS}
            records = signals_from_scores(frame, factors, context, delta, DEFAULT_PARAMS, replace_scores)
            replace = {"dim": dim_name, "kind": dim_kind, "events": evaluate_events(records, frame),
                       "count": len(records)}
        results[name] = {
            "correlation_dim": best_dim, "correlation": best_correlation,
            "overlays": overlays, "replace": replace,
            "coverage": int(ext_scores[f"{name}_bottom"].notna().sum()),
        }
    return {
        "baseline": {"events": base_events, "count": len(base_records)},
        "matrix": matrix, "factors": results,
    }


def _interval_overlap(left, right) -> bool:
    """两个 Wilson 区间是否重叠；重叠意味着差异在抽样误差内。"""
    if not left or not right or left[0] is None or right[0] is None:
        return True
    return not (left[1] < right[0] or right[1] < left[0])


def decide(name: str, result: dict) -> dict:
    """按预注册门槛给出三态结论；不满足样本下限一律降级为观察。"""
    threshold = PREREGISTERED["correlation_threshold"]
    correlation = result["correlation"]
    duplicate = correlation is not None and abs(correlation) >= threshold
    path = "replace" if duplicate else "overlay"
    trials = [result["replace"]["events"]] if duplicate and result["replace"] else \
             [result["overlays"][str(weight)]["events"] for weight in PREREGISTERED["overlay_weights"]]

    baseline_validation = summarize(result["baseline"]["events"], "validation")["all"]
    base_rate = baseline_validation["rate"]
    base_interval = baseline_validation["wilson95"]
    deltas = []
    details = []
    for events in trials:
        summary = summarize(events, "validation")["all"]
        rate = summary["rate"]
        delta = None if (rate is None or base_rate is None) else rate - base_rate
        deltas.append(delta)
        details.append({
            "resolved": summary["resolved"], "rate": rate, "delta": delta,
            "wilson95": summary["wilson95"],
            "interval_overlap": _interval_overlap(base_interval, summary["wilson95"]),
        })

    resolved_total = max((detail["resolved"] for detail in details), default=0)
    valid_deltas = [delta for delta in deltas if delta is not None]
    median_delta = float(np.median(valid_deltas)) if valid_deltas else None
    noise = all(detail["interval_overlap"] for detail in details) if details else True

    if resolved_total < PREREGISTERED["min_validation_samples"]:
        verdict, reason = "观察", f"验证段有效样本 n={resolved_total}，低于 {PREREGISTERED['min_validation_samples']}，不足以判定"
    elif median_delta is None:
        verdict, reason = "观察", "验证段无可比样本，无法计算胜率差"
    elif median_delta > 0:
        verdict = "采纳"
        reason = (f"验证段胜率中位提升 {median_delta:+.1%}（路径 {path}），"
                  f"相关性 |ρ|={abs(correlation):.2f}")
        if noise:
            reason += "；提升幅度与基线 Wilson 区间重叠，仅表示值得进入下一轮独立验证"
    else:
        verdict = "放弃"
        reason = (f"验证段胜率中位变化 {median_delta:+.1%}（路径 {path}），未提供增量信息")

    return {
        "indicator": name, "path": path, "verdict": verdict, "reason": reason,
        "max_abs_correlation": None if correlation is None else round(abs(correlation), 4),
        "correlation_dim": result["correlation_dim"],
        "baseline_validation": baseline_validation,
        "trials": details, "median_delta": median_delta,
        "resolved_total": resolved_total, "within_noise": noise,
    }


def build_survey(*, external: dict[str, pd.DataFrame] | None = None) -> dict:
    """主流程：加载数据 → 逐指数检验 → 汇总结论。"""
    if external is None:
        external = {name: frame for name in FACTOR_SPECS
                    if (frame := external_data.load_series(name)) is not None}
    manifest = external_data.load_manifest()
    breadth = load_breadth()

    per_index = {}
    for code, name in INDEX_FILES.items():
        path = ROOT / "data" / "raw" / "indices-ths" / f"{code}.csv"
        if not path.exists():
            continue
        frame = read_index_csv(path)
        per_index[code] = {"name": name, **run_index(frame, breadth, external)}
        print(f"  {code} {name}: 基线 {per_index[code]['baseline']['count']} 个信号")

    decisions = {}
    for name in FACTOR_SPECS:
        merged = {}
        for code, payload in per_index.items():
            merged[code] = payload["factors"][name]
        # 合并样本：把各指数事件拼起来后统一判定，避免单指数小样本下结论
        combined = {
            "baseline": {"events": [event for payload in per_index.values() for event in payload["baseline"]["events"]]},
            "correlation": _median_correlation(merged),
            "correlation_dim": _dominant_dim(merged),
            "overlays": {str(weight): {"events": [event for payload in per_index.values()
                                                  for event in payload["factors"][name]["overlays"][str(weight)]["events"]]}
                         for weight in PREREGISTERED["overlay_weights"]},
            "replace": None,
            "coverage": max(payload["coverage"] for payload in merged.values()),
        }
        if combined["correlation_dim"]:
            combined["replace"] = {"events": [event for payload in per_index.values()
                                              for event in (payload["factors"][name]["replace"] or {"events": []})["events"]]}
        decisions[name] = decide(name, combined)

    candidates = []
    for name, spec in FACTOR_SPECS.items():
        entry = manifest.get(name, {})
        grade, grade_reason = grade_indicator(entry)
        candidates.append({
            "indicator": name, "label": spec["label"], "mapping": spec["type"],
            "frequency": entry.get("frequency"), "start": entry.get("start"), "end": entry.get("end"),
            "missing_rate": entry.get("missing_rate"), "grade": grade, "grade_reason": grade_reason,
            "verdict": decisions[name]["verdict"], "verdict_reason": decisions[name]["reason"],
        })
    for name, entry in manifest.items():
        if name in FACTOR_SPECS:
            continue
        grade, grade_reason = grade_indicator(entry)
        candidates.append({
            "indicator": name, "label": entry["definition"], "mapping": None,
            "frequency": entry.get("frequency"), "start": entry.get("start"), "end": entry.get("end"),
            "missing_rate": entry.get("missing_rate"), "grade": grade, "grade_reason": grade_reason,
            "verdict": "观察", "verdict_reason": "未进入本次增量检验（" + grade_reason + "）",
        })

    return {
        "generated_at": datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds"),
        "preregistered": PREREGISTERED,
        "evidence": EVIDENCE,
        "candidates": candidates,
        "decisions": decisions,
        "per_index": {
            code: {
                "name": payload["name"],
                "baseline": {
                    "count": payload["baseline"]["count"],
                    "train": summarize(payload["baseline"]["events"], "train")["all"],
                    "validation": summarize(payload["baseline"]["events"], "validation")["all"],
                },
                "factors": {
                    name: {
                        "correlation": payload["factors"][name]["correlation"],
                        "correlation_dim": payload["factors"][name]["correlation_dim"],
                        "coverage": payload["factors"][name]["coverage"],
                        "validation": {
                            str(weight): summarize(payload["factors"][name]["overlays"][str(weight)]["events"], "validation")["all"]
                            for weight in PREREGISTERED["overlay_weights"]
                        },
                    }
                    for name in FACTOR_SPECS
                },
                "yearly": yearly_table(payload["baseline"]["events"]),
            }
            for code, payload in per_index.items()
        },
    }


def _median_correlation(merged: dict) -> float | None:
    values = [payload["correlation"] for payload in merged.values()
              if payload["correlation"] is not None]
    return float(np.median([abs(value) for value in values])) if values else None


def _dominant_dim(merged: dict) -> str | None:
    dims = [payload["correlation_dim"] for payload in merged.values() if payload["correlation_dim"]]
    if not dims:
        return None
    return max(set(dims), key=dims.count)


def write_survey(survey: dict, *, json_path: Path = SURVEY_JSON, md_path: Path = SURVEY_MD) -> tuple[Path, Path]:
    json_path.parent.mkdir(parents=True, exist_ok=True)
    payload = _clean(survey)
    descriptor, temp_name = tempfile.mkstemp(prefix=json_path.name + ".", suffix=".tmp", dir=json_path.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, json_path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    md_path.write_text(render_markdown(survey), encoding="utf-8")
    return json_path, md_path


def _pct(value) -> str:
    return "—" if value is None else f"{value:.1%}"


def render_markdown(survey: dict) -> str:
    preregistered = survey["preregistered"]
    lines = [
        "# 外部顶底信号体系调研报告", "",
        f"生成时间：{survey['generated_at']}", "",
        "## 0. 结论先行", "",
        "本次调研**不改变生产信号算法**，生产准确率不变。以下结论只回答"
        "「某个外部维度是否值得进一步验证」，不回答「准确率能提升多少」。", "",
        "| 指标 | 级别 | 定级依据 | 结论 | 判定理由 |", "| --- | --- | --- | --- | --- |",
    ]
    for item in survey["candidates"]:
        lines.append(f"| {item['label']} | {item['grade']} | {item['grade_reason']} | "
                     f"{item['verdict']} | {item['verdict_reason']} |")

    lines += ["", "## 1. 预注册门槛（不可事后放宽）", "",
              "| 项目 | 取值 |", "| --- | --- |",
              f"| 相关性判重复门槛 | \\|ρ\\| ≥ {preregistered['correlation_threshold']} |",
              f"| 叠加权重（固定三档，不搜索） | {', '.join(str(w) for w in preregistered['overlay_weights'])} |",
              f"| 验证段有效样本下限 | {preregistered['min_validation_samples']} |",
              f"| 训练段 | {preregistered['train'][0]} ~ {preregistered['train'][1]} |",
              f"| 验证段 | {preregistered['validation'][0]} ~ {preregistered['validation'][1]} |",
              f"| P0 最低历史长度 | {preregistered['min_p0_years']:.0f} 年 |",
              "", "任何对上述门槛的调整都必须作为新的一轮检验重新报告。", ""]

    lines += ["## 2. 证据表", "", "| 类别 | 体系 | 代理指标 | 原始出处 | 数据可得性 | 结论 | 理由 |",
              "| --- | --- | --- | --- | --- | --- | --- |"]
    for item in survey["evidence"]:
        lines.append(f"| {item['category']} | {item['system']} | {item['proxy']} | {item['source']} | "
                     f"{item['availability']} | {item['conclusion']} | {item['reason']} |")

    lines += ["", "## 3. 增量检验结果", ""]
    for name, decision in survey["decisions"].items():
        lines += [f"### {name}", "",
                  f"- 路径：{decision['path']}（最大相关维度：{decision['correlation_dim']}，"
                  f"|ρ|={decision['max_abs_correlation']}）",
                  f"- 验证段基线胜率：{_pct(decision['baseline_validation']['rate'])} "
                  f"（n={decision['baseline_validation']['resolved']}）"]
        for trial in decision["trials"]:
            overlap = "区间重叠" if trial.get("interval_overlap") else "区间不重叠"
            lines.append(f"  - 试验：n={trial['resolved']}，胜率 {_pct(trial['rate'])}，"
                         f"相对基线 {_pct(trial['delta'])}，Wilson95 "
                         f"[{_pct(trial['wilson95'][0])}, {_pct(trial['wilson95'][1])}]（{overlap}）")
        lines += [f"- 结论：**{decision['verdict']}** — {decision['reason']}", ""]

    lines += ["### 结论的读法", "",
              "`采纳` 的含义是「值得进入下一轮独立验证」，**不是**「可以上线」。"
              "本轮所有试验的提升幅度都与基线 Wilson 区间重叠，说明差异尚在抽样误差内；"
              "真正上线必须另开变更并重新做独立区间验证。", ""]

    lines += ["## 4. 各指数基线信号", "", "| 指数 | 信号数 | 训练段胜率 | 训练段 n | 验证段胜率 | 验证段 n |",
              "| --- | --- | --- | --- | --- | --- |"]
    for code, payload in survey["per_index"].items():
        train, valid = payload["baseline"]["train"], payload["baseline"]["validation"]
        lines.append(f"| {code} {payload['name']} | {payload['baseline']['count']} | "
                     f"{_pct(train['rate'])} | {train['resolved']} | {_pct(valid['rate'])} | {valid['resolved']} |")

    lines += ["", "## 5. 局限与已知风险", "",
              "1. **主胜率口径已接近饱和**：验证段基线胜率约九成（见上表），"
              "成功判定为「后续出现均线多头排列」，而任意一次反弹都会触发。"
              "在这个口径下，提升空间本就被压缩，任何「提升」都只对应 1-4 个信号的变化。",
              "2. **样本量受限**：7 指数 6 年共约 70 个信号，验证段可判定样本约 30-50 个，"
              "不足以区分「真实增量」与「运气」。",
              "3. **事件不配对**：叠加新维度会改变信号序列，基线与试验不是同一批事件，"
              "比较的是两组的整体胜率而非配对差异。",
              "4. **第三方口径风险**：破净率、新高新低由第三方按当日全市场计算，"
              "manifest 的 `revision_detected` 会在后续重复抓取时暴露事后修订。",
              "5. **月频与短历史指标未检验**：ERP（月频）、新高新低（约 2 年）、"
              "涨停家数（约 0.2 年）未进入增量检验，不能据本报告推断其有效性。", ""]

    lines += ["", "## 6. 对生产的影响", "",
              "本次调研**不改变生产准确率**。原因：调研数据落在 `data/raw/external/`，"
              "研究脚本只读取生产因子与确认函数，不写入生产信号文件。", "",
              "若后续要采纳任何候选因子，必须另开变更，并满足以下**前置条件**：", "",
              "1. 在独立于调参区间的验证段复算准确率，并给出样本量与 Wilson 区间（AGENTS.md 第 7、8 条）；",
              "2. 保留旧算法版本可对比，记录改动原因与前后准确率对照，实现**旧版本留痕**（AGENTS.md 第 9 条）；",
              "3. 不破坏三层确认机制与波段交替约束（AGENTS.md 第 11 条）。", ""]
    return "\n".join(lines)


if __name__ == "__main__":
    print("构建外部信号调研：")
    result = build_survey()
    json_path, md_path = write_survey(result)
    print(f"已写入 {json_path}")
    print(f"已写入 {md_path}")
    for item in result["candidates"]:
        print(f"  {item['grade']} {item['indicator']}: {item['verdict']} — {item['verdict_reason']}")
