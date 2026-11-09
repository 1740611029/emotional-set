"""ETF v1.2 修正的隔离验收与同输入三版本对照，不写生产文件。

运行：python pipeline/verify_etf_volume_revision.py
旧研究原件保留；新报告及验证记录仅写 data/research/。
"""
from __future__ import annotations

import hashlib
import io
import json
import subprocess
import sys
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import build_dashboard
import etf_volume_events as ev
import run_etf_volume_study as study

ROOT = Path(__file__).resolve().parents[1]
STUDY_NAME = f"{ev.ALGORITHM_VERSION}.json"
REPORT = ROOT / f"data/research/{ev.ALGORITHM_VERSION}-verification.json"
GUARDED = [ROOT / "data" / name for name in (
    "index-signals.json", "sector-trends.json", "etf-volume-events.json",
    "research/etf-volume-spike.json", "research/etf-volume-spike.md",
    "research/etf-volume-spike-v1.1.json", "research/etf-volume-spike-v1.1.md",
    "research/etf-volume-spike-v1.1-verification.json",
)]
GUARDED += [study.ETF_DAILY_RAW / f"{item['code']}.csv" for item in study.ETF_UNIVERSE]
GUARDED += [study.INDEX_RAW / f"{code}.csv" for code in study.INDEXES]


def fingerprints():
    return {str(path.relative_to(ROOT)): {
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "mtime_ns": path.stat().st_mtime_ns,
    } for path in GUARDED}


def run_checked(args):
    result = subprocess.run(args, cwd=ROOT, capture_output=True, text=True,
                            encoding="utf-8", errors="replace")
    print(result.stdout, flush=True)
    if result.stderr:
        print(result.stderr, flush=True)
    result.check_returncode()
    return {"command": args, "exit_code": result.returncode,
            "stdout": result.stdout, "stderr": result.stderr}


def same_input_comparison(start, boundary, current, previous):
    core, indices, _ = study.load_inputs(boundary)
    basis = indices[ev.STATE_BASIS_INDEX]
    sample_core = {code: study._clip(frame, start, boundary) for code, frame in core.items()}
    short_basis = study._clip(basis, start, boundary)
    sample_dates = short_basis["date"].astype(str).str.slice(0, 10).tolist()
    short_result = ev.identify(sample_core, short_basis)
    full_result = ev.identify(core, basis)
    full_events = [row for row in full_result["events"] if start <= row["start_date"] <= boundary]
    assert full_events == current["events"], "完整历史识别与研究事件不一致"
    # 旧口径只在验证中复刻；三版均使用当前原始输入及同一研究区间。
    legacy = [{**event, "level": max(
        (day["level"] for day in short_result["days"]
         if event["start_date"] <= day["date"] <= event["end_date"]),
        key=ev.LEVEL_RANK.__getitem__,
    )} for event in short_result["events"]]

    def summary(events, history):
        baselines = study.baseline_days(sample_core, history, sample_dates)
        stats = ev.forward_stats(events, history, baselines=baselines)
        study.attach_normal_baseline(stats, ev.layer_stats(history, baselines["normal_volume"]))
        return {"event_count": len(events), "events": events,
                "layers_5d": {row["key"]: row["horizons"]["5"] for row in stats["layers"]}}

    versions = {
        "v1.0_short_history_highest_level": summary(legacy, short_basis),
        "v1.1_short_history_first_day": summary(short_result["events"], short_basis),
        "v1.2_full_history_first_day": summary(full_events, basis),
    }
    assert versions["v1.2_full_history_first_day"]["layers_5d"] == {
        row["key"]: row["horizons"]["5"] for row in current["stats"]["layers"]
    }, "同输入 v1.2 对照与报告统计不一致"
    if previous["input_hash"] == current["input_hash"]:
        assert short_result["events"] == previous["events"], "v1.1 事件复刻不一致"
        assert versions["v1.1_short_history_first_day"]["layers_5d"] == {
            row["key"]: row["horizons"]["5"] for row in previous["stats"]["layers"]
        }, "v1.1 统计复刻不一致"
    old_by_date = {row["start_date"]: row for row in short_result["events"]}
    new_by_date = {row["start_date"]: row for row in full_events}
    state_changes = [{"start_date": day, "old_state": old_by_date[day]["state"],
                      "new_state": new_by_date[day]["state"]}
                     for day in sorted(old_by_date.keys() & new_by_date.keys())
                     if old_by_date[day]["state"] != new_by_date[day]["state"]]
    return {
        "basis": "相同当前本地输入、同一研究区间和冻结参数；旧短历史最高级、旧短历史首日级别、新完整预热首日级别三版对照",
        "range": [start, boundary],
        "level_changes_v1_0_to_v1_1": [
            {"start_date": new["start_date"], "old_level": old["level"], "new_level": new["level"]}
            for old, new in zip(legacy, short_result["events"]) if old["level"] != new["level"]
        ],
        "state_changes_v1_1_to_v1_2": state_changes,
        "removed_start_dates": sorted(old_by_date.keys() - new_by_date.keys()),
        "added_start_dates": sorted(new_by_date.keys() - old_by_date.keys()),
        "v1_1_matches_saved_report": previous["input_hash"] == current["input_hash"],
        "versions": versions,
    }


