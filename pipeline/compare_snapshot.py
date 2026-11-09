"""数值回归：用当前流水线重建数据，并与基线逐指数、逐行业板块比对信号事实。

只比对会影响准确率的事实（指数：latest / change_pct / current_signal / signals /
accuracy / evaluation 事件；板块：state / strength / days / 分组），新增字段（如 candidates）不参与比对。

板块基线默认取生产 `data/sector-trends.json`（与指数侧同一口径：现场重建 vs 已落盘产物）；
指向一个不存在的文件即跳过板块回归。换源前的申万两层基线
`data/research/sector-trends-baseline.json` 仅作换源前留痕，不再参与比对（新旧可比性已由
`sector_source_migration.py` 的对照报告接管）。

用法：
    python pipeline/compare_snapshot.py [--baseline data/index-signals.json]
"""
from __future__ import annotations

import argparse
import json
import math
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"


def _load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def compare_indices(baseline: dict, current: dict) -> list[str]:
    problems: list[str] = []
    base_map = {item["code"]: item for item in baseline.get("indices", [])}
    cur_map = {item["code"]: item for item in current.get("indices", [])}

    if set(base_map) != set(cur_map):
        problems.append(f"指数集合不一致：基线 {sorted(base_map)} / 当前 {sorted(cur_map)}")
        return problems

    for code, base in base_map.items():
        cur = cur_map[code]
        name = base.get("name", code)
        if not math.isclose(base["latest"], cur["latest"], rel_tol=0, abs_tol=1e-9):
            problems.append(f"{code} {name} latest 不一致：{base['latest']} vs {cur['latest']}")
        if not math.isclose(base["change_pct"], cur["change_pct"], rel_tol=0, abs_tol=1e-9):
            problems.append(f"{code} {name} change_pct 不一致：{base['change_pct']} vs {cur['change_pct']}")
        if base["current_signal"] != cur["current_signal"]:
            problems.append(f"{code} {name} current_signal 不一致：{base['current_signal']} vs {cur['current_signal']}")
        if base["signals"] != cur["signals"]:
            problems.append(
                f"{code} {name} signals 不一致：基线 {len(base['signals'])} 条 / 当前 {len(cur['signals'])} 条"
            )
        if base["accuracy"] != cur["accuracy"]:
            problems.append(f"{code} {name} accuracy 不一致：{base['accuracy']} vs {cur['accuracy']}")

        base_events = _events(base.get("evaluation"))
        cur_events = _events(cur.get("evaluation"))
        if base_events != cur_events:
            problems.append(
                f"{code} {name} evaluation 事件不一致：基线 {len(base_events)} 条 / 当前 {len(cur_events)} 条"
            )

    return problems


def _events(evaluation: dict | None) -> list[dict]:
    if not evaluation:
        return []
    rows: list[dict] = []
    for segment in evaluation.get("segments", []):
        for event in segment.get("events", []):
            rows.append({
                "date": event["date"], "type": event["type"], "price": event["price"],
                "status": event["status"], "confirmation_date": event["confirmation_date"],
                "reason": event["reason"],
            })
    return rows


def compare_recent(baseline: dict, current: dict) -> list[str]:
    """最近信号列表只做包含性校验：基线条目必须全部出现在当前结果中。"""
    key = lambda row: (row["date"], row["code"], row["type"], row["price"])  # noqa: E731
    current_keys = {key(row) for row in current.get("recent_signals", [])}
    missing = [key(row) for row in baseline.get("recent_signals", []) if key(row) not in current_keys]
    if missing:
        return [f"最近信号缺失 {len(missing)} 条，例如 {missing[:3]}"]
    return []


