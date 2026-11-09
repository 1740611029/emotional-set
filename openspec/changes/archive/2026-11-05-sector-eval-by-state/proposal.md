# Proposal

## Why

板块换源（[2026-11-04-switch-sector-source-em](../archive/2026-11-04-switch-sector-source-em/proposal.md)）把「一级 + 二级」两层结构改为单层 31 个东财行业板块，产物中**唯一按四态分别给出胜率**的 `subsector_evaluation`（二级评估）随之移除，存活下来的板块级 `evaluation` 只剩**按段聚合的单一胜率**。聚合口径把不同状态的信息抹平了，实测差异很大（只读复算，31 个行业，研究段/验证段 × 10/20 日）：

| 段 | bull | warn | bear | rise |
|---|---|---|---|---|
| 研究段 10 日 | 52.14%（n=1,404） | 53.90%（1,128） | 53.42%（1,359） | 50.84%（1,184） |
| 验证段 10 日 | 57.75%（684） | 50.62%（565） | 43.12%（494） | 72.11%（441） |
| 验证段 20 日 | 61.38%（668） | 51.29%（544） | 39.32%（468） | 74.32%（440） |

聚合值（研究段 10 日 52.57%、验证段 10 日 55.49%）既看不出「空头态方向胜率低于 50%」（产物 `note` 里已有该提示，但产物本身没有数据能证明它），也看不出反转态与多头态的差异。上一变更的 delta 已把「板块级按四态拆分」明确标为**不在其范围、如需另开变更**，本次即补实现。

## What Changes

- **BREAKING** `data/sector-trends.json` 的 `evaluation.segments[*]`：由 `horizons: {"10": …, "20": …}`（该段全部板块聚合）改为 `states: {bull, warn, bear, rise} → {"10": …, "20": …}`，**聚合口径整体移除**（用户选定）。四态的 `samples` 之和恒等于原聚合 `samples`（同一批事件重新分组，不重算、不新增事件），因此信息无损。
- 每个 `state × horizon` 格子沿用既有字段：`entries`、`samples`、`successes`、`pending`、`rate`、`wilson95`、`small_sample`、`note`；四态键固定齐备，无事件的态为 `null`。
- 判定方向沿用既有口径（`bull`/`rise` 期望上涨，`warn`/`bear` 期望下跌），**四态判据、有向强度、持续天数、历史序列、算法版本号 `sector-donchian20-ma60-v2.0-em-source` 全部不动**；`research_status=post_hoc_exploratory_not_independent` 与「描述性分类、不代表交易收益」的说明保留。
- 产物契约（`pipeline/schemas.py` 的 `SECTOR_EVALUATION`）收紧为「必须四态齐备、不得再有 `horizons`」，旧产物在校验阶段被拒。
- 服务端快照透传不变（仍为 `sectors.evaluation` 原样转发），但前端 `types.ts` 的 `evaluation?: unknown` 收窄为具体类型。
- 前端板块趋势页新增「方向胜率验证」区块：按研究段/验证段 × 10/20 日 × 四态成表，每格同时给出胜率、已判定样本量、Wilson 95% 区间与小样本标记，并保留说明文案。
- 规格同步：`live-data-api` 的「快照携带板块评估」改为四态口径；`market-data-pipeline` 新增板块评估产物契约；`sector-trend-explorer` 新增该区块的展示要求。README 的「准确率披露」段与换源变更留痕里的旧数字一并更新。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `live-data-api`: 「快照携带板块评估」的口径由「该段全部板块的聚合方向胜率」改为「按四态分别给出」，且回调该要求按四态分别输出触发数、已判定样本数与胜率；`MUST NOT` 再以单一聚合百分比呈现。
- `market-data-pipeline`: 新增「板块评估四态统计产物契约」要求——产物必须输出 `states` 四态统计、四态样本之和必须等于该段该窗口的全部已判定样本、且不得再输出聚合 `horizons` 结构。
- `sector-trend-explorer`: 新增「四态方向胜率验证展示」要求——页面必须把这份评估以带样本量的表格/卡片呈现，小样本必须显式标记，且不得呈现为交易收益或买卖建议。

## Impact

- 流水线：`pipeline/confirmed_sector_trends.py::build_sector_evaluation`（改为按 `type` 分组）、`pipeline/research_algorithms.py::summarize_sector_events`（已有分组键能力，判定与配对逻辑不动）、`pipeline/schemas.py::SECTOR_EVALUATION`、`pipeline/build_dashboard.py`（调用处不变）。
- 服务端：`server/snapshot.py` 透传不变；`server/test_server.py` 相关断言按新结构核对。
- 前端：`signal-hub-web/src/data/types.ts`、`src/pages/Sectors.tsx`、新增展示组件（复用 `lib/sectorStates.ts` 的状态标签与配色），`tests/` 补纯函数用例。
- 测试：`pipeline/test_confirmed_sector_trends.py`（现有三个 evaluation 用例只断言 `horizons`，需改为四态）、`pipeline/test_io_utils.py`、`server/test_server.py`。
- 文档与留痕：`README.md`「准确率披露」段、`openspec/changes/archive/2026-11-04-switch-sector-source-em/tasks.md` 里「四态拆分属后续变更」的注记。
- **不影响**任何指数顶底信号、候选状态、板块四态/强度数值与行业宽度计算：改动只发生在事后评估的分组方式上，验证时以产物逐条相等（除 `evaluation` 外）为准。