def main():
    before = fingerprints()
    old = json.loads((ROOT / "data/research/etf-volume-spike.json").read_text(encoding="utf-8"))
    previous = json.loads((ROOT / "data/research/etf-volume-spike-v1.1.json").read_text(encoding="utf-8"))
    start, end = old["range"]["start"], old["range"]["end"]
    assert STUDY_NAME not in {"etf-volume-spike.json", "etf-volume-spike-v1.1.json"}
    tests = run_checked([sys.executable, "-m", "pytest", "pipeline", "server", "-q"])
    assert fingerprints() == before, "测试阶段改变了生产文件或旧报告，停止验收"
    research = run_checked([
        sys.executable, "pipeline/run_etf_volume_study.py", "--start", start,
        "--end", end, "--out", STUDY_NAME, "--previous", "etf-volume-spike.json",
    ])
    assert fingerprints() == before, "研究阶段改变了生产文件或旧报告，停止验收"
    current = json.loads((ROOT / "data/research" / STUDY_NAME).read_text(encoding="utf-8"))
    assert current["algorithm_version"] == ev.ALGORITHM_VERSION
    assert {key: value for key, value in current["preregistered"].items() if key != "algorithm_version"} == {
        key: value for key, value in previous["preregistered"].items() if key != "algorithm_version"
    }, "冻结参数意外改变"
    assert current["lists"]["all_six"]["dates"] == ["2024-09-30", "2026-11-03"]
    assert current["lists"]["core"]["break_count"] == 6
    assert len(current["lists"]["broad"]["codes"]) == 10
    assert not current["lists"]["broad"]["failed"]
    assert all(row["verdict"] in study.VERDICTS for row in current["questions"])

    rebuilt = {}
    with tempfile.TemporaryDirectory(prefix=".tmp-etf-v1.2-", dir=ROOT) as tmp:
        folder = Path(tmp)
        with redirect_stdout(io.StringIO()) as build_log:
            build_dashboard.main(output_dir=folder)
            build_dashboard.main(validate_only=True, output_dir=folder)
        for name in ("index-signals.json", "sector-trends.json"):
            expected = json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
            actual = json.loads((folder / name).read_text(encoding="utf-8"))
            assert expected == actual, f"{name} 隔离重建后逐值不一致"
            rebuilt[name] = {"equal": True, "sha256": hashlib.sha256((folder / name).read_bytes()).hexdigest()}
        event_product = json.loads((folder / "etf-volume-events.json").read_text(encoding="utf-8"))
        original_events = json.loads((ROOT / "data/etf-volume-events.json").read_text(encoding="utf-8"))
        assert event_product["days"] == original_events["days"], "每日图表标记意外改变"
        lookup = {day["date"]: day for day in event_product["days"]}
        assert all(row["level"] == lookup[row["start_date"]]["level"] for row in event_product["events"])
        product_lookup = {row["start_date"]: row for row in event_product["events"]}
        for row in current["events"]:
            product = product_lookup[row["start_date"]]
            assert all(row[key] == product[key] for key in ("start_date", "level", "state")), \
                f"研究与生产首日属性不一致：{row['start_date']}"
        product_dates = sorted(day for day in product_lookup if start <= day <= end)
        assert product_dates == current["event_dates"], "研究与生产样本范围不一致"
        rebuilt["etf-volume-events.json"] = {"schema_valid": True, "daily_markers_unchanged": True,
                                             "event_levels_equal_first_day": True,
                                             "research_first_day_attributes_match": True,
                                             "research_event_count": len(current["events"])}
    comparison = same_input_comparison(start, end, current, previous)
    after = fingerprints()
    assert before == after, "隔离重建阶段改变了生产文件或旧报告，停止验收"
    record = {
        "algorithm_version": ev.ALGORITHM_VERSION, "range": {"start": start, "end": end},
        "old_report_input_hash": old["input_hash"], "current_input_hash": current["input_hash"],
        "input_changed_since_old_report": old["input_hash"] != current["input_hash"],
        "v1_1_report_input_hash": previous["input_hash"],
        "input_changed_since_v1_1": previous["input_hash"] != current["input_hash"],
        "before": before, "after": after, "guarded_files_unchanged": before == after,
        "tests": tests, "research": research, "isolated_rebuild": rebuilt,
        "build_log": build_log.getvalue(), "same_input_comparison": comparison,
        "browser_verified": False,
    }
    REPORT.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"验证记录：{REPORT}", flush=True)
    print("生产三份文件、两版旧研究与原始输入均未改变；旧信号隔离重建逐值一致，研究与生产事件首日属性一致。", flush=True)


if __name__ == "__main__":
    main()
