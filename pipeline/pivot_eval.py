"""拐点口径评价协议：信号集 × 真实拐点基准集 → 四类指标 + 训练/验证段切分（含 embargo）。

设计要点（见 openspec/changes/index-pivot-signal-research/design.md D5）：
- 四类指标 MUST 同时输出：
  (a) 捕捉率——基准拐点在 ±M 交易日窗口内被同类信号覆盖的比例；
  (b) 滞后分布——(匹配)信号确认日相对拐点日的交易日数（中位数、P90）；
  (c) 点位偏差——|信号价 − 拐点价| / 拐点价分布（中位数）；
  (d) 主达标率——拐点被"及时确认"的信号命中的比例：存在同类信号落在
      [拐点日 − N 交易日, 拐点日 + CONFIRMATION_CAP 交易日] 内。
- 主达标口径的非对称窗（提前量 ≤ N、确认滞后 ≤ 5）与捕捉率的对称 ±M 窗度量不同事物：
  捕捉率只看"信号在不在拐点附近"，主达标率进一步要求"确认是否足够及时"。
- 训练段/验证段 MUST NOT 重叠，并保留不少于评价最长前瞻窗口的隔离期（embargo）。
- 每段结果附样本量、统计区间与 Wilson 95% 区间；已判定样本 < 30 标注小样本。
- N、M 在训练段确定并冻结（见 `FROZEN_WINDOWS` 与 `window_evidence`），验证段代码路径只报告、不再择参。

纯计算模块，不做任何文件读写。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from research_algorithms import wilson_interval

KINDS = ("bottom", "top")
SMALL_SAMPLE = 30
CONFIRMATION_CAP = 5          # 主达标口径：信号确认相对拐点的最大滞后（交易日）
M_CANDIDATES = (3, 5, 8)      # 捕捉率对称窗口候选（交易日）
N_CANDIDATES = (5, 8, 10)     # 主达标提前量候选（交易日）

# 训练段（2020-10-30~2024-12-31）冻结的评价窗口。确定依据是【基准集结构 + "定位拐点"
# 的业务语义】，与任何候选（含现行算法）的表现无关，因此对所有候选一视同仁、无循环论证：
#   - 基准 min_gap=8 交易日 ⇒ 同类拐点至少相隔 ~16 交易日；
#   - M=5：对称 ±5 交易日（约一个自然周）的"贴近转折"容差，2*M=10 < 16 保证相邻同类拐点
#     的捕捉窗互不重叠，不会把一个信号同时记到两个拐点账上；
#   - N=8：主达标允许信号最多提前 8 交易日（正好一个最小波段）预判拐点，最多滞后
#     CONFIRMATION_CAP=5 确认；提前量 < 16 不会串到上一同类拐点。
# 参考实测（window_evidence，仅登记不作择参依据）：现行 index-swing6-v2.0 在训练段 139 个
# 拐点上，最近同类信号偏移中位数 |offset|≈83 交易日、捕捉率(M=5) 仅 7.9%——印证现行算法
# 本质是"情绪/极端度共振"而非拐点定位器，故窗口 MUST NOT 按其偏移分布反推（否则会得到
# 荒谬的 ±80 日窗）。
FROZEN_WINDOWS = {
    "M": 5,
    "N": 8,
    "confirmation_cap": CONFIRMATION_CAP,
    "determined_on": ("train 2020-10-30~2024-12-31; basis=benchmark structure "
                      "(min_gap=8d, same-type pivot spacing>=16d) + near-turn semantics; "
                      "M=5 (2M<16 no overlap), N=8 (=1 min swing); NOT tuned on incumbent "
                      "(incumbent train capture@M5=7.9%, median|offset|~83d)"),
}


def trading_positions(frame: pd.DataFrame) -> dict[str, int]:
    """把日线里的交易日映射为行位置（交易日序号），供 ±M/±N 窗口按交易日计算。"""
    dates = pd.to_datetime(frame["date"]).dt.strftime("%Y-%m-%d")
    return {day: position for position, day in enumerate(dates)}


def _positions(records: list[dict], positions: dict[str, int]) -> list[tuple[int, dict]]:
    """把带 date 的记录转成 (位置, 记录)；忽略行情中不存在的日期。"""
    out = []
    for record in records:
        pos = positions.get(record["date"])
        if pos is not None:
            out.append((pos, record))
    return out


def _wilson(successes: int, total: int) -> dict:
    return {
        "hits": int(successes), "total": int(total),
        "rate": (successes / total) if total else None,
        "wilson95": wilson_interval(successes, total),
        "small_sample": bool(total < SMALL_SAMPLE),
    }


def _percentile(values: list[float], q: float) -> float | None:
    clean = [value for value in values if value is not None and np.isfinite(value)]
    return float(np.percentile(clean, q)) if clean else None


def evaluate_segment(pivots: list[dict], signals: list[dict], positions: dict[str, int], *,
                     M: int, N: int, confirmation_cap: int = CONFIRMATION_CAP) -> dict:
    """对给定拐点集与信号集计算四类指标（拐点为分母，逐类匹配同类型）。"""
    pivot_items = _positions(pivots, positions)
    signal_items = _positions(signals, positions)
    by_kind: dict[str, list[tuple[int, dict]]] = {kind: [] for kind in KINDS}
    for pos, record in signal_items:
        if record["type"] in by_kind:
            by_kind[record["type"]].append((pos, record))
    by_kind = {kind: sorted(items, key=lambda item: item[0]) for kind, items in by_kind.items()}
    pivots_by_kind: dict[str, list[int]] = {kind: [] for kind in KINDS}
    for pos, pivot in pivot_items:
        if pivot["type"] in pivots_by_kind:
            pivots_by_kind[pivot["type"]].append(pos)

    captured_lags: list[float] = []
    price_devs: list[float] = []
    capture_hits = 0
    main_hits = 0
    total_pivots = len(pivot_items)
    for pos, pivot in pivot_items:
        candidates = by_kind.get(pivot["type"], [])
        within_capture = [(s, rec) for s, rec in candidates if abs(s - pos) <= M]
        if within_capture:
            capture_hits += 1
            nearest_pos, nearest_rec = min(within_capture, key=lambda item: abs(item[0] - pos))
            captured_lags.append(float(nearest_pos - pos))
            if pivot["price"]:
                price_devs.append(abs(nearest_rec["price"] - pivot["price"]) / abs(pivot["price"]))
        if any((pos - N) <= s <= (pos + confirmation_cap) for s, _ in candidates):
            main_hits += 1
    # 精确度（信号侧分母）：同类拐点 ±M 日内有信号覆盖的信号占比；
    # 与捕捉率（拐点侧分母）互补，用于惩罚过量触发（召回型指标不惩罚滥发信号）。
    signal_hits = sum(
        1 for pos, record in signal_items
        if record["type"] in pivots_by_kind
        and any(abs(p - pos) <= M for p in pivots_by_kind[record["type"]])
    )

    return {
        "pivot_samples": total_pivots,
        "signal_count": len(signal_items),
        "capture": _wilson(capture_hits, total_pivots),
        "main_hit": _wilson(main_hits, total_pivots),
        "precision": _wilson(signal_hits, len(signal_items)),
        "lag": {
            "median": _percentile(captured_lags, 50),
            "p90": _percentile(captured_lags, 90),
            "counted": len(captured_lags),
            "values": [float(value) for value in captured_lags],
        },
        "price_deviation": {
            "median": _percentile(price_devs, 50),
            "p90": _percentile(price_devs, 90),
            "counted": len(price_devs),
            "values": [float(value) for value in price_devs],
        },
    }


def window_evidence(pivots: list[dict], signals: list[dict], positions: dict[str, int], *,
                    m_candidates=M_CANDIDATES, n_candidates=N_CANDIDATES,
                    confirmation_cap: int = CONFIRMATION_CAP) -> dict:
    """训练段窗口择参证据（纯计算）：逐拐点最近同类信号偏移分布 + 候选窗的捕捉/主达标率。

    用于把 N、M 的冻结值落在可复现的依据上（只喂训练段拐点与信号，验证段不参与）；
    对候选算法不作专门优化——证据函数与 `evaluate_segment` 共用同一套窗口语义。
    """
    signal_items = _positions(signals, positions)
    by_kind: dict[str, list[int]] = {kind: [] for kind in KINDS}
    for pos, record in signal_items:
        if record["type"] in by_kind:
            by_kind[record["type"]].append(pos)
    pivot_items = _positions(pivots, positions)
    offsets: list[float] = []
    capture = {m: {"hits": 0, "total": 0} for m in m_candidates}
    main = {n: {"hits": 0, "total": 0} for n in n_candidates}
    for pos, pivot in pivot_items:
        candidates = by_kind.get(pivot["type"], [])
        for m in m_candidates:
            capture[m]["total"] += 1
            if any(abs(s - pos) <= m for s in candidates):
                capture[m]["hits"] += 1
        for n in n_candidates:
            main[n]["total"] += 1
            if any((pos - n) <= s <= (pos + confirmation_cap) for s in candidates):
                main[n]["hits"] += 1
        if candidates:
            nearest = min(candidates, key=lambda s: abs(s - pos))
            offsets.append(float(nearest - pos))
    absolute = [abs(value) for value in offsets]
    return {
        "pivot_samples": len(pivot_items),
        "signal_count": len(signal_items),
        "matched": len(offsets),
        "offset": {
            "median": _percentile(offsets, 50),
            "abs_median": _percentile(absolute, 50),
            "abs_p75": _percentile(absolute, 75),
            "abs_p90": _percentile(absolute, 90),
            "lead_share": (sum(1 for value in offsets if value < 0) / len(offsets)) if offsets else None,
        },
        "capture_by_M": {str(m): {**capture[m], "rate": (capture[m]["hits"] / capture[m]["total"]) if capture[m]["total"] else None}
                          for m in m_candidates},
        "main_hit_by_N": {str(n): {**main[n], "rate": (main[n]["hits"] / main[n]["total"]) if main[n]["total"] else None}
                           for n in n_candidates},
    }


def assign_segment(day: str, *, train_start: pd.Timestamp, train_end: pd.Timestamp,
                   last_valid_date: pd.Timestamp, embargo_end: pd.Timestamp) -> str:
    """标注拐点/信号所属段：train（截止 embargo 前）、excluded（隔离带）、validation、outside。"""
    stamp = pd.Timestamp(day)
    if stamp < train_start:
        return "outside"
    if stamp <= embargo_end:
        return "train"
    if stamp <= train_end:
        return "excluded"  # embargo 隔离带，两段都不计入
    if stamp <= last_valid_date:
        return "validation"
    return "outside"


def embargo_boundary(train_end: pd.Timestamp, positions: dict[str, int], frame: pd.DataFrame,
                     embargo_days: int) -> pd.Timestamp:
    """从训练段末日往回退 embargo_days 个交易日，返回 train/validation 的实际切分上界。"""
    dates = pd.to_datetime(frame["date"])
    train_positions = [pos for pos, day in enumerate(dates) if day <= train_end]
    if not train_positions:
        return train_end
    cut = train_positions[-1 - embargo_days] if embargo_days < len(train_positions) else train_positions[0]
    return dates.iat[cut]


def evaluate_signal_set(pivots: list[dict], signals: list[dict], frame: pd.DataFrame, *,
                        train_start: pd.Timestamp, train_end: pd.Timestamp,
                        windows: dict | None = None, embargo_days: int | None = None) -> dict:
    """逐指数完整评价：按段（train/validation/excluded）切分拐点后分别计算四类指标。

    pivot 的分段以拐点日为准；某段的评价只使用该段内的信号，避免跨段借信息。
    """
    windows = windows or FROZEN_WINDOWS
    M, N, cap = windows["M"], windows["N"], windows["confirmation_cap"]
    positions = trading_positions(frame)
    last_valid_date = pd.to_datetime(frame["date"]).max()
    embargo = embargo_days if embargo_days is not None else max(M, cap)
    cut = embargo_boundary(train_end, positions, frame, embargo)

    def segment_pivots(name: str) -> list[dict]:
        return [pivot for pivot in pivots if assign_segment(
            pivot["date"], train_start=train_start, train_end=train_end,
            last_valid_date=last_valid_date, embargo_end=cut) == name]

    def segment_signals(name: str) -> list[dict]:
        return [record for record in signals if assign_segment(
            record["date"], train_start=train_start, train_end=train_end,
            last_valid_date=last_valid_date, embargo_end=cut) == name]

    result = {"windows": {"M": M, "N": N, "confirmation_cap": cap},
              "embargo_days": int(embargo), "segments": {}}
    for name in ("train", "validation"):
        piv = segment_pivots(name)
        sig = segment_signals(name)
        metrics = evaluate_segment(piv, sig, positions, M=M, N=N, confirmation_cap=cap)
        days = sorted(row["date"] for row in piv)
        metrics["interval"] = {"start": days[0] if days else None, "end": days[-1] if days else None}
        result["segments"][name] = metrics
    return result


def build_sensitivity(frames: dict[str, pd.DataFrame], benchmark_by_amplitude: dict[float, dict[str, list[dict]]],
                      signals_by_code: dict[str, list[dict]], *, train_start: pd.Timestamp,
                      train_end: pd.Timestamp, windows: dict | None = None,
                      embargo_days: int | None = None) -> dict:
    """同一信号集在 3%/5%/7% 三档基准阈值下的主达标/捕捉对照（报告敏感性节）。"""
    windows = windows or FROZEN_WINDOWS
    tiers = {}
    for amplitude, benchmark in benchmark_by_amplitude.items():
        merged = {"train": {"capture_hits": 0, "capture_total": 0, "main_hits": 0, "main_total": 0},
                  "validation": {"capture_hits": 0, "capture_total": 0, "main_hits": 0, "main_total": 0}}
        for code, pivots in benchmark.items():
            if code not in frames:
                continue
            per = evaluate_signal_set(pivots, signals_by_code.get(code, []), frames[code],
                                      train_start=train_start, train_end=train_end,
                                      windows=windows, embargo_days=embargo_days)
            for seg in ("train", "validation"):
                merged[seg]["capture_hits"] += per["segments"][seg]["capture"]["hits"]
                merged[seg]["capture_total"] += per["segments"][seg]["capture"]["total"]
                merged[seg]["main_hits"] += per["segments"][seg]["main_hit"]["hits"]
                merged[seg]["main_total"] += per["segments"][seg]["main_hit"]["total"]
        tiers[f"{amplitude:.0%}"] = {
            seg: {"capture": _wilson(merged[seg]["capture_hits"], merged[seg]["capture_total"]),
                  "main_hit": _wilson(merged[seg]["main_hits"], merged[seg]["main_total"])}
            for seg in ("train", "validation")
        }
    return tiers
