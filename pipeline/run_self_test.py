#!/usr/bin/env python3
"""外部信号调研自测脚本：隔离性、报告完整性与 OpenSpec 校验。

运行方式（在项目根目录）：
    .venv/Scripts/python pipeline/run_self_test.py

测试内容：
1. 调研脚本不改变生产信号文件（data/index-signals.json）的哈希。
2. 移除外部数据目录后重建，输出与保留外部数据时一致（两次重建均写入临时目录，不碰生产文件）。
3. OpenSpec 变更校验通过。
4. 调研报告包含必要的免责声明与前置条件段落。
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SIGNALS_JSON = ROOT / "data" / "index-signals.json"
EXTERNAL_DIR = ROOT / "data" / "raw" / "external"
SURVEY_MD = ROOT / "data" / "research" / "external-signal-survey.md"
SURVEY_JSON = ROOT / "data" / "research" / "external-signal-survey.json"


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(8192), b""):
            h.update(chunk)
    return h.hexdigest()


def _run(cmd: list[str], cwd: Path | None = None, timeout: int = 300) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, cwd=cwd or ROOT, capture_output=True, text=True, timeout=timeout)


def test_survey_does_not_alter_production_signals() -> None:
    """Task 5.1: 运行调研前后生产信号哈希一致。"""
    if not SIGNALS_JSON.exists():
        print("SKIP 5.1: data/index-signals.json 不存在，无法比对隔离性。")
        return
    before = _sha256(SIGNALS_JSON)
    # 重新运行调研脚本
    result = _run([sys.executable, str(ROOT / "pipeline" / "factor_survey.py")], timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"factor_survey.py 运行失败:\n{result.stderr}")
    after = _sha256(SIGNALS_JSON)
    assert before == after, (
        f"生产信号文件在调研运行后被修改！\n"
        f"  before: {before}\n  after : {after}"
    )
    print("PASS 5.1: 调研脚本未改变生产信号文件。")


def _rebuild_into(directory: Path) -> None:
    """把产物重建到指定目录，绝不写生产 data 目录。"""
    result = _run([sys.executable, str(ROOT / "pipeline" / "build_dashboard.py"),
                   "--output-dir", str(directory)], timeout=600)
    if result.returncode != 0:
        raise RuntimeError(f"build_dashboard.py 重建失败（退出码 {result.returncode}）:\n{result.stderr}")


def test_production_signals_unchanged_without_external_data() -> None:
    """Task 5.2: 临时移除外部数据后重建，输出不变；两次重建均写临时目录，生产文件不得被触碰。"""
    if not SIGNALS_JSON.exists():
        print("SKIP 5.2: data/index-signals.json 不存在。")
        return
    production_before = _sha256(SIGNALS_JSON)

    with tempfile.TemporaryDirectory(prefix="selftest_52_") as temp:
        root = Path(temp)
        with_external = root / "with-external"
        without_external = root / "without-external"
        _rebuild_into(with_external)

        stored = root / "external-store"
        moved = EXTERNAL_DIR.exists()
        if moved:
            shutil.move(str(EXTERNAL_DIR), str(stored))
        try:
            _rebuild_into(without_external)
        finally:
            if moved:
                shutil.move(str(stored), str(EXTERNAL_DIR))

        for name in ("index-signals.json", "sector-trends.json"):
            before = _sha256(with_external / name)
            after = _sha256(without_external / name)
            assert before == after, (
                f"移除外部数据后 {name} 改变！\n"
                f"  with   : {before}\n  without: {after}"
            )

    after_production = _sha256(SIGNALS_JSON)
    assert production_before == after_production, (
        "自检用例改写了生产 index-signals.json！\n"
        f"  before: {production_before}\n  after : {after_production}"
    )
    print("PASS 5.2: 移除外部数据后重建结果未变，且生产文件未被触碰。")


def test_openspec_validate() -> None:
    """Task 5.3: OpenSpec 校验通过（全域，含主规格与活跃变更）。"""
    import shutil
    openspec_cmd = shutil.which("openspec")
    if openspec_cmd is None:
        openspec_cmd = shutil.which("openspec.cmd")
    if openspec_cmd is None:
        raise RuntimeError("openspec 命令未找到，请确认已安装 OpenSpec CLI。")
    # 用 --all 避免硬编码变更名：变更归档后旧名字会从 changes 列表消失，使本用例长期红着
    result = _run([openspec_cmd, "validate", "--all", "--strict"])
    if result.returncode != 0:
        raise AssertionError(
            f"openspec validate --all --strict 失败（退出码 {result.returncode}）:\n"
            f"{result.stdout}\n{result.stderr}"
        )
    print("PASS 5.3: OpenSpec validate --all --strict 通过。")


def test_report_contains_disclaimers() -> None:
    """Task 5.4: 报告包含免责声明与前置条件。"""
    assert SURVEY_MD.exists(), f"报告文件不存在: {SURVEY_MD}"
    text = SURVEY_MD.read_text(encoding="utf-8")
    required_phrases = [
        "不改变生产准确率",
        "前置条件",
        "独立区间验证",
        "旧版本留痕",
        "准确率对照",
    ]
    missing = [p for p in required_phrases if p not in text]
    assert not missing, f"报告缺少以下必要段落: {missing}"
    print("PASS 5.4: 报告包含必要的免责声明与前置条件。")


def test_json_structure() -> None:
    """补充：JSON 报告结构完整性。"""
    assert SURVEY_JSON.exists(), f"JSON 不存在: {SURVEY_JSON}"
    with open(SURVEY_JSON, encoding="utf-8") as fh:
        data = json.load(fh)
    assert "preregistered" in data, "JSON 缺少 preregistered 字段"
    assert "decisions" in data, "JSON 缺少 decisions 字段"
    for name, decision in data["decisions"].items():
        assert decision.get("verdict") in ("采纳", "观察", "放弃"), f"{name} 结论非法"
        if decision["verdict"] == "放弃":
            assert decision.get("reason"), f"{name} 放弃项缺少理由"
    print("PASS JSON: 报告 JSON 结构完整，结论三态化且放弃项有理由。")


def main() -> int:
    tests = [
        test_survey_does_not_alter_production_signals,
        test_production_signals_unchanged_without_external_data,
        test_openspec_validate,
        test_report_contains_disclaimers,
        test_json_structure,
    ]
    passed = 0
    failed = 0
    for test in tests:
        try:
            test()
            passed += 1
        except Exception as exc:
            print(f"FAIL {test.__name__}: {exc}")
            failed += 1
    print(f"\n{'='*50}")
    print(f"结果: {passed} 通过, {failed} 失败")
    return 0 if failed == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
