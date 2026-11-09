# drawdown-metrics Specification

## Purpose
定义回撤指标的计算口径与展示约束，使项目中任何对外展示的绩效指标都必须与回撤并列，避免只报胜率、不报代价的片面结论。

## Requirements

### Requirement: 回撤指标定义

系统 MUST 为每个已确认信号计算以下回撤指标，且 MUST 明确以信号日收盘价为基准：

- `max_adverse_excursion`：从信号日次日起至确认日（未决时至评价区间末尾）之间，底部信号取最低价相对信号价的最大跌幅，顶部信号取最高价相对信号价的最大涨幅；
- `max_adverse_excursion_20`：同上，但窗口固定为信号日后 20 个交易日；
- `max_drawdown_20`：信号后 20 个交易日内，按收盘价计算的相对信号价的最大不利偏移；
- `forward_return_5/10/20/30`：信号后第 5 / 10 / 20 / 30 个交易日收盘相对信号价的收益；
- `max_adverse_excursion_30`：窗口固定为信号日后 30 个交易日的最大不利偏移。

浮亏硬约束 MUST 固定使用 `max_adverse_excursion_20`（盘中最低/最高价口径），MUST NOT 改用收盘价口径 `max_drawdown_20` 来放宽判定。

#### Scenario: 底部信号回撤取最低价
- **WHEN** 某 `bottom` 信号价格为 3000，信号后 20 个交易日内最低价为 2850
- **THEN** `max_adverse_excursion_20` MUST 为 `-5.0%`（以小数表示即 -0.05）

#### Scenario: 顶部信号回撤取最高价
- **WHEN** 某 `top` 信号价格为 3000，信号后 20 个交易日内最高价为 3120
- **THEN** `max_adverse_excursion_20` MUST 为 `-4.0%`（顶部的不利方向为上涨）

#### Scenario: 硬约束用盘中极值而非收盘价
- **WHEN** 某 `bottom` 信号盘中最低价较信号价低 5.4%，但 20 日内收盘价最低仅低 3.1%
- **THEN** 浮亏硬约束 MUST 以 5.4% 判定超限，MUST NOT 以 3.1% 判定为达标

### Requirement: 回撤聚合

每个准确率汇总 MUST 同时给出：已判定样本的平均最大不利偏移、最差最大不利偏移、平均 20 日前瞻收益、平均 30 日前瞻收益、收益回撤比（平均 20 日前瞻收益 / 平均最大不利偏移的绝对值），以及因浮亏超限而被判失败的信号数与占全部触发数的比例。无已判定样本时上述字段 MUST 为 `null`，MUST NOT 用 0 代替。多持有期矩阵的每一格 MUST 携带本格对应的回撤聚合值。

#### Scenario: 汇总带平均与最差回撤
- **WHEN** 某指数底部信号有 5 个已判定样本
- **THEN** 汇总 MUST 包含 `avg_max_adverse_excursion`、`worst_max_adverse_excursion`、`avg_forward_return_20`、`avg_forward_return_30`、`return_drawdown_ratio` 与浮亏超限失败数

#### Scenario: 无样本时不填零
- **WHEN** 某方向没有任何已判定样本
- **THEN** 回撤聚合字段 MUST 为 `null`

### Requirement: 回撤不参与胜率计算

本要求经本变更单独评审后**撤销**。最大不利偏移 MUST 参与每个持有期的成败判定：信号后 20 个交易日内 `max_adverse_excursion_20` 的绝对值超过 **5%** 时，该信号 MUST 在所有 5 / 10 / 20 / 30 持有期格一律记为 `failure`，无论期末收益方向是否正确、也无论后续是否出现均线反转。等于 5% 视为未超限。浮亏阈值 MUST 在训练段冻结，验证段 MUST NOT 调整；阈值变更 MUST 视为独立变更单独评审并更新算法版本号。

#### Scenario: 浮亏超限直接判失败
- **WHEN** 某 `bottom` 信号价格为 3000，信号后 20 个交易日内最低价为 2840（-5.33%），第 20 日收盘为 3150
- **THEN** 该信号在全部持有期格 MUST 记为 `failure`，MUST NOT 记为 `success`

#### Scenario: 恰好等于阈值不判失败
- **WHEN** 某 `bottom` 信号后 20 个交易日内最大浮亏恰为 5.00%，期末收益方向正确
- **THEN** 该信号 MUST 按方向判定，MUST NOT 因浮亏被判 `failure`

#### Scenario: 阈值验证段不调整
- **WHEN** 在独立验证段发现 5% 阈值导致信号过少
- **THEN** 系统 MUST NOT 在验证段调整阈值，MUST 记录为待评审事项并按原阈值报告结果

#### Scenario: 改回撤窗口不影响胜率
- **WHEN** 将回撤的展示窗口从 20 日改为 10 日（仅用于展示，判定窗口仍固定为 20 日）
- **THEN** 所有信号的成败判定 MUST 完全不变；浮亏硬约束 MUST 仍以 20 日窗口的盘中极值为准，MUST NOT 随展示窗口变化

### Requirement: 信号生成不使用事后回撤

信号生成 MUST 只使用截至信号日的历史回撤（例如距 250 日高点回撤的分位），MUST NOT 使用信号日之后出现的回撤。事后回撤指标 MUST 只出现在评价结果中。

#### Scenario: 截断末尾数据不改变历史信号
- **WHEN** 将行情截断到某历史日期后重新计算信号
- **THEN** 截断日之前的全部信号 MUST 与未截断时完全一致

### Requirement: 页面绩效指标必须与回撤并列

所有展示绩效指标的页面（仪表盘汇总、宽基指数卡片、指标详情、信号明细表）MUST 在展示胜率或收益的同时展示对应的回撤指标。多持有期准确率矩阵的每一格 MUST 在紧邻位置展示该格的平均最大不利偏移与浮亏超限失败数。缺少回撤的绩效展示 MUST 视为未完成，不得上线。页面 MUST 用业务语言说明浮亏约束的含义（例如「信号后中途最多允许浮亏 5%，超出即判失败」），MUST NOT 只显示字段名或阈值数字而不解释。

#### Scenario: 胜率卡片并列回撤
- **WHEN** 仪表盘展示准确率矩阵
- **THEN** 每格 MUST 同时展示平均最大不利偏移与浮亏超限失败数

#### Scenario: 指数卡片并列回撤
- **WHEN** 宽基指数卡片展示胜率
- **THEN** 该卡片 MUST 同时展示该指数的平均最大回撤

#### Scenario: 浮亏阈值有大白话说明
- **WHEN** 页面首次展示 5% 浮亏约束
- **THEN** 页面 MUST 显示通俗解释与示例，MUST NOT 只显示「MAE ≤ 5%」等技术表述

#### Scenario: 回撤缺失时显式标注
- **WHEN** 某指数尚无已判定样本，无法计算回撤
- **THEN** 页面 MUST 显示 `--` 或"暂无样本"，MUST NOT 省略该字段或显示 0
