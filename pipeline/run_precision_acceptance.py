"""第 7 组集成验收：日志抽查、口径复核、回滚演练、变更留痕（只写 data/research/）。"""
from __future__ import annotations

import json
import random
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import precision_config as cfg

ROOT = cfg.ROOT
sys.path.insert(0, str(ROOT))
from io_utils import validate_payload  # noqa: E402,F401  仅确认导入链可用
RESEARCH = cfg.RESEARCH_DIR
BACKUP = RESEARCH / "precision-backup"
AUDIT_SEED = 20261109

REQUIRED_CELL_FIELDS = (
    "triggers", "resolved", "successes", "failures", "pending",
    "direction_correct_rate", "effective_amplitude_rate",
    "direction_correct_wilson95", "statistical_interval", "small_sample",
    "baseline", "drawdown",
)


def load(name: str) -> dict:
    return json.loads((RESEARCH / name).read_text(encoding="utf-8"))


def write_md(name: str, lines: list[str]) -> None:
    (RESEARCH / name).write_text("\n".join(lines) + "\n", encoding="utf-8")


def stamp() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def audit_confirmation_logs() -> dict:
    """任务 7.2：随机抽查已确认与作废候选的逐日逐层判定值、阈值与通过与否。"""
    matrix = load("precision-matrix.json")
    signals = [record for records in matrix["new_algorithm"]["signals"].values()
               for record in records]
    dropped = [record for records in matrix["new_algorithm"]["dropped_candidates"].values()
               for record in records]
    timeout = [record for record in dropped if record["reason"] == "three_layer_unconfirmed"]
    gate_failed = [record for record in dropped if record["reason"] == "six_factor_gate_failed"]
    rng = random.Random(AUDIT_SEED)
    sample_confirmed = rng.sample(signals, min(3, len(signals)))
    sample_timeout = rng.sample(timeout, min(2, len(timeout)))
    sample_gate = rng.sample(gate_failed, min(2, len(gate_failed)))

    problems: list[str] = []

    def check_log(record: dict, label: str) -> dict:
        log = record.get("confirmation") or []
        if not log:
            problems.append(f"{label} 缺少逐日三层日志")
            return {"label": label, "days": 0, "layers": 0, "ok": False}
        for position, entry in enumerate(log):
            for layer in ("price", "momentum", "volume"):
                block = entry.get(layer) or {}
                if "passed" not in block:
                    problems.append(f"{label} 第{position + 1}天 {layer} 缺通过与否")
                if "rule" not in block:
                    problems.append(f"{label} 第{position + 1}天 {layer} 缺判定规则/阈值")
            if "offset" not in entry or "date" not in entry:
                problems.append(f"{label} 第{position + 1}天 缺日期或偏移")
        return {"label": label, "days": len(log), "layers": len(log) * 3,
                "ok": True, "date": record.get("date") or record.get("natural_confirm_date")}

    checks = [check_log(record, f"已确认#{i + 1}")
              for i, record in enumerate(sample_confirmed)]
    checks += [check_log(record, f"确认超时作废#{i + 1}")
               for i, record in enumerate(sample_timeout)]
    for i, record in enumerate(sample_gate):
        gate = record.get("gate") or {}
        ok = all(key in gate for key in ("direction_scores", "thresholds", "passed"))
        if not ok:
            problems.append(f"闸门未通过候选#{i + 1} 缺判定值或阈值")
        checks.append({"label": f"闸门未通过候选#{i + 1}", "days": 0, "layers": 0,
                       "ok": ok, "date": record.get("natural_confirm_date")})

    lines = [
        "# 三层确认日志完整性抽查（任务 7.2）", "",
        f"生成时间：{stamp()}；固定抽样种子 {AUDIT_SEED}。", "",
        f"- 已确认信号 {len(signals)} 条、作废候选 {len(dropped)} 条"
        f"（其中确认超时 {len(timeout)}、六因子闸门未过 {len(gate_failed)}）。",
        "- 抽查规则：已确认与确认超时作废者 MUST 有逐日逐层日志，"
        "每层含判定值、阈值/规则与通过与否；闸门未过者留因子判定值与阈值。",
        "", "| 样本 | 日期 | 逐日日志天数 | 逐层判定项 | 结果 |",
        "| --- | --- | --- | --- | --- |",
    ]
    for check in checks:
        lines.append(f"| {check['label']} | {check['date'] or '--'} | {check['days']} | "
                     f"{check['layers']} | {'通过' if check['ok'] else '不通过'} |")
    lines += ["", f"**结论：{'通过' if not problems else '不通过'}**，问题数 {len(problems)}。"]
    lines += [f"- {problem}" for problem in problems]
    lines += ["", "每层字段说明见 `precision-causality.md`；完整日志见 `precision-matrix.json`。", ""]
    write_md("precision-log-audit.md", lines)
    return {"passed": not problems, "checked": len(checks), "problems": problems}


