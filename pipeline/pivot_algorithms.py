"""候选拐点算法（离线调研专用）：全部纯因果实现，绝不读写文件、绝不进入生产。

设计（见 openspec/changes/index-pivot-signal-research/design.md D2）：三种价格结构主干，
统一以"信号确认日"作为信号日期（与现行 index-swing6-v2.0 的确认日语义对齐），
信号价取**确认日收盘价**（可与现行算法同口径比较点位偏差），并额外保留拐点事实字段：

    {date, type, price, algorithm, pivot_date, pivot_price, confirm_offset}

因果性：任一日 T 生成的信号只依赖 T 及之前的数据——每算法都只在"确认条件成立的那一天"
才写出信号，确认条件全部由 ≤ 确认日的行情决定（截断重跑自检见 test_pivot_algorithms.py）。

- pivot-zigzag  ：滚动 ZigZag 极值破位确认（反转幅度达标 + 最小波段间隔），确认滞后=自然滞后；
- pivot-donchian：滚动 N 日高/低极值，突破后 M 日不创新极值才确认该极值为顶/底；
- pivot-fractal ：分形（左右各 K 根 K 线的高低点），右侧第 K 根走完即可确认。
"""
from __future__ import annotations

import pandas as pd

from swing_signal_engine import DEFAULT_PARAMS, layer_state, prepare

KINDS = ("bottom", "top")


def _arrays(frame: pd.DataFrame):
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"])
    work = work.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)
    dates = work["date"]
    close = work["close"].to_numpy(dtype=float)
    high = work["high"].to_numpy(dtype=float)
    low = work["low"].to_numpy(dtype=float)
    return dates, close, high, low


def _record(dates, close, algorithm: str, pivot_index: int, kind: str, confirm_index: int) -> dict:
    return {
        "date": dates.iat[confirm_index].strftime("%Y-%m-%d"),
        "type": kind,
        "price": float(close[confirm_index]),
        "algorithm": algorithm,
        "pivot_date": dates.iat[pivot_index].strftime("%Y-%m-%d"),
        "pivot_price": float(close[pivot_index]),
        "confirm_offset": int(confirm_index - pivot_index),
    }


def zigzag_signals(frame: pd.DataFrame, *, amplitude: float, min_gap: int) -> list[dict]:
    """滚动 ZigZag 拐点确认（与基准集同一结构规则，但作为实时信号在确认日发出）。"""
    dates, close, _high, _low = _arrays(frame)
    n = close.size
    if n < 2 or amplitude <= 0 or min_gap < 1:
        return []

    def seek_extreme(start: int, want: str, prev_idx: int | None):
        ext = close[start]
        ext_idx = start
        for i in range(start + 1, n):
            c = close[i]
            if want == "top":
                if c > ext:
                    ext, ext_idx = c, i
                reversal = (ext - c) / ext if ext > 0 else 0.0
            else:
                if c < ext:
                    ext, ext_idx = c, i
                reversal = (c - ext) / ext if ext > 0 else 0.0
            if reversal >= amplitude and (i - ext_idx) >= 1:
                if prev_idx is not None and ext_idx - prev_idx < min_gap:
                    continue
                return ext_idx, i
        return None

    hi = lo = close[0]
    hi_idx = lo_idx = 0
    seed = None
    for i in range(1, n):
        c = close[i]
        if c > hi:
            hi, hi_idx = c, i
        if c < lo:
            lo, lo_idx = c, i
        top_ready = hi > 0 and (hi - c) / hi >= amplitude and (i - hi_idx) >= 1
        bottom_ready = lo > 0 and (c - lo) / lo >= amplitude and (i - lo_idx) >= 1
        if top_ready and bottom_ready:
            if hi_idx < lo_idx or (hi_idx == lo_idx and (hi - c) / hi >= (c - lo) / lo):
                seed = (hi_idx, "top", i)
            else:
                seed = (lo_idx, "bottom", i)
            break
        if top_ready:
            seed = (hi_idx, "top", i)
            break
        if bottom_ready:
            seed = (lo_idx, "bottom", i)
            break
    if seed is None:
        return []

    confirmed: list[tuple[int, str, int]] = [seed]
    cur_idx, cur_type, _ = seed
    while True:
        want = "bottom" if cur_type == "top" else "top"
        nxt = seek_extreme(cur_idx, want, cur_idx)
        if nxt is None:
            break
        ext_idx, confirm_idx = nxt
        if ext_idx <= cur_idx:
            break
        confirmed.append((ext_idx, want, confirm_idx))
        cur_idx, cur_type = ext_idx, want
    return [_record(dates, close, "pivot-zigzag", idx, kind, conf) for idx, kind, conf in confirmed]


