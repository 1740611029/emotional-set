"""双类底部信号研究：结构底（分 3 组定参）+ 情绪底（宽度/背离/超卖共振）。

用户 2026-11-09 确认的方案二：
- 每指数并行两类底部信号 —— 结构底、情绪底，前端用不同标记区分，共振时高亮；
- 指数按波动分 3 组（steady/mid/active），每组共用一套结构底参数，防逐指数过拟合；
- 参数只在训练段（2020-10-30~2024-12-31）选定，验证段（2025-01 起）只报告。

结构底 = 自适应幅度大波段极值 → 1~3 日三层确认（可选 MA60 趋势确认，按组定参）。
情绪底 = 宽度冰点（breadth 极低）+ 底背离（divergence 高）+ 超卖（position 极低）三者共振，
         在局部低点处触发，独立于价格结构，作为第二类互补信号。

纯计算模块，只在 __main__ 写 data/research/，不触碰任何生产产物。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipeline") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipeline"))

import precision_config as pc  # noqa: E402
from pivot_benchmark import annotate_pivots  # noqa: E402
from research_algorithms import wilson_interval  # noqa: E402
from swing_indicators import factor_table  # noqa: E402
from swing_signal_engine import prepare  # noqa: E402
from research_bigwave_pivot import (  # noqa: E402
    CONFIRM_WINDOW, EMBARGO_END, GAP_GRID, K_GRID, TRAIN_END, TRAIN_START,
    VALID_START, annual_vol, evaluate_position, load_breadth, load_frames,
    segment_of, three_layer_confirm,
)

RESEARCH_DIR = ROOT / "data" / "research"

GROUPS = {
    "steady": ["sh000001", "sh000300"],            # 低波动
    "mid": ["sz399001", "sh000905", "sh000852"],   # 中波动
    "active": ["sz399006", "sh000688"],            # 高波动
}
GROUP_LABEL = {"steady": "稳健组", "mid": "中波动组", "active": "活跃组"}

# 情绪底：必须出现底背离（divergence 高），再叠加「超卖」或「宽度冰点」其一，
# 且当日是局部低点；因子本身是滚动分位、跨指数可比，故不分组。
E_POSITION = 0.90
E_DIVERGENCE = 2 / 3          # 三个背离源中至少 2 个（必要条件）
E_BREADTH = 0.65              # breadth_bottom 上界约 0.85，取 P90 附近作「冰点」
E_LOCAL_WINDOW = 5            # 局部低点：信号日是近 5 日最低收盘
E_COOLDOWN = 30               # 情绪底去重窗口（交易日）

MAX_SIGNALS_PER_YEAR = 2.5    # 结构底每指数每年上限（极少而精）


def _dedup(positions: list[int], cooldown: int) -> list[int]:
    keep: list[int] = []
    last = None
    for p in positions:
        if last is None or p - last > cooldown:
            keep.append(p)
            last = p
    return keep


def _trend_ok(frame: pd.DataFrame, ma60: pd.Series, position: int, kind: str) -> bool:
    if position >= len(frame) or not np.isfinite(ma60.iat[position]):
        return False
    close = float(frame["close"].iat[position])
    return bool(close < float(ma60.iat[position])) if kind == "top" else bool(close > float(ma60.iat[position]))


def _train_slice(frame: pd.DataFrame) -> pd.DataFrame:
    part = frame[(frame["date"] >= TRAIN_START) & (frame["date"] <= TRAIN_END)]
    return part.reset_index(drop=True)


def choose_group_structure(codes: list[str], frames: dict[str, pd.DataFrame],
                           breadth: pd.DataFrame) -> dict:
    """训练段网格：为整组选一套 (k, min_gap, use_ma60)，目标方向正确率+大波段命中率，约束信号密度。"""
    years = (TRAIN_END - TRAIN_START).days / 365.25
    # 预计算训练段三层上下文（与 k/gap 无关，避免重复）
    train = {code: _train_slice(frames[code]) for code in codes}
    vol = {code: annual_vol(frames[code], TRAIN_START, TRAIN_END) for code in codes}
    state = {code: prepare(train[code], breadth, {"confirm_window": CONFIRM_WINDOW}) for code in codes}
    ma60 = {code: train[code]["close"].rolling(60).mean() for code in codes}

    records = []
    for k in K_GRID:
        for gap in GAP_GRID:
            for use_ma60 in (False, True):
                evals = []
                for code in codes:
                    frame = train[code]
                    amp = k * vol[code]
                    for pivot in annotate_pivots(frame, amplitude=amp, min_gap=gap):
                        if pivot["type"] != "bottom":
                            continue
                        pos, _ = three_layer_confirm(
                            frame, state[code], pivot["index"], "bottom", pivot["confirm_index"])
                        if pos is None:
                            continue
                        if use_ma60 and not _trend_ok(frame, ma60[code], pos, "bottom"):
                            continue
                        price = float(frame["close"].iat[pos])
                        ev = evaluate_position(frame, pos, "bottom", price, amp)
                        evals.append(ev)
                n = len(evals)
                per_year = n / (len(codes) * years) if years > 0 else 0.0
                resolved = [e for e in evals if e["horizons"]["h20"]["status"] == "resolved"]
                dir_ok = [e for e in resolved if e["horizons"]["h20"]["pass"]]
                big = [e for e in evals if e["big_hit"] is not None]
                big_ok = [e for e in big if e["big_hit"]]
                dir_rate = len(dir_ok) / len(resolved) if resolved else 0.0
                big_rate = len(big_ok) / len(big) if big else 0.0
                score = 0.6 * dir_rate + 0.4 * big_rate
                records.append({
                    "k": k, "min_gap": gap, "use_ma60": use_ma60,
                    "signal_count": n, "per_year": round(per_year, 3),
                    "dir_rate": round(dir_rate, 4), "big_rate": round(big_rate, 4),
                    "score": round(score, 4), "samples": len(resolved),
                })
    in_budget = [r for r in records if r["per_year"] <= MAX_SIGNALS_PER_YEAR and r["signal_count"] > 0]
    pool = in_budget or [r for r in records if r["signal_count"] > 0] or records
    best = max(pool, key=lambda r: (r["score"], r["samples"]))
    return {"chosen": best, "grid": records, "vol": {c: round(vol[c], 4) for c in codes}}


def structure_signals(code: str, frame: pd.DataFrame, breadth: pd.DataFrame,
                      params: dict) -> list[dict]:
    """全段结构底信号：用组定参生成，评价分 train/validation。"""
    frame = frame.reset_index(drop=True)
    amp = float(params["k"]) * annual_vol(frame, TRAIN_START, TRAIN_END)
    state = prepare(frame, breadth, {"confirm_window": CONFIRM_WINDOW})
    ma60 = frame["close"].rolling(60).mean()
    out = []
    for pivot in annotate_pivots(frame, amplitude=amp, min_gap=int(params["min_gap"])):
        if pivot["type"] != "bottom":
            continue
        pos, log = three_layer_confirm(frame, state, pivot["index"], "bottom", pivot["confirm_index"])
        if pos is None:
            continue
        if params["use_ma60"] and not _trend_ok(frame, ma60, pos, "bottom"):
            continue
        date = frame["date"].iat[pos]
        price = float(frame["close"].iat[pos])
        rec = {
            "signal_type": "structure",
            "date": date.strftime("%Y-%m-%d"),
            "segment": segment_of(date),
            "price": price,
            "pivot_date": pivot["date"],
            "pivot_price": float(pivot["price"]),
            "lag_from_pivot": int(pos - int(pivot["index"])),
            "amplitude": round(amp, 5),
            "confirmation": log,
        }
        rec.update(evaluate_position(frame, pos, "bottom", price, amp))
        out.append(rec)
    return out


def emotion_signals(code: str, frame: pd.DataFrame, breadth: pd.DataFrame) -> list[dict]:
    """全段情绪底信号：宽度冰点 + 底背离 + 超卖共振，局部低点触发，去重。"""
    frame = frame.reset_index(drop=True)
    factors = factor_table(frame, breadth)
    pos = factors["position_bottom"]
    div = factors["divergence_bottom"]
    br = factors["breadth_bottom"]
    local_low = frame["close"] <= frame["close"].rolling(E_LOCAL_WINDOW).min()
    # 必须背离 + (超卖 或 宽度冰点) + 局部低点
    cond = ((div >= E_DIVERGENCE)
            & ((pos >= E_POSITION) | (br >= E_BREADTH))
            & local_low)
    candidates = [int(p) for p in np.flatnonzero(cond.fillna(False).to_numpy())]
    kept = _dedup(candidates, E_COOLDOWN)
    # 情绪底的"大波段幅度线"取该指数自身的自适应结构幅度（用 active 档默认 k=0.55 作参照线），
    # 仅用于统一的"大波段命中"衡量；不影响触发。
    ref_amp = 0.55 * annual_vol(frame, TRAIN_START, TRAIN_END)
    out = []
    for p in kept:
        date = frame["date"].iat[p]
        price = float(frame["close"].iat[p])
        rec = {
            "signal_type": "emotion",
            "date": date.strftime("%Y-%m-%d"),
            "segment": segment_of(date),
            "price": price,
            "position": round(float(pos.iat[p]), 4),
            "divergence": round(float(div.iat[p]), 4),
            "breadth": round(float(br.iat[p]), 4) if np.isfinite(br.iat[p]) else None,
        }
        rec.update(evaluate_position(frame, p, "bottom", price, ref_amp))
        rec["big_line"] = round(ref_amp, 5)
        out.append(rec)
    return out


def merge_with_resonance(structure: list[dict], emotion: list[dict],
                         frame: pd.DataFrame) -> list[dict]:
    """把两类信号合并：±3 交易日内同现则记为 resonance，否则保留原类型。"""
    dates = {d.strftime("%Y-%m-%d"): i for i, d in enumerate(frame["date"])}
    emo_positions = [dates[e["date"]] for e in emotion if e["date"] in dates]
    merged = []
    used_emotion = set()
    for s in structure:
        sp = dates.get(s["date"])
        hits = [i for i, ep in enumerate(emotion) if ep["date"] in dates
                and sp is not None and abs(dates[ep["date"]] - sp) <= 3]
        if hits:
            nearest = min(hits, key=lambda i: abs(dates[emotion[i]["date"]] - sp))
            used_emotion.update(hits)
            e = emotion[nearest]
            rec = dict(s)
            rec["signal_type"] = "resonance"
            rec["resonance_partner"] = {"date": e["date"], "price": e["price"]}
            merged.append(rec)
        else:
            merged.append(s)
    for i, e in enumerate(emotion):
        if i not in used_emotion:
            merged.append(e)
    merged.sort(key=lambda r: r["date"])
    return merged


def summarize(signals: list[dict], segment: str, signal_type: str) -> dict:
    part = [s for s in signals if s["segment"] == segment and s["signal_type"] == signal_type]
    resolved = [s for s in part if s["horizons"]["h20"]["status"] == "resolved"]
    ok = [s for s in resolved if s["horizons"]["h20"]["pass"]]
    big = [s for s in part if s.get("big_hit") is not None]
    big_ok = [s for s in big if s["big_hit"]]
    n = len(resolved)
    return {
        "triggers": len(part),
        "h20_resolved": n, "h20_success": len(ok),
        "h20_rate": round(len(ok) / n, 4) if n else None,
        "h20_wilson95": [round(v, 4) for v in wilson_interval(len(ok), n)] if n else [None, None],
        "big_resolved": len(big), "big_success": len(big_ok),
        "big_rate": round(len(big_ok) / len(big), 4) if big else None,
        "big_wilson95": [round(v, 4) for v in wilson_interval(len(big_ok), len(big))] if big else [None, None],
    }


def main() -> None:
    frames = load_frames()
    breadth = load_breadth()

    group_params = {}
    for group, codes in GROUPS.items():
        group_params[group] = choose_group_structure(codes, frames, breadth)

    per_index = {}
    all_signals = {}
    for group, codes in GROUPS.items():
        params = group_params[group]["chosen"]
        for code in codes:
            frame = frames[code].reset_index(drop=True)
            structure = structure_signals(code, frame, breadth, params)
            emotion = emotion_signals(code, frame, breadth)
            merged = merge_with_resonance(structure, emotion, frame)
            all_signals[code] = merged
            per_index[code] = {
                "name": pc.INDEXES[code],
                "group": group,
                "structure_params": params,
                "amplitude": structure[0]["amplitude"] if structure else None,
                "counts": {
                    "structure": sum(1 for s in merged if s["signal_type"] == "structure"),
                    "emotion": sum(1 for s in merged if s["signal_type"] == "emotion"),
                    "resonance": sum(1 for s in merged if s["signal_type"] == "resonance"),
                    "total": len(merged),
                },
            }

    # 汇总：按 信号类型 × 段
    summary = {}
    for signal_type in ("structure", "emotion", "resonance"):
        merged_all = [s for code in all_signals for s in all_signals[code]]
        summary[signal_type] = {
            segment: summarize(merged_all, segment, signal_type)
            for segment in ("train", "validation")
        }

    report = {
        "generated_for": "双类底部信号（结构底+情绪底），指数分 3 组",
        "note": "探索性研究，只读行情、只写研究目录；未触碰生产信号。训练段定参、验证段只报告。",
        "groups": {g: {"codes": c, "label": GROUP_LABEL[g]} for g, c in GROUPS.items()},
        "group_params": {
            g: {"chosen": group_params[g]["chosen"], "vol": group_params[g]["vol"],
                "grid": group_params[g]["grid"]}
            for g in GROUPS
        },
        "emotion_thresholds": {
            "position": E_POSITION, "divergence": round(E_DIVERGENCE, 4),
            "breadth": E_BREADTH, "local_window": E_LOCAL_WINDOW, "cooldown": E_COOLDOWN,
        },
        "per_index": per_index,
        "summary": summary,
        "signals": all_signals,
    }
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / "dual-bottom-research.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("已写入：", RESEARCH_DIR / "dual-bottom-research.json")
    for group in GROUPS:
        chosen = group_params[group]["chosen"]
        print(f"  {GROUP_LABEL[group]}: k={chosen['k']} gap={chosen['min_gap']} ma60={chosen['use_ma60']} "
              f"信号{chosen['signal_count']}/每年{chosen['per_year']} 方向{chosen['dir_rate']} 大波段{chosen['big_rate']}")
    for code, row in per_index.items():
        print(f"  {row['name']}: 结构{row['counts']['structure']} 情绪{row['counts']['emotion']} "
              f"共振{row['counts']['resonance']} 合计{row['counts']['total']}")


if __name__ == "__main__":
    main()