def _sector_map(payload: dict) -> dict[str, dict]:
    """code -> 状态事实；单层清单里每个行业板块就是自身那条记录，无父子层级。"""
    result: dict[str, dict] = {}
    for group, rows in payload.get("groups", {}).items():
        for item in rows:
            state = item.get("state") if item.get("state") is not None else (
                None if group == "unclassified" else group)
            result[item["code"]] = {
                "code": item["code"], "name": item["name"],
                "state": state,
                "strength": item.get("strength") if state else None,
                "days": item.get("days") if state else None, "group": group,
            }
    return result


def compare_sectors(baseline: dict, current: dict) -> list[str]:
    """单层行业板块逐个必须零变化：任何状态/强度/天数/分组差异都是回归失败。"""
    problems: list[str] = []
    if baseline.get("as_of") != current.get("as_of"):
        problems.append(f"板块 as_of 不一致：{baseline.get('as_of')} vs {current.get('as_of')}")
    base_map = _sector_map(baseline)
    cur_map = _sector_map(current)
    # code 是唯一锁，重复 code 会在 map 里静默覆盖，故先按条目数对账
    total = sum(len(rows) for rows in current.get("groups", {}).values())
    if total != len(cur_map):
        problems.append(f"当前产物板块 code 重复：条目 {total} 个 / 唯一 code {len(cur_map)} 个")
    if set(base_map) != set(cur_map):
        missing = sorted(set(base_map) - set(cur_map))
        extra = sorted(set(cur_map) - set(base_map))
        problems.append(f"板块集合不一致：缺少 {missing[:3]} / 多出 {extra[:3]}")
        return problems

    for code, base in base_map.items():
        cur = cur_map[code]
        label = f"{base['name']}({code})"
        if base["state"] != cur["state"]:
            problems.append(f"板块 {label} state 变化：{base['state']} -> {cur['state']}")
        if not _close(base["strength"], cur["strength"]):
            problems.append(f"板块 {label} strength 变化：{base['strength']} vs {cur['strength']}")
        if base["days"] != cur["days"]:
            problems.append(f"板块 {label} days 变化：{base['days']} -> {cur['days']}")
        if base["group"] != cur["group"]:
            problems.append(f"板块 {label} 分组变化：{base['group']} -> {cur['group']}")
    return problems


def _close(left, right) -> bool:
    if left is None or right is None:
        return left is None and right is None
    # 产物按 4 位小数落盘，旧基线保留全精度
    return math.isclose(left, right, rel_tol=0, abs_tol=1e-4)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--baseline", default=str(DATA / "index-signals.json"))
    parser.add_argument("--baseline-sectors",
                        default=str(DATA / "sector-trends.json"),
                        help="板块基线产物，默认与生产 sector-trends.json 比对；文件不存在则跳过板块回归")
    parser.add_argument("--current", default="", help="已重建的 index-signals.json；留空则现场重建")
    parser.add_argument("--current-sectors", default="")
    args = parser.parse_args()

    baseline = _load(Path(args.baseline))
    baseline_sectors = _load(Path(args.baseline_sectors)) if Path(args.baseline_sectors).exists() else None

    if args.current:
        current = _load(Path(args.current))
    else:
        from build_dashboard import build_indices  # noqa: PLC0415
        print("现场重建指数数据……")
        current = build_indices()

    problems = compare_indices(baseline, current)
    problems += compare_recent(baseline, current)

    if baseline_sectors is not None:
        if args.current_sectors:
            current_sectors = _load(Path(args.current_sectors))
        else:
            from build_dashboard import build_sectors  # noqa: PLC0415
            print("现场重建板块数据……")
            current_sectors = build_sectors()
        sector_problems = compare_sectors(baseline_sectors, current_sectors)
        problems += sector_problems

    if problems:
        print(f"FAIL：发现 {len(problems)} 处不一致")
        for row in problems:
            print("  -", row)
        return 1
    print("PASS：信号事实与基线完全一致")
    print(f"  指数 {len(current['indices'])} 个，已确认信号 {len(current['recent_signals'])} 条，"
          f"候选 {len(current.get('candidates', []))} 条")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
