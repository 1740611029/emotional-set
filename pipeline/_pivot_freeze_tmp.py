"""临时：3.6 训练段择参（每算法主指标 Top-1 + 辅助因子判别力权重），跑完即删。

只用训练段（2020-10-30~2024-12-31）拐点 + 候选算法信号，绝不触碰验证段做择参。
"""
from __future__ import annotations

import itertools
import json

import numpy as np
import pandas as pd

import pivot_benchmark as pb
from pivot_algorithms import donchian_signals, fractal_signals, zigzag_signals
from pivot_eval import FROZEN_WINDOWS, assign_segment, trading_positions
from pivot_aux_factors import build_aux_scores
from build_dashboard import load_breadth

TRAIN_START, TRAIN_END = pb.TRAIN_START, pb.TRAIN_END
WINDOWS = FROZEN_WINDOWS
M, N, CAP = WINDOWS["M"], WINDOWS["N"], WINDOWS["confirmation_cap"]

GRIDS = {
    "pivot-zigzag": [{"amplitude": a, "min_gap": g} for a, g in itertools.product((0.03, 0.05, 0.07), (8, 13, 21))],
    "pivot-donchian": [{"window": w, "confirm_days": d} for w, d in itertools.product((20, 55), (3, 5))],
    "pivot-fractal": [{"k": k} for k in (2, 3, 5)],
}
FUNCS = {"pivot-zigzag": zigzag_signals, "pivot-donchian": donchian_signals, "pivot-fractal": fractal_signals}


def segment_of(day: str, positions, frame) -> str:
    return assign_segment(day, train_start=TRAIN_START, train_end=TRAIN_END,
                          last_valid_date=pd.to_datetime(frame["date"]).max(),
                          embargo_end=TRAIN_END)


def train_metrics(signals, pivots, positions, frame):
    """训练段内：主达标命中/总数、捕捉命中/总数（以训练段拐点为分母）。"""
    train_piv = [p for p in pivots if segment_of(p["date"], positions, frame) == "train"]
    train_sig = [s for s in signals if segment_of(s["date"], positions, frame) == "train"]
    sig_by_kind = {"bottom": [], "top": []}
    for s in train_sig:
        pos = positions.get(s["date"])
        if pos is not None:
            sig_by_kind[s["type"]].append(pos)
    main = cap = total = 0
    for p in train_piv:
        pos = positions.get(p["date"])
        if pos is None:
            continue
        total += 1
        cands = sig_by_kind.get(p["type"], [])
        if any((pos - N) <= s <= (pos + CAP) for s in cands):
            main += 1
        if any(abs(s - pos) <= M for s in cands):
            cap += 1
    return main, cap, total


def main():
    frames = pb.load_index_frames()
    breadth = load_breadth()
    benchmark = json.loads(pb.BENCHMARK_PRODUCT.read_text(encoding="utf-8"))

    print("=== 每算法训练段主指标 Top-1 ===")
    chosen = {}
    for algo, grid in GRIDS.items():
        rows = []
        for params in grid:
            main_sum = cap_sum = total_sum = 0
            for code, frame in frames.items():
                positions = trading_positions(frame)
                signals = FUNCS[algo](frame, **params)
                pivots = benchmark["indices"][code]["pivots"]
                m, c, t = train_metrics(signals, pivots, positions, frame)
                main_sum += m; cap_sum += c; total_sum += t
            rate = main_sum / total_sum if total_sum else 0.0
            cap_rate = cap_sum / total_sum if total_sum else 0.0
            rows.append((rate, cap_rate, params, main_sum, total_sum))
        rows.sort(key=lambda r: (-r[0], -r[1]))
        best = rows[0]
        chosen[algo] = best[2]
        print(f"\n{algo}: 选定 {best[2]}  训练段主达标 {best[0]:.3f} 捕捉 {best[1]:.3f} "
              f"(命中/总={best[3]}/{best[4]})")
        for rate, cap_rate, params, m, t in rows[:4]:
            print(f"    {params} main={rate:.3f} capture={cap_rate:.3f} ({m}/{t})")

    # 汇总候选信号做辅助因子判别力分析
    print("\n=== 辅助因子判别力（hit 组均分 − miss 组均分，训练段）===")
    disc = {f: {"hit": [], "miss": []} for f in [
        "margin_flow", "etf_share", "qvix", "divergence", "breadth", "external_spx"]}
    for code, frame in frames.items():
        positions = trading_positions(frame)
        scores = build_aux_scores(frame, breadth)
        pivots = benchmark["indices"][code]["pivots"]
        piv_by_kind = {"bottom": [positions[p["date"]] for p in pivots if p["type"] == "bottom" and p["date"] in positions],
                       "top": [positions[p["date"]] for p in pivots if p["type"] == "top" and p["date"] in positions]}
        for algo, params in chosen.items():
            for s in FUNCS[algo](frame, **params):
                if segment_of(s["date"], positions, frame) != "train":
                    continue
                pos = positions.get(s["date"])
                if pos is None:
                    continue
                hit = any((p - N) <= pos <= (p + CAP) for p in piv_by_kind.get(s["type"], []))
                day = pd.Timestamp(s["date"])
                for factor, table in scores.items():
                    if day in table.index:
                        value = float(table.at[day, s["type"]])
                        if np.isfinite(value):
                            disc[factor]["hit" if hit else "miss"].append(value)

    weights = {}
    for factor, groups in disc.items():
        if groups["hit"] and groups["miss"]:
            d = float(np.mean(groups["hit"]) - np.mean(groups["miss"]))
            n_hit, n_miss = len(groups["hit"]), len(groups["miss"])
        else:
            d = 0.0; n_hit = n_miss = 0
        weight = 0.15 if d >= 0.10 else (0.10 if d >= 0.03 else 0.05)
        weights[factor] = weight
        print(f"  {factor:14s} d={d:+.3f} (hit n={n_hit}, miss n={n_miss}) -> 权重 {weight}")

    print("\nPREREGISTERED 草案:")
    print("  algorithm_params =", json.dumps(chosen, ensure_ascii=False))
    print("  aux_weights =", json.dumps(weights, ensure_ascii=False))


if __name__ == "__main__":
    main()