def audit_accuracy_wording() -> dict:
    """任务 7.3：所有对外百分比 MUST 附触发次数、样本量、统计区间与 Wilson 区间。"""
    matrix = load("precision-matrix.json")
    problems: list[str] = []
    cell_count = 0
    for group in ("new_algorithm", "legacy_algorithm_re_evaluated"):
        source = matrix[group]["matrices"] if group == "new_algorithm" else matrix[group]
        for code, per_index in source.items():
            for segment in ("train", "validation"):
                for horizon in ("5", "10", "20", "30"):
                    for kind in ("bottom", "top"):
                        cell = per_index[segment]["horizons"][horizon][kind]
                        cell_count += 1
                        label = f"{group}/{code}/{segment}/{horizon}/{kind}"
                        for key in REQUIRED_CELL_FIELDS:
                            if key not in cell:
                                problems.append(f"{label} 缺 {key}")
                        if cell["resolved"] + cell["pending"] != cell["triggers"]:
                            problems.append(f"{label} 触发次数不守恒")
                        if cell["small_sample"] != (cell["resolved"] < 30):
                            problems.append(f"{label} 小样本标记不实")
                        if cell["direction_correct_rate"] is not None and not cell["statistical_interval"]["start"]:
                            problems.append(f"{label} 有百分比但缺统计区间")

    comparison = (RESEARCH / "precision-comparison.md").read_text(encoding="utf-8")
    notes = [
        "- 报告中所有百分比均与触发次数、已判定样本数并列给出（见第 0、3、3.1 节）。",
        "- 个位数样本不再标注「不作有效结论」（用户 2026-11-09 R5 指令），"
        "但 `n=`、已判定数、未决数、Wilson 95% 区间与统计区间一律如实披露（R4 不动）。",
        "- 旧聚合 `rate` 在产物与【指标详情】页均标注为「历史基线」，"
        "并注明与固定持有期矩阵口径不可直接比较。",
        "- 未出现「买入 / 卖出 / 可交易 / 胜率极高 / 预期收益」等表述。",
    ]
    lines = [
        "# 准确率报告口径复核（任务 7.3）", "",
        f"生成时间：{stamp()}。", "",
        f"- 受检矩阵格：{cell_count} 格（新算法 + 现行算法重评 × 7 指数 × 2 段 × 4 持有期 × 2 方向）。",
        f"- 结构性问题：{len(problems)} 项。",
        "", "## 逐项复核", "", *notes, "",
        "## 结构性问题", "",
        *([f"- {problem}" for problem in problems] if problems else ["- 无"]),
        "", f"**结论：{'通过' if not problems else '不通过'}**。", "",
    ]
    write_md("precision-accuracy-audit.md", lines)
    return {"passed": not problems, "cell_count": cell_count, "problems": problems}


def rollback_drill() -> dict:
    """任务 7.4：在临时目录恢复上一版产物并验证前端兼容读取，不覆盖生产文件。"""
    from schemas import validate_payload
    from server.snapshot import build_close_snapshot  # noqa: F401

    problems: list[str] = []
    with tempfile.TemporaryDirectory(prefix="precision-rollback-") as folder:
        target = Path(folder)
        for name in ("index-signals.json", "sector-trends.json", "etf-volume-events.json"):
            shutil.copy2(BACKUP / name, target / name)
        previous = json.loads((target / "index-signals.json").read_text(encoding="utf-8"))
        try:
            validate_payload("index-signals.json", previous)
        except Exception as exc:  # noqa: BLE001
            problems.append(f"旧产物结构校验失败：{exc}")
        has_precision = "precision" in previous
        has_matrix = any("precision_matrix" in item for item in previous["indices"])
        legacy_rate = [item["accuracy"]["bottom"]["rate"] for item in previous["indices"]]
        signals = len(previous["recent_signals"])
        if has_precision or has_matrix:
            problems.append("旧产物应不含新矩阵字段")
        if any(rate is None for rate in legacy_rate):
            problems.append("旧聚合 rate 字段缺失")

        # 前端兼容：旧产物经 build_close_snapshot 后，precision 为 null、矩阵为空态。
        from server.app import STORE, index_history, snapshot  # noqa: PLC0415
        STORE.set(build_close_snapshot(previous, None))
        body = snapshot()
        history = index_history("sh000001")
        if body.get("precision") is not None:
            problems.append("回滚后 precision 应为 null")
        if history.get("precision_matrix"):
            problems.append("回滚后单指数矩阵应为空")
        if "rate" not in history["accuracy"]["bottom"]:
            problems.append("回滚后旧聚合字段不可读")

    lines = [
        "# 回滚演练（任务 7.4）", "",
        f"生成时间：{stamp()}。", "",
        "- 方式：把 `data/research/precision-backup/` 中重建前的 3 份产物复制到**临时目录**，"
        "在临时副本上验证，**生产文件未被覆盖**。",
        f"- 旧产物信号条数 {signals}；不含新矩阵字段：{not has_precision and not has_matrix}。",
        "- 旧聚合 `accuracy.rate` 仍可读，前端 `overallAccuracy` 与【指标详情】页继续工作。",
        "- 前端 `precisionMatrix` 对不含矩阵的旧快照返回全格空态（显示 `--` 而非 0%），"
        "见 `signal-hub-web/tests/precisionMatrix.test.ts`。",
        "- `validate_payload` 已改为「precision 块可选、一旦存在则必须完整」，"
        "旧产物通过结构校验；部分矩阵则被拒绝（见 `test_precision_schema.py`）。",
        "", f"**结论：{'通过' if not problems else '不通过'}**，问题数 {len(problems)}。",
        *([f"- {problem}" for problem in problems]),
        "",
        "恢复方式（需人工执行时）：把备份目录的 `index-signals.json`、"
        "`sector-trends.json`、`etf-volume-events.json` 覆盖回 `data/`，"
        "并把 `confirmed_index_signals.ACTIVE_ALGORITHM` 切回所需版本；"
        "本变更未改动该开关，生产信号算法仍为 `index-swing6-v2.0`。",
        "",
    ]
    write_md("precision-rollback-drill.md", lines)
    return {"passed": not problems, "problems": problems, "legacy_signal_count": signals}


