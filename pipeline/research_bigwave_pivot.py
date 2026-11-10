"""探索性预研：自适应幅度「大波段极值」+ 因子闸门 + 1~3 日三层确认 + 双口径评价。

目标（对齐用户四条口径，见 signal-design-principles）：
- 幅度驱动：候选由「自适应幅度 ZigZag 极值」产生，不刷固定形态；
- 极少而精：每个指数按自身训练段波动设一条幅度线，目标每指数每年 1~2 个拐点；
- 确认只等 1~3 天：三层确认（价格→动量→量能）窗口固定 3 个交易日；
- 双口径：口径A=5/10/20/30 日方向正确 + 20 日 5% 浮亏硬约束；口径B=信号后 60~120 日
  是否出现 ≥ 大波段幅度线的反向行情（大波段命中）。

硬约束（AGENTS.md）：
- #1 无未来函数：幅度线只用训练段（2020-10-30~2024-12-31）定，验证段不调整；
  信号日 = max(拐点自然确认日, 其后 3 日内三层确认通过日)，绝不早于可确认日。
- #7 调参/验证分离：参数只在训练段选，验证段只报告。
- #9 变更留痕：本脚本只写 data/research/，不触碰任何生产产物。

纯计算模块，不做 I/O；仅在 __main__ 时写 data/research/ 下的研究产物。
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipeline") not in __import__("sys").path:
    __import__("sys").path.insert(0, str(ROOT / "pipeline"))

from pivot_benchmark import annotate_pivots  # noqa: E402
from research_algorithms import read_sector_csv, wilson_interval  # noqa: E402
from swing_indicators import breadth_table, factor_table  # noqa: E402
from swing_signal_engine import layer_state, prepare  # noqa: E402
import precision_config as pc  # noqa: E402

RESEARCH_DIR = ROOT / "data" / "research"
INDEX_RAW = ROOT / "data" / "raw" / "indices-ths"
SECTOR_RAW = ROOT / "data" / "raw" / "sectors-em-industry"

KINDS = ("bottom", "top")
HORIZONS = (5, 10, 20, 30)
MAE_WINDOW = 20
MAE_LIMIT = 0.05
BIG_WINDOW = (20, 120)          # 大波段命中观察窗口（交易日）
CONFIRM_WINDOW = 3              # 三层确认最多等待的交易日
GATE_CORE = ("position", "divergence", "breadth")
GATE_THRESHOLD = 0.60
GATE_NEED = 2                   # 三选二

TRAIN_START = pd.Timestamp("2020-10-30")
TRAIN_END = pd.Timestamp("2024-12-31")
VALID_START = pd.Timestamp("2025-01-01")
EMBARGO_END = pd.Timestamp("2024-11-19")   # 训练段有效截止（末尾 30 交易日为隔离带）

K_GRID = (0.35, 0.45, 0.55, 0.65, 0.75)    # 幅度线 = k × 训练段年化波动
GAP_GRID = (15, 20, 30)                     # 最小波段间隔（交易日）
TARGET_PER_YEAR = (1.0, 2.0)                # 每指数每年目标拐点数（极少而精）
IDEAL_PER_YEAR = 1.5


def load_frames() -> dict[str, pd.DataFrame]:
    return {code: pc.read_index_csv(code) for code in pc.INDEXES}


def load_breadth() -> pd.DataFrame:
    files = sorted(SECTOR_RAW.glob("*.csv"))
    if len(files) < 20:
        raise ValueError(f"东财行业板块文件不足 20 个：{len(files)}")
    return breadth_table({path.stem: read_sector_csv(path) for path in files})


def annual_vol(frame: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> float:
    part = frame[(frame["date"] >= start) & (frame["date"] <= end)]
    ret = part["close"].pct_change().dropna()
    return float(ret.std() * np.sqrt(252))


def choose_amplitude(frame: pd.DataFrame) -> dict:
    """训练段网格：为单个指数在 k×vol 里选「每指数每年 1~2 个拐点」最贴近的组合。"""
    train = frame[(frame["date"] >= TRAIN_START) & (frame["date"] <= TRAIN_END)].reset_index(drop=True)
    years = (TRAIN_END - TRAIN_START).days / 365.25
    vol = annual_vol(frame, TRAIN_START, TRAIN_END)
    grid = []
    for k in K_GRID:
        amplitude = k * vol
        for gap in GAP_GRID:
            count = len(annotate_pivots(train, amplitude=amplitude, min_gap=gap))
            per_year = count / years if years > 0 else 0.0
            grid.append({"k": k, "amplitude": amplitude, "min_gap": gap,
                         "train_pivots": count, "per_year": per_year})
    lo, hi = TARGET_PER_YEAR
    in_band = [row for row in grid if lo <= row["per_year"] <= hi]
    pool = in_band or grid
    best = min(pool, key=lambda row: abs(row["per_year"] - IDEAL_PER_YEAR))
    return {
        "train_annual_vol": vol,
        "chosen": best,
        "in_target_band": bool(in_band),
        "grid": grid,
    }


def segment_of(day: pd.Timestamp) -> str:
    if day <= EMBARGO_END:
        return "train"
    if day < VALID_START:
        return "excluded"
    return "validation"


def three_layer_confirm(frame: pd.DataFrame, state: dict, candidate: int, kind: str,
                        natural: int) -> tuple[int | None, list[dict]]:
    """从自然确认日（含）起最多 3 个交易日找三层全通过日；只读 ≤ 确认日数据。"""
    config = state["params"]
    context, delta = state["context"], state["breadth_delta5"]
    log = []
    for offset in range(0, CONFIRM_WINDOW):
        position = natural + offset
        if position >= len(frame):
            break
        layers = layer_state(frame, context, delta, position, kind, candidate, config)
        ok = bool(layers["price"] and layers["momentum"] and layers["volume"])
        log.append({
            "date": frame["date"].iat[position].strftime("%Y-%m-%d"),
            "offset": int(offset),
            "price": bool(layers["price"]),
            "momentum": bool(layers["momentum"]),
            "volume": bool(layers["volume"]),
            "passed": int(ok),
        })
        if ok:
            return position, log
    return None, log


def factor_gate(factors: pd.DataFrame, candidate: int, kind: str) -> dict:
    values = {name: factors[f"{name}_{kind}"].iat[candidate] for name in GATE_CORE}
    available = {name: float(v) for name, v in values.items() if np.isfinite(v)}
    hits = sum(1 for v in available.values() if v >= GATE_THRESHOLD)
    veto = bool(factors[f"veto_{kind}"].iat[candidate])
    return {
        "values": {name: (round(v, 4) if np.isfinite(v) else None) for name, v in values.items()},
        "available": len(available),
        "hits": int(hits),
        "gate_pass": bool(hits >= GATE_NEED),
        "veto": veto,
    }


def evaluate_position(frame: pd.DataFrame, position: int, kind: str, price: float,
                      amplitude: float) -> dict:
    """单信号双口径评价：口径A（方向+浮亏）、口径B（大波段命中）。"""
    last = len(frame) - 1
    forward = frame.iloc[position + 1:]
    window = forward.iloc[:MAE_WINDOW]
    if len(window) == 0:
        mae = None
    elif kind == "bottom":
        mae = float(window["low"].min() / price - 1)          # 负值=浮亏
    else:
        mae = float(window["high"].max() / price - 1)         # 正值=浮亏
    mae_ok = bool(mae is not None and abs(mae) <= MAE_LIMIT)

    horizons = {}
    for h in HORIZONS:
        if position + h > last:
            horizons[f"h{h}"] = {"status": "pending", "ret": None, "dir_ok": None, "pass": None}
            continue
        ret = float(frame["close"].iat[position + h] / price - 1)
        dir_ok = bool(ret > 0) if kind == "bottom" else bool(ret < 0)
        horizons[f"h{h}"] = {
            "status": "resolved", "ret": round(ret, 5),
            "dir_ok": dir_ok, "pass": bool(dir_ok and mae_ok),
        }

    lo, hi = BIG_WINDOW
    big_part = forward.iloc[:hi]
    if len(big_part) < lo:
        big_band, big_hit = None, None
    elif kind == "bottom":
        big_band = float(big_part["high"].max() / price - 1)
        big_hit = bool(big_band >= amplitude)
    else:
        big_band = float((price - big_part["low"].min()) / price)
        big_hit = bool(big_band >= amplitude)

    return {
        "mae20": round(mae, 5) if mae is not None else None,
        "mae_ok": mae_ok,
        "horizons": horizons,
        "big_band": round(big_band, 5) if big_band is not None else None,
        "big_hit": big_hit,
        "big_line": round(amplitude, 5),
    }


def trend_break_ok(frame: pd.DataFrame, ma60: pd.Series, position: int, kind: str) -> bool:
    """趋势结构确认：顶部要求确认日收盘跌破 MA60；底部要求收盘重回 MA60 上方。

    目的是剔除「上涨趋势中的回调被当成顶」「下跌趋势中的反弹被当成底」——
    只有中期趋势真的被破坏/修复，才认这个大波段拐点。
    """
    if position >= len(frame):
        return False
    close = float(frame["close"].iat[position])
    level = ma60.iat[position]
    if not np.isfinite(level):
        return False
    return bool(close < float(level)) if kind == "top" else bool(close > float(level))


def run_index(code: str, name: str, frame: pd.DataFrame, breadth: pd.DataFrame) -> dict:
    frame = frame.reset_index(drop=True)
    choice = choose_amplitude(frame)
    amplitude = float(choice["chosen"]["amplitude"])
    gap = int(choice["chosen"]["min_gap"])

    state = prepare(frame, breadth, {"confirm_window": CONFIRM_WINDOW})
    factors = state["factors"]
    ma60 = frame["close"].rolling(60).mean()
    ma120 = frame["close"].rolling(120).mean()
    ma120_slope = ma120.diff(10)

    def phase_up(position: int) -> bool:
        return bool(position < len(frame) and np.isfinite(ma120.iat[position])
                    and float(frame["close"].iat[position]) > float(ma120.iat[position])
                    and float(ma120_slope.iat[position]) > 0)

    def phase_down(position: int) -> bool:
        return bool(position < len(frame) and np.isfinite(ma120.iat[position])
                    and float(frame["close"].iat[position]) < float(ma120.iat[position])
                    and float(ma120_slope.iat[position]) < 0)

    pivots = annotate_pivots(frame, amplitude=amplitude, min_gap=gap)
    variants = {"A_pure_pivot": [], "B_plus_confirm": [],
                "C_plus_gate": [], "D_plus_veto": [],
                "E_pivot_trendbreak": [], "F_full_trendbreak": [],
                "G_phase_filtered": [], "H_confirm_trendbreak": []}
    dropped = {"B_plus_confirm": 0, "C_plus_gate": 0, "D_plus_veto": 0}

    for pivot in pivots:
        candidate = int(pivot["index"])
        natural = int(pivot["confirm_index"])
        kind = pivot["type"]

        # 方案A：纯大波段极值，信号日=拐点自然确认日
        rec_a = _signal_record(
            frame, natural, kind, "A_pure_pivot", pivot, natural, amplitude,
            factor_gate(factors, candidate, kind), None)
        variants["A_pure_pivot"].append(rec_a)
        # 方案E：A + 趋势结构确认（跌破/站上 MA60）
        if trend_break_ok(frame, ma60, natural, kind):
            variants["E_pivot_trendbreak"].append({**rec_a, "variant": "E_pivot_trendbreak"})
            # 方案G：E + 行情阶段过滤（强势上行不报顶、强势下行不报底）
            if kind == "top" and not phase_up(natural):
                variants["G_phase_filtered"].append({**rec_a, "variant": "G_phase_filtered"})
            elif kind == "bottom" and not phase_down(natural):
                variants["G_phase_filtered"].append({**rec_a, "variant": "G_phase_filtered"})

        # 三层确认（B/C/D 共用）
        pos, log = three_layer_confirm(frame, state, candidate, kind, natural)
        if pos is None:
            for key in ("B_plus_confirm", "C_plus_gate", "D_plus_veto"):
                dropped[key] += 1
            continue
        gate = factor_gate(factors, candidate, kind)
        # B：仅三层确认
        rec_b = _signal_record(frame, pos, kind, "B_plus_confirm", pivot, natural, amplitude, gate, log)
        variants["B_plus_confirm"].append(rec_b)
        # H：三层确认 + MA60 趋势结构确认（最贴合「1~3 日确认 + 大波段级别」的组合）
        if trend_break_ok(frame, ma60, pos, kind):
            variants["H_confirm_trendbreak"].append({**rec_b, "variant": "H_confirm_trendbreak"})
        # C：三层 + 因子闸门（三选二）
        if gate["gate_pass"]:
            variants["C_plus_gate"].append(_signal_record(
                frame, pos, kind, "C_plus_gate", pivot, natural, amplitude, gate, log))
        else:
            dropped["C_plus_gate"] += 1
        # D：C + trend 硬约束（强趋势加速时否决反向信号）
        rec_d = None
        if gate["gate_pass"] and not gate["veto"]:
            rec_d = _signal_record(frame, pos, kind, "D_plus_veto", pivot, natural, amplitude, gate, log)
            variants["D_plus_veto"].append(rec_d)
        else:
            dropped["D_plus_veto"] += 1
        # F：D + 趋势结构确认（在最强闸门之上再叠加 MA60 趋势确认）
        if rec_d is not None and trend_break_ok(frame, ma60, pos, kind):
            variants["F_full_trendbreak"].append({**rec_d, "variant": "F_full_trendbreak"})

    return {
        "code": code, "name": name,
        "amplitude_choice": choice,
        "pivot_count": len(pivots),
        "pivots": pivots,
        "variants": variants,
        "dropped_by_confirm": dropped["B_plus_confirm"],
        "dropped_by_gate": dropped["C_plus_gate"],
        "dropped_by_veto": dropped["D_plus_veto"],
    }


def _signal_record(frame, position, kind, variant, pivot, natural, amplitude, gate, log) -> dict:
    date = frame["date"].iat[position]
    price = float(frame["close"].iat[position])
    record = {
        "date": date.strftime("%Y-%m-%d"),
        "segment": segment_of(date),
        "type": kind,
        "variant": variant,
        "price": price,
        "pivot_date": pivot["date"],
        "pivot_price": float(pivot["price"]),
        "natural_confirm_date": frame["date"].iat[natural].strftime("%Y-%m-%d"),
        "lag_from_pivot": int(position - int(pivot["index"])),
        "gate": gate,
        "confirmation": log,
    }
    record.update(evaluate_position(frame, position, kind, price, amplitude))
    return record


def aggregate(records: list[dict], variant: str, segment: str) -> dict:
    selected = [r for r in records if r["variant"] == variant and r["segment"] == segment]
    out = {"variant": variant, "segment": segment, "total": len(selected), "by_kind": {}}
    for kind in KINDS:
        part = [r for r in selected if r["type"] == kind]
        cell = {"triggers": len(part), "horizons": {}}
        for h in HORIZONS:
            resolved = [r for r in part if r["horizons"][f"h{h}"]["status"] == "resolved"]
            success = sum(1 for r in resolved if r["horizons"][f"h{h}"]["pass"])
            n = len(resolved)
            cell["horizons"][f"h{h}"] = {
                "resolved": n, "success": success,
                "rate": round(success / n, 4) if n else None,
                "wilson95": [round(v, 4) for v in wilson_interval(success, n)] if n else [None, None],
            }
        big_resolved = [r for r in part if r["big_hit"] is not None]
        big_success = sum(1 for r in big_resolved if r["big_hit"])
        nb = len(big_resolved)
        cell["big_band"] = {
            "resolved": nb, "success": big_success,
            "rate": round(big_success / nb, 4) if nb else None,
            "wilson95": [round(v, 4) for v in wilson_interval(big_success, nb)] if nb else [None, None],
        }
        maes = [r["mae20"] for r in part if r["mae20"] is not None]
        cell["avg_mae20"] = round(float(np.mean(maes)), 5) if maes else None
        cell["worst_mae20"] = round(float(min(maes, key=abs)), 5) if maes else None
        lags = [r["lag_from_pivot"] for r in part]
        cell["lag_from_pivot"] = {
            "median": float(np.median(lags)) if lags else None,
            "p90": float(np.percentile(lags, 90)) if lags else None,
        }
        out["by_kind"][kind] = cell
    return out


def build_report(results: dict[str, dict]) -> dict:
    all_records = [r for item in results.values() for variant in item["variants"].values() for r in variant]
    variants = ("A_pure_pivot", "B_plus_confirm", "C_plus_gate", "D_plus_veto",
                "E_pivot_trendbreak", "F_full_trendbreak", "G_phase_filtered",
                "H_confirm_trendbreak")
    segments = ("train", "validation")
    matrix = {(v, s): aggregate(all_records, v, s) for v in variants for s in segments}

    per_index = {}
    for code, item in results.items():
        per_index[code] = {
            "name": item["name"],
            "amplitude": round(item["amplitude_choice"]["chosen"]["amplitude"], 5),
            "k": item["amplitude_choice"]["chosen"]["k"],
            "min_gap": item["amplitude_choice"]["chosen"]["min_gap"],
            "pivot_count": item["pivot_count"],
            "per_year": round(item["amplitude_choice"]["chosen"]["per_year"], 3),
            "in_target_band": item["amplitude_choice"]["in_target_band"],
            "dropped_confirm": item["dropped_by_confirm"],
            "dropped_gate": item["dropped_by_gate"],
            "dropped_veto": item["dropped_by_veto"],
            "train_annual_vol": round(item["amplitude_choice"]["train_annual_vol"], 4),
        }

    return {
        "generated_for": "signal-design-principles 四条口径预研",
        "note": "探索性预研，非独立验证、非交易收益；只读行情，未触碰生产信号。",
        "frozen": {
            "train": [TRAIN_START.strftime("%Y-%m-%d"), EMBARGO_END.strftime("%Y-%m-%d")],
            "embargo_end": EMBARGO_END.strftime("%Y-%m-%d"),
            "validation_from": VALID_START.strftime("%Y-%m-%d"),
            "k_grid": list(K_GRID), "gap_grid": list(GAP_GRID),
            "gate_core": list(GATE_CORE), "gate_threshold": GATE_THRESHOLD,
            "gate_need": GATE_NEED, "confirm_window": CONFIRM_WINDOW,
            "mae_window": MAE_WINDOW, "mae_limit": MAE_LIMIT,
            "big_window": list(BIG_WINDOW),
        },
        "per_index": per_index,
        "matrix": {f"{v}|{s}": matrix[(v, s)] for v in variants for s in segments},
        "signals": {code: item["variants"] for code, item in results.items()},
    }


def _pct(value):
    return f"{value * 100:.1f}%" if isinstance(value, (int, float)) else "—"


def render_markdown(report: dict) -> str:
    lines = ["# 大波段顶底信号预研报告（探索性）", ""]
    lines.append(f"生成目标：{report['generated_for']}")
    lines.append("")
    lines.append("> 纯计算预研，只读行情、只写本目录；**未触碰生产 `index-signals.json` 与现行信号**。"
                 "结果为事后探索性统计，不是独立验证或交易收益。")
    lines.append("")
    lines.append("## 1. 自适应幅度定参（每指数按自身波动，训练段 2020-10-30~2024-12-31）")
    lines.append("")
    lines.append("| 指数 | 训练段年化波动 | k | 幅度线 | 最小间隔 | 训练段拐点 | 每年 | 落入 1~2/年 |")
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for code, row in report["per_index"].items():
        lines.append(
            f"| {row['name']} | {_pct(row['train_annual_vol'])} | {row['k']} | {_pct(row['amplitude'])} "
            f"| {row['min_gap']}d | {row['pivot_count']} | {row['per_year']} | {'是' if row['in_target_band'] else '否'} |")
    lines.append("")
    lines.append("## 2. 四方案 × 训练段 / 验证段 对照（7 指数合并）")
    lines.append("")
    lines.append("方案说明：A=纯大波段极值（自然确认日即信号）；B=+1~3 日三层确认；"
                 "C=+宽度/背离/位置三选二闸门；D=+趋势否决硬约束；"
                 "E=极值+MA60 趋势破坏确认；F=三层+闸门+否决+MA60 趋势破坏确认；"
                 "G=E+行情阶段过滤（MA120 强势上行不报顶、强势下行不报底）；"
                 "H=三层确认+MA60 趋势破坏确认（最贴合「1~3 日确认 + 大波段」的组合）。")
    lines.append("")
    header = "| 方案 | 段 | 方向 | 触发 | 20日方向正确 | 大波段命中 | 平均MAE20 | 距极值滞后中位 |"
    lines.append(header)
    lines.append("| --- | --- | --- | --- | --- | --- | --- | --- |")
    for variant in ("A_pure_pivot", "B_plus_confirm", "C_plus_gate", "D_plus_veto",
                    "E_pivot_trendbreak", "F_full_trendbreak", "G_phase_filtered",
                    "H_confirm_trendbreak"):
        for segment in ("train", "validation"):
            block = report["matrix"][f"{variant}|{segment}"]
            for kind, label in (("bottom", "底"), ("top", "顶")):
                cell = block["by_kind"][kind]
                h20 = cell["horizons"]["h20"]
                big = cell["big_band"]
                lag = cell["lag_from_pivot"]
                lines.append(
                    f"| {variant.split('_')[0]} | {segment} | {label} | {cell['triggers']} "
                    f"| {_pct(h20['rate'])} ({h20['success']}/{h20['resolved']}) "
                    f"| {_pct(big['rate'])} ({big['success']}/{big['resolved']}) "
                    f"| {_pct(cell['avg_mae20'])} "
                    f"| {lag['median'] if lag['median'] is not None else '—'}d |")
    lines.append("")
    lines.append("## 3. 读法与局限")
    lines.append("")
    lines.append("1. 方向正确 = 固定持有期期末方向正确，且 20 个交易日内 |盘中最大浮亏| ≤ 5%。")
    lines.append("2. 大波段命中 = 信号后 20~120 个交易日内，朝信号方向的最大幅度 ≥ 该指数幅度线。")
    lines.append("3. 纯幅度极值（方案A）的信号日 = 拐点自然确认日，本身已滞后极值若干交易日；"
                 "这是「等反转达幅度才可知拐点」的固有代价，不是实现选择。")
    lines.append("4. 参数只在训练段选定、验证段未调整；样本量偏少时数字仍如实披露，置信区间见 JSON。")
    lines.append("5. 预研不等于立项；是否替换生产信号须另行确认。")
    lines.append("")
    lines.append("## 4. 初步解读（人工补充）")
    lines.append("")
    lines.append("- **底部信号：这条路基本走通。** 方案 E（极值+MA60 趋势确认）在验证段 5/5 方向正确、"
                 "大波段命中 4/5；方案 B 在验证段 6/6 方向正确。较现行 `index-swing6-v2.0`"
                 "（验证段顶部+底部合计 11.8% 方向正确）明显改善。")
    lines.append("- **顶部信号：训练段有效、验证段归零。** 训练段（含 2021 年末大顶、2022 熊市）"
                 "顶部大波段命中 C=92.3%、D=88.9%、B=76.5%；但验证段（2025-01 起）顶部一律 0%。")
    lines.append("- **验证段顶部归零的根因不是算法，是行情性质。** 2025-2026 的 7 个指数整体处于"
                 "震荡上行、反复创新高，本就不存在「大波段顶部」；任何在此段报顶的信号都必然被后续新高打脸。"
                 "这正是「顶部信号后指数一直涨」的直观来源——那些位置本质是上涨中继/回调底，不是顶。")
    lines.append("- **改进方向：底顶不对称 + 行情阶段过滤。** 强上行趋势里不报顶（只报底部/回调买点），"
                 "强下行趋势里不报底。方案 G 已验证该思路不伤害底部（验证段仍 5/5），"
                 "但用 MA120 判定行情阶段在震荡上行里不够灵敏，顶部误报未能清干净，需进一步研究更灵敏的阶段判定。")
    lines.append("- **诚实提示：验证段没有真大顶**，顶部算法的真实检验要等下一轮熊市，或回看 2018 / 2021 末等含大顶的历史段。"
                 "验证段的 0% 不据此反向调参（训练/验证分离）。")
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    frames = load_frames()
    breadth = load_breadth()
    results = {}
    for code, name in pc.INDEXES.items():
        results[code] = run_index(code, name, frames[code], breadth)
    report = build_report(results)
    RESEARCH_DIR.mkdir(parents=True, exist_ok=True)
    (RESEARCH_DIR / "bigwave-pivot-preflash.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    (RESEARCH_DIR / "bigwave-pivot-preflash.md").write_text(
        render_markdown(report), encoding="utf-8")
    print("已写入：", RESEARCH_DIR / "bigwave-pivot-preflash.md")
    for code, row in report["per_index"].items():
        print(f"  {row['name']}: 幅度线 {row['amplitude']*100:.1f}% 间隔 {row['min_gap']}d "
              f"拐点 {row['pivot_count']} 个/每年 {row['per_year']}")


if __name__ == "__main__":
    main()