def _alternate(candidates: list[tuple[int, str, float]], n: int, confirm_offset: int,
               dates, close, algorithm: str) -> list[dict]:
    """把 (极值索引, 类型, 极值价) 候选按极值索引升序整理成顶底交替的信号序列。

    严格实时因果：按极值索引（=确认顺序，因偏移量恒定）遍历，遇到与上一发出信号同类的
    候选直接跳过（"先确认者胜"），绝不因为后续出现更极端的同类而回撤/替换已发信号
    （那会用到未来数据）。输出因此天然顶底交替且截断重跑逐条一致。
    """
    records: list[dict] = []
    last_type: str | None = None
    for ext_idx, kind, _ext_price in candidates:
        if last_type is not None and kind == last_type:
            continue
        confirm = min(ext_idx + confirm_offset, n - 1)
        records.append(_record(dates, close, algorithm, ext_idx, kind, confirm))
        last_type = kind
    return records


def donchian_signals(frame: pd.DataFrame, *, window: int, confirm_days: int) -> list[dict]:
    """滚动 N 日极值：新高/新低后连续 M 日不创新极值，则该极值确认为顶/底拐点。"""
    dates, close, high, low = _arrays(frame)
    n = close.size
    if n < window + confirm_days + 1 or window < 2 or confirm_days < 1:
        return []
    candidates: list[tuple[int, str, float]] = []
    for i in range(window - 1, n - confirm_days):
        left_high = high[i - window + 1:i + 1]
        left_low = low[i - window + 1:i + 1]
        right = slice(i + 1, i + confirm_days + 1)
        if high[i] >= left_high.max() - 1e-12 and high[right].max() < high[i]:
            candidates.append((i, "top", float(high[i])))
        if low[i] <= left_low.min() + 1e-12 and low[right].min() > low[i]:
            candidates.append((i, "bottom", float(low[i])))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return _alternate(candidates, n, confirm_days, dates, close, "pivot-donchian")


def fractal_signals(frame: pd.DataFrame, *, k: int) -> list[dict]:
    """分形拐点：左右各 K 根 K 线的高低点均不被超越，右侧走完第 K 根即在确认日发出。"""
    dates, close, high, low = _arrays(frame)
    n = close.size
    if n < 2 * k + 1 or k < 1:
        return []
    candidates: list[tuple[int, str, float]] = []
    for i in range(k, n - k):
        left_high = high[i - k:i]
        right_high = high[i + 1:i + 1 + k]
        if (left_high.size == k and right_high.size == k
                and high[i] > left_high.max() and high[i] > right_high.max()):
            candidates.append((i, "top", float(high[i])))
        left_low = low[i - k:i]
        right_low = low[i + 1:i + 1 + k]
        if (left_low.size == k and right_low.size == k
                and low[i] < left_low.min() and low[i] < right_low.min()):
            candidates.append((i, "bottom", float(low[i])))
    candidates.sort(key=lambda item: (item[0], item[1]))
    return _alternate(candidates, n, k, dates, close, "pivot-fractal")


# 登记：算法名 → (函数, 参数取值范围, 数据依赖)。参数只在训练段择 Top-1 冻结（见 PREREGISTERED）。
def _normalize_frame(frame: pd.DataFrame) -> pd.DataFrame:
    work = frame.copy()
    work["date"] = pd.to_datetime(work["date"])
    return work.dropna(subset=["close"]).sort_values("date").reset_index(drop=True)


