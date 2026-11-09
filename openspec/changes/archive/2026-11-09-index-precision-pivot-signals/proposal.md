# Proposal

## Why

现行 `index-swing6-v2.0` 以六因子情绪/极端度共振产生候选，强制等待 1–3 个交易日完成价格→动量→量能三层确认，并以**确认日**作为对外信号日；候选条件本身不是拐点定位器。项目自有研究已量化该问题：训练段拐点捕捉率 13.7%、验证段 11.8%，最近同类信号偏移中位数约 83 个交易日（`pipeline/pivot_eval.py`、`data/research/index-pivot-comparison.md`）。同时「已判定胜率 97.4%」= 75/77，其成功定义为「信号后到下一个反向信号或段末之前的**任意时点**出现首次均线反转」，无持有期上限、无涨跌幅阈值；5 个未决样本不进分母，且只在已通过三层确认的信号上评估，各指数各方向仅 3–8 个样本。该数字是事后探索性比率，与用户在折线图上观察到的实际走势不符。

用户需要的是一套以**控制回撤、高准确率为第一目标**的顶底信号指标，并明确表示宁愿显著牺牲信号次数。本变更同时解决信号定位偏离与准确率口径失真两个问题。

## What Changes

- **BREAKING** 主准确率口径由「后续任意时点首次均线反转」改为「固定持有期方向正确 + 单信号最大不利偏移硬约束」，按 **5 / 10 / 20 / 30 个交易日**分别列出准确率与触发次数。
- **BREAKING** 单信号浮亏约束：信号后 20 个交易日内，底部信号最低价相对信号日收盘价跌幅 >5%（顶部信号最高价相对信号日收盘价涨幅 >5%）即判 `failure`，无论期末收益方向是否正确。
- 信号候选改由价格结构拐点（ZigZag 事后确认制）锚定，三层确认的起点 MUST 绑定拐点自然确认日、不得早于该日；保留价格确认 → 动量确认 → 量能确认三层全部通过的完整机制（不破坏 `AGENTS.md` #11），并提高候选与确认门槛以压缩信号次数。
- 未决样本显式记为 `pending` 并展示其数量，不再静默从分母剔除；每个持有期并列报「方向正确率」与「有效幅度率」，后者的幅度线按每个指数波动特性 × 每个持有期分别冻结（训练段 H 日绝对收益 75 分位，共 28 条），避免只呈现最宽松的单一百分比。
- 评价与调参标的池**固定为现有 7 大宽基指数，不扩展**；同花顺指数日线、前复权口径不变；生产展示仍为 7 张图卡，界面 MUST 标注统计所覆盖的标的范围与样本量。
- 严格划分训练段 / 隔离期 / 独立验证段，参数只在训练段选取；所有准确率附样本量、统计区间与 Wilson 95% 区间；样本量偏少时数字如实披露，不再以样本数门槛禁止给出结论（2026-11-09 用户逐条确认，见 `design.md` 留痕）。
- 保留 `index-swing6-v2.0` 可复算入口与产物，输出新旧算法在同一段历史上的信号条数、准确率矩阵与回撤对照，记录改动原因。
- 算法版本号更新；三层确认逐层日志（判定值、阈值、通过与否）保持完整输出。

**口径不变项**：复权方式（指数前复权 / 板块不复权）、数据源（同花顺指数日线 / 东财 31 个单层行业板块）、字段顺序与价格单位、行业宽度计算均不更换。本次不更换数据口径，仅扩展同类数据源下的标的池与统计区间。

**评审结论（2026-11-09 已确认）**：不扩展标的池，统计验证与生产展示均只用现有 7 大宽基指数；7 张指数图卡不变，界面标注「统计覆盖 7 个标的」。有效幅度线按指数 × 持有期分档冻结（28 条，训练段 75 分位）。样本量门槛按用户逐条指令放宽，改动留痕见 `design.md` 的「2026-11-09 口径放宽留痕」与「R3 范围界定」。此结论不改变上述四项已确认口径。

## Capabilities

### New Capabilities

无。本次全部为既有 capability 的行为要求变更，不引入近似名的新 capability。

### Modified Capabilities

- `index-signal-engine`: 候选由拐点结构锚定；三层确认起点绑定拐点自然确认日；提高门槛压缩信号次数；输出新增拐点基准信息；版本留痕。
- `index-signal-evaluation`: **BREAKING** 主胜率口径改为 5/10/20/30 持有期矩阵；触发次数、未决数、双口径、Wilson 区间与基准对照随口径同步更新。
- `drawdown-metrics`: **BREAKING** 撤销「回撤不参与胜率判定」，改为 MAE 5% 超限判失败；补齐 30 日窗口指标与页面并列展示。
- `index-signal-research`: 固定 7 指数清单下的训练/验证分段冻结、隔离期、28 条幅度线留痕与复现约束。
- `market-data-pipeline`: 生产 7 指数清单固化与 manifest；指数信号输出的准确率摘要适配新矩阵结构。
- `live-data-api`: `/api/snapshot` 与 `/api/indices/{code}/history` 的 `accuracy` / `evaluation` 结构扩展。
- `live-data-ui`: 【指标总览】单一「已判定胜率」卡片改为多持有期矩阵，逐格给出准确率、触发次数、未决数与小样本提示。

## Impact

- **信号计算**：`pipeline/swing_signal_engine.py`、`pipeline/pivot_algorithms.py`、`pipeline/pivot_eval.py`、`pipeline/confirmed_index_signals.py`、`pipeline/candidate_state.py`、`pipeline/research_algorithms.py`。
- **产物与校验**：`pipeline/build_dashboard.py`、`pipeline/schemas.py`、`data/index-signals.json`（`accuracy` / `evaluation` 结构变化，需结构校验）、`data/research/`（新增新旧对照产物，调研阶段 MUST NOT 触碰生产 JSON）。
- **服务与前端**：`server/app.py`（`/api/snapshot`、`/api/indices/{code}/history`）；`signal-hub-web/src/data/MarketDataProvider.tsx`、`src/pages/Dashboard.tsx`、`src/lib/indexSignalCharts.ts`、`src/components/IndexSignalChartCard.tsx`。折线图 marker 仍取确认日，但需同时可见拐点基准日以便对照。
- **测试**：`test_confirmed_index_signals.py`、`test_swing_signal_engine.py`、`test_pivot_eval.py` 及新增持有期矩阵、MAE 硬约束、未来函数截断自检用例。
- **规则约束**：`AGENTS.md` #4（影响信号逻辑）已通过用户对本变更四项口径的明确确认获得授权；#1 禁止未来函数、#3 只做本次需求、#7 调参与验证分离、#9 算法变更留痕、#12 三层确认日志完整、#13 UI 清新淡色风格一致性均为硬性验收项。#8 的个位数门槛已于 2026-11-09 按用户逐条指令放宽，但样本量、统计区间与 Wilson 区间的披露仍为硬性验收项；生产信号算法是否切换须在 5.2 前单独确认。