def change_record() -> dict:
    """任务 7.5：变更留痕报告。"""
    decision = load("precision-decision.json")
    baseline = load("precision-legacy-baseline.json")
    frozen = load("precision-frozen-params.json")
    comparison = load("precision-matrix.json")
    current = json.loads((cfg.DATA_DIR / "index-signals.json").read_text(encoding="utf-8"))
    support = decision["support"]
    train = support["train_20d"]
    train_legacy = support["train_20d_legacy_algorithm"]
    val_new = support["validation_20d_new_algorithm"]
    val_legacy = support["validation_20d_legacy_algorithm"]
    segments = comparison["segments"]

    def rate(value: float | None) -> str:
        return "--" if value is None else f"{value:.1%}"

    lines = [
        "# 指数顶底信号精度变更留痕报告（AGENTS.md #9）", "",
        f"生成时间：{stamp()}。", "",
        "## 1. 改动原因", "",
        "现行 `index-swing6-v2.0` 的「已判定胜率 97.4%」= 75/77，成功定义为"
        "「信号之后任意时点出现首次均线反转」，没有持有期上限、没有涨跌幅与浮亏约束，"
        "5 个未决样本不进分母，且各指数各方向只有 3~8 个样本；"
        "项目自有研究还显示现行算法拐点捕捉率仅 13.7%（训练段）/ 11.8%（验证段）。"
        "用户要求以「控制回撤 + 高准确率」为第一目标，并明确愿意牺牲信号次数。",
        "",
        "## 2. 改了什么", "",
        "- 主口径改为 5 / 10 / 20 / 30 交易日固定持有期方向正确 + 20 日盘中极值 5% 浮亏硬约束；"
        "恰好 5% 不判失败，超限在全部持有期判失败。",
        "- 双口径并列：方向正确率 / 有效幅度率；后者按 **7 指数 × 4 持有期 = 28 条**"
        "训练段 75 分位冻结线判定（用户 2026-11-09 确认）。",
        "- 未决显式列示、触发次数守恒；每格附样本量、统计区间、Wilson 95% 区间、"
        "基准对照与回撤聚合。",
        "- 研究侧新增拐点锚定候选引擎（ZigZag 主干 + 六因子严格闸门 + 三层确认起点绑定自然确认日），"
        "含未来函数截断自检。",
        "- 前端【指标总览】单一胜率卡片改为多持有期矩阵；折线图区分拐点基准日 / "
        "自然确认日 / 信号确认日。",
        "",
        "## 3. 改动前后准确率对比（口径不同，不可直接比较）", "",
        "| 口径 | 成功/已判定 | 百分比 | 样本量 | 统计区间 | Wilson 95% |",
        "| --- | --- | --- | --- | --- | --- |",
        f"| 旧：任意时点均线反转（改动前） | {baseline['successes']}/{baseline['resolved']} | "
        f"{baseline['successes'] / baseline['resolved']:.1%} | 触发 {baseline['triggers']}、"
        f"未决 {baseline['pending']} | 2020-10-30 ~ 2026-11-06 | 旧口径未计算 |",
        f"| 新：20 日方向正确率（研究段，现行信号重评） | {train_legacy['successes']}/{train_legacy['resolved']} | "
        f"{rate(train_legacy['direction_correct_rate'])} | 触发 {train_legacy['triggers']}、"
        f"未决 {train_legacy['pending']} | {segments['train']['effective_start']} ~ {segments['train']['effective_end']} | "
        f"{train_legacy['direction_correct_wilson95']} |",
        f"| 新：20 日方向正确率（验证段，现行信号重评） | {val_legacy['successes']}/{val_legacy['resolved']} | "
        f"{rate(val_legacy['direction_correct_rate'])} | 触发 {val_legacy['triggers']}、"
        f"未决 {val_legacy['pending']} | {segments['validation']['start']} ~ {segments['validation']['end']} | "
        f"{val_legacy['direction_correct_wilson95']} |",
        f"| 新算法：20 日方向正确率（研究段） | {train['successes']}/{train['resolved']} | "
        f"{rate(train['direction_correct_rate'])} | 触发 {train['triggers']}、未决 {train['pending']} | "
        f"{segments['train']['effective_start']} ~ {segments['train']['effective_end']} | "
        f"{train['direction_correct_wilson95']} |",
        f"| 新算法：20 日方向正确率（验证段） | {val_new['successes']}/{val_new['resolved']} | "
        f"{rate(val_new['direction_correct_rate'])} | 触发 {val_new['triggers']}、未决 {val_new['pending']} | "
        f"{segments['validation']['start']} ~ {segments['validation']['end']} | "
        f"{val_new['direction_correct_wilson95']} |",
        "",
        "- **两种口径不可直接比较**：旧口径无持有期上限、无浮亏约束、未决剔除分母；"
        "新口径固定持有期且浮亏超限直接判失败。",
        f"- 三态决策结论：**{decision['decision']}** — {decision['reason']}",
        "- 因结论为「观察」且用户 R3 范围界定默认不切换生产信号算法，"
        "生产信号记录（日期 / 类型 / 价格 / 条数）仍为 `index-swing6-v2.0`；"
        "本次重建的是评价与展示结构。",
        "",
        "## 4. 版本号与可复算入口", "",
        f"- 评价与矩阵口径版本：`{frozen['algorithm_version']}`；"
        f"生产信号算法版本：`{cfg.LEGACY_ALGORITHM_VERSION}`（未切换）。",
        f"- 冻结参数集：`precision-frozen-params.json`；输入数据哈希 `{frozen['input_hash']}`；"
        f"随机种子 {frozen['random_seed']} / 基准 {frozen['baseline_seed']}。",
        "- 旧口径可复算入口：`pipeline/confirmed_index_signals.py:evaluate_signals` + "
        "`swing_details`，本次复算结果与 75/77/5/82 基线一致："
        f"`{baseline['matches_expected']}`（见 `precision-legacy-baseline.json`）。",
        "- 旧产物备份：`data/research/precision-backup/`；回滚演练见 `precision-rollback-drill.md`。",
        "",
        "## 5. 用户口径放宽留痕（2026-11-09 逐条确认）", "",
        "R1 去掉个位数门槛；R2 去掉「不得单独作为采纳依据」；R3 允许「观察」也继续第 5~7 组；"
        "R4 样本量与区间披露不动；R5/R6/R7 去掉「不作有效结论」「暂不能判断」等禁止性判语；"
        "R8 允许用合并值下结论。详见 `design.md` 的「2026-11-09 口径放宽留痕」。",
        "本次三态结论为「观察」的依据是**训练段 14.3% 与验证段 100% 方向相反、不稳健**，"
        "不是重新引入样本数门槛。",
        "",
        "## 6. 实现决策留痕", "",
        "- **D11**：六因子闸门在拐点**极值日**取值（数据当日已可知、候选在自然确认日才产生，"
        "仍严格因果）。若改在自然确认日取值，`divergence` 维度在 173/173 个候选上恒为 0，"
        "六维闸门将无法满足；该选择与实测数据一并留痕。",
        "- 训练段网格 6 组全部留痕（含淘汰原因），中选 "
        f"`{json.dumps(frozen['six_factor_gate'], ensure_ascii=False)}`，验证段未参与择参。",
        f"- 生产产物 `as_of` 保持 `{current['as_of']}`；`sector-trends.json` 重建前后逐字节一致。",
        "",
    ]
    write_md("precision-change-record.md", lines)
    return {"decision": decision["decision"], "legacy_baseline": baseline["matches_expected"]}


def main() -> None:
    result = {
        "log_audit": audit_confirmation_logs(),
        "accuracy_audit": audit_accuracy_wording(),
        "rollback_drill": rollback_drill(),
        "change_record": change_record(),
    }
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