def _three_layer_confirm(work: pd.DataFrame, state: dict, candidate: int, kind: str,
                         start: int, window: int) -> tuple[int | None, list[dict]]:
    """从 start（含）往后最多 window 个交易日寻找三层同时通过的确认日；只读 ≤确认日数据。

    start 传入 zigzag 自然确认日：拐点只有在那一刻才"可知"，因此三层确认不得早于它，
    否则会用到 start 之前尚不可知的拐点信息（未来函数）。三层判定直接复用生产
    `swing_signal_engine.layer_state`，保持价格→动量→量能三层机制完整。
    """
    config, context, delta = state["params"], state["context"], state["breadth_delta5"]
    log: list[dict] = []
    for offset in range(0, window + 1):
        position = start + offset
        if position >= len(work):
            break
        layers = layer_state(work, context, delta, position, kind, candidate, config)
        log.append({
            "date": work["date"].iat[position].strftime("%Y-%m-%d"), "offset": offset,
            "price": layers["price"], "momentum": layers["momentum"], "volume": layers["volume"],
            "passed": int(layers["price"]) + int(layers["momentum"]) + int(layers["volume"]),
        })
        if layers["price"] and layers["momentum"] and layers["volume"]:
            return position, log
    return None, log


def zigzag_confirm3_signals(frame: pd.DataFrame, *, amplitude: float, min_gap: int,
                            breadth: pd.DataFrame | None = None, params: dict | None = None
                            ) -> tuple[list[dict], list[dict]]:
    """变体：对 pivot-zigzag 候选叠加现行三层确认（价格→动量→量能）。

    返回 (信号列表, 被作废候选日志)；确认失败的候选不进信号集但留日志（design D3/任务 3.3）。
    因果性：信号日 = max(zigzag 自然确认日, 其后三层确认通过日)，只用 ≤ 信号日数据。
    """
    work = _normalize_frame(frame)
    config = {**DEFAULT_PARAMS, **(params or {})}
    window = int(config["confirm_window"])
    state = prepare(work, breadth, config)
    lookup = {day.strftime("%Y-%m-%d"): position for position, day in enumerate(work["date"])}
    signals: list[dict] = []
    dropped: list[dict] = []
    for pivot in zigzag_signals(work, amplitude=amplitude, min_gap=min_gap):
        candidate = lookup[pivot["pivot_date"]]
        natural_confirm = lookup[pivot["date"]]  # zigzag 自然确认日 = 拐点"可知"之日
        position, log = _three_layer_confirm(work, state, candidate, pivot["type"],
                                             natural_confirm, window)
        if position is None:
            dropped.append({"pivot_date": pivot["pivot_date"], "type": pivot["type"],
                            "natural_confirm_date": pivot["date"], "reason": "three_layer_unconfirmed",
                            "confirmation": log})
            continue
        signals.append({
            "date": work["date"].iat[position].strftime("%Y-%m-%d"), "type": pivot["type"],
            "price": float(work["close"].iat[position]), "algorithm": "pivot-zigzag+confirm3",
            "pivot_date": pivot["pivot_date"], "pivot_price": pivot["pivot_price"],
            "confirm_offset": int(position - candidate), "confirmation": log,
        })
    return signals, dropped


