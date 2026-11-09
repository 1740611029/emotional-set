# Spec Delta

## Purpose

定义回撤指标的计算口径与展示约束，使项目中任何对外展示的绩效指标都必须与回撤并列，避免只报胜率、不报代价的片面结论。

## ADDED Requirements

### Requirement: 回撤指标定义

系统 MUST 为每个已确认信号计算以下回撤指标，且 MUST 明确以信号日收盘价为基准：

- `max_adverse_excursion`：从信号日次日起至确认日（未决时至评价区间末尾）之间，底部信号取最低价相对信号价的最大跌幅，顶部信号取最高价相对信号价的最大涨幅；
- `max_adverse_excursion_20`：同上，但窗口固定为信号日后 20 个交易日；
- `max_drawdown_20`：信号后 20 个交易日内，按收盘价计算的相对信号价的最大不利偏移；
- `forward_return_5/10/20`：信号后第 5/10/20 个交易日收盘相对信号价的收益。

#### Scenario: 底部信号回撤取最低价
- **WHEN** 某 `bottom` 信号价格为 3000，信号后 20 个交易日内最低价为 2850
- **THEN** `max_adverse_excursion_20` MUST 为 `-5.0%`（以小数表示即 -0.05）

#### Scenario: 顶部信号回撤取最高价
- **WHEN** 某 `top` 信号价格为 3000，信号后 20 个交易日内最高价为 3120
- **THEN** `max_adverse_excursion_20` MUST 为 `-4.0%`（顶部的不利方向为上涨）

### Requirement: 回撤聚合

每个准确率汇总 MUST 同时给出：已判定样本的平均最大不利偏移、最差最大不利偏移、平均 20 日前瞻收益、收益回撤比（平均 20 日前瞻收益 / 平均最大不利偏移的绝对值）。无已判定样本时上述字段 MUST 为 `null`，MUST NOT 用 0 代替。

#### Scenario: 汇总带平均与最差回撤
- **WHEN** 某指数底部信号有 5 个已判定样本
- **THEN** 汇总 MUST 包含 `avg_max_adverse_excursion`、`worst_max_adverse_excursion`、`avg_forward_return_20`、`return_drawdown_ratio` 四个字段

#### Scenario: 无样本时不填零
- **WHEN** 某方向没有任何已判定样本
- **THEN** 回撤聚合字段 MUST 为 `null`

### Requirement: 回撤不参与胜率计算

回撤指标 MUST NOT 进入胜率分子或分母，MUST NOT 改变信号的 `success` / `failure` / `pending` 判定。修改回撤窗口或计算方式时，主胜率数值 MUST 保持不变。

#### Scenario: 改回撤窗口不影响胜率
- **WHEN** 将回撤窗口从 20 日改为 10 日
- **THEN** 所有信号的胜率判定与汇总胜率 MUST 完全不变

### Requirement: 信号生成不使用事后回撤

信号生成 MUST 只使用截至信号日的历史回撤（例如距 250 日高点回撤的分位），MUST NOT 使用信号日之后出现的回撤。事后回撤指标 MUST 只出现在评价结果中。

#### Scenario: 截断末尾数据不改变历史信号
- **WHEN** 将行情截断到某历史日期后重新计算信号
- **THEN** 截断日之前的全部信号 MUST 与未截断时完全一致

### Requirement: 页面绩效指标必须与回撤并列

所有展示绩效指标的页面（仪表盘汇总、宽基指数卡片、指标详情、信号明细表）MUST 在展示胜率或收益的同时展示对应的回撤指标。缺少回撤的绩效展示 MUST 视为未完成，不得上线。

#### Scenario: 胜率卡片并列回撤
- **WHEN** 仪表盘展示"已判定胜率"
- **THEN** 同一卡片 MUST 同时展示平均最大回撤，或在紧邻位置提供可展开的回撤明细

#### Scenario: 指数卡片并列回撤
- **WHEN** 宽基指数卡片展示胜率
- **THEN** 该卡片 MUST 同时展示该指数的平均最大回撤

#### Scenario: 回撤缺失时显式标注
- **WHEN** 某指数尚无已判定样本，无法计算回撤
- **THEN** 页面 MUST 显示 `--` 或"暂无样本"，MUST NOT 省略该字段或显示 0