ALGORITHMS: dict[str, dict] = {
    "pivot-zigzag": {
        "func": zigzag_signals,
        "definition": "滚动 ZigZag 极值破位确认：反向运动达 amplitude 且与上一拐点间隔≥min_gap 时确认",
        "param_grid": {"amplitude": (0.03, 0.05, 0.07), "min_gap": (8, 13, 21)},
        "data_deps": ("指数日线 OHLCV",),
    },
    "pivot-donchian": {
        "func": donchian_signals,
        "definition": "N 日新高/新低后连续 M 日不创新极值，确认该极值为顶/底拐点",
        "param_grid": {"window": (20, 55), "confirm_days": (3, 5)},
        "data_deps": ("指数日线 OHLCV",),
    },
    "pivot-fractal": {
        "func": fractal_signals,
        "definition": "分形：左右各 K 根 K 线高低点均不被超越，右侧走完即在确认日发出",
        "param_grid": {"k": (2, 3, 5)},
        "data_deps": ("指数日线 OHLCV",),
    },
    # 变体：对 zigzag 候选叠加三层确认；func 返回 (signals, dropped_log)，供 Section 4 对照。
    "pivot-zigzag+confirm3": {
        "func": zigzag_confirm3_signals,
        "definition": "pivot-zigzag 候选叠加现行三层确认（价格→动量→量能），确认失败候选作废",
        "param_grid": {"amplitude": (0.03, 0.05, 0.07), "min_gap": (8, 13, 21)},
        "data_deps": ("指数日线 OHLCV", "行业板块宽度 breadth"),
        "returns_dropped_log": True,
    },
}


def run_algorithm(name: str, frame: pd.DataFrame, **params) -> list[dict]:
    """按登记的算法名与参数生成信号记录（统一确认日语义）。

    变体 pivot-zigzag+confirm3 的 func 返回 (signals, dropped_log)，其余返回 signals 列表。
    """
    if name not in ALGORITHMS:
        raise KeyError(f"未登记的候选算法：{name}")
    return ALGORITHMS[name]["func"](frame, **params)


# 训练段（2020-10-30~2024-12-31, 7 指数）冻结参数。主指标 = 训练段主达标率 Top-1（并列以捕捉率破平），
# 使用 M=5/N=8/cap=5 的冻结窗口；辅助因子权重按【判别力】择定（hit 组均分 − miss 组均分）。
# 验证段（2025-01-01 起）只报告、MUST NOT 再择参。
PREREGISTERED: dict = {
    "determined_on": ("train 2020-10-30~2024-12-31, 7 indices, 139 training pivots; "
                      "objective=train-segment main_hit Top-1 (tie by capture) under frozen windows M=5/N=8/cap=5"),
    "algorithm_params": {
        "pivot-zigzag": {"amplitude": 0.05, "min_gap": 8,
                         "train_main_hit": 0.388, "train_capture": 0.381},
        "pivot-donchian": {"window": 20, "confirm_days": 3,
                           "train_main_hit": 0.360, "train_capture": 0.324},
        "pivot-fractal": {"k": 2, "train_main_hit": 0.942, "train_capture": 0.921},
        # 变体沿用 zigzag 主干参数，额外叠加三层确认
        "pivot-zigzag+confirm3": {"amplitude": 0.05, "min_gap": 8},
    },
    # 辅助因子权重（{0.05,0.10,0.15}）：d = mean(命中拐点的信号因子分) − mean(未命中信号因子分)，
    # 映射规则：d>=0.10 -> 0.15；0.03<=d<0.10 -> 0.10；其余/无数据 -> 0.05。
    "aux_weights": {"margin_flow": 0.15, "breadth": 0.15, "etf_share": 0.10,
                    "qvix": 0.10, "divergence": 0.10, "external_spx": 0.05},
    "aux_discrimination": {"margin_flow": 0.202, "breadth": 0.173, "etf_share": 0.090,
                           "qvix": 0.052, "divergence": 0.035, "external_spx": 0.0},
    # 重要口径提醒（写入供报告与用户判读）：捕捉率/主达标率以基准拐点为分母，是召回型指标，
    # 不惩罚过量信号；pivot-fractal k=2 高频触发导致召回虚高，zigzag 因确认日自然滞后而偏低。
    # 最终对照报告 MUST 并列信号量与精确度（命中信号数/总信号数），否则召回型高分会误导结论。
    "caveat": ("capture/main_hit are pivot-denominated recall and do NOT penalize over-signaling; "
               "report MUST show signal_count and precision alongside"),
}


def frozen_params(name: str) -> dict:
    """返回某算法的冻结参数（去掉证据字段）。"""
    record = dict(PREREGISTERED["algorithm_params"][name])
    for key in ("train_main_hit", "train_capture"):
        record.pop(key, None)
    return record
