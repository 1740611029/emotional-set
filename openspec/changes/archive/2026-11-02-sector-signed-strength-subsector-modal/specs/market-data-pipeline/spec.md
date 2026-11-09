# Spec Delta

## MODIFIED Requirements

### Requirement: 板块趋势产物契约

流水线 MUST 输出板块趋势产物，覆盖全部申万一级与二级板块，包含算法版本号、数据所属交易日与分组结构。四态取值 MUST 固定为 `bull` / `warn` / `bear` / `rise`，判定口径 MUST 保持 `sector-donchian20-ma60-v1.0`：以唐奇安 20 日通道中轨与 60 日均线两条判据组合成四态，MUST NOT 因展示需要调整阈值或引入新的判据。强度 MUST 使用统一的有向强度公式，MUST NOT 按四态取反。

#### Scenario: 四态取值固定

- **WHEN** 构建板块趋势产物
- **THEN** 每个板块的 `state` MUST 为 `bull` / `warn` / `bear` / `rise` 之一，或为空表示数据不足，MUST NOT 出现其他取值

#### Scenario: 一级判定口径不变

- **WHEN** 本次改动前后对同一份原始日线各跑一次
- **THEN** 全部 31 个一级板块的 `state`、`days` 与分组归属 MUST 逐条相等，`strength` MUST 按新的统一有向公式转换，MUST NOT 因强度展示改动改变四态判据

#### Scenario: 算法版本可追溯

- **WHEN** 产物生成
- **THEN** 产物 MUST 包含算法版本号与 `as_of`，且版本号 MUST 与所用判定模块一致

### Requirement: 板块条目字段完整

每个板块条目 MUST 包含层级、代码、名称、父级名称、数据日期、状态、有向强度、持续天数、强度构成、数据异常标记与数据滞后标记。有向强度 MUST 为 -50–+50 的数值；持续天数 MUST 为该状态连续保持的交易日数。

#### Scenario: 一级板块字段完整

- **WHEN** 读取任一一级板块条目
- **THEN** 该条目 MUST 含 `level`、`code`、`name`、`date`、`state`、`strength`、`strength_parts`、`strength_basis`、`days`、`data_anomaly`、`data_lag` 与 `subsectors` 列表

#### Scenario: 二级板块条目可独立定位

- **WHEN** 读取某一级板块的 `subsectors`
- **THEN** 每个二级条目 MUST 含 `code`、`name`、`state`、`strength`、`strength_parts`、`strength_basis`、`days`、`date`、`data_anomaly` 与 `data_lag`，且 `code` MUST 能用于单独查询该板块的历史序列

#### Scenario: 强度取值范围

- **WHEN** 某板块状态有效
- **THEN** 其 `strength` MUST 落在 -50–+50 闭区间内

### Requirement: 强度构成导出

产物 MUST 为每个板块导出强度构成：`range_position`（收盘价在近 20 日高低区间中的位置分，0–100）与 `ma_score`（相对 60 日均线偏离的映射分，0–100），以及 `strength_basis` 标明该强度的口径：`directional` 表示统一有向计算、`neutral` 表示通道宽度为零按中性处理。构成分 MUST 与 `strength` 数值自洽：`directional` 时 MUST 等于两项均值减 50，误差 MUST 小于 0.01；`neutral` 时 MUST 固定为 0。产物 MUST NOT 再输出按多空状态取反的 `inverted` 口径。

#### Scenario: 多头状态强度可复算

- **WHEN** 某板块 `strength_basis` 为 `directional`
- **THEN** 其 `strength` MUST 等于 `(range_position + ma_score) / 2 - 50`（误差 < 0.01），且该公式 MUST 对全部四态一致

#### Scenario: 空头与退潮状态取反

- **WHEN** 某板块状态为 `bear` 或 `warn`
- **THEN** 其 `strength` MUST 仍等于两项构成分均值减 50，MUST NOT 使用 `100 - 两项均值` 或其他取反公式

#### Scenario: 通道宽度为零

- **WHEN** 某板块近 20 日最高价等于最低价
- **THEN** 其 `strength` MUST 固定为 0、`strength_basis` MUST 为 `neutral` 并标记 `data_anomaly`，`strength_parts` MUST 仍可解析

### Requirement: 变更留痕与对照

本次改动 MUST 保留改动前的产物作为对照基线，并 MUST 输出一级与二级板块的强度和四态对照结果。除 `strength` 与 `strength_basis` 的预期口径变化外，一级和二级板块的 `state`、`days`、分组归属及历史序列 MUST 保持不变；任何非预期变化 MUST 视为回归失败。

#### Scenario: 一级板块零变化

- **WHEN** 对同一份原始日线分别用改动前与改动后代码生成产物
- **THEN** 全部一级与二级板块的 `state`、`days` 与分组归属 MUST 完全一致，历史收盘与状态序列 MUST 完全一致

#### Scenario: 二级变化可追溯

- **WHEN** 对比改动前后的板块强度
- **THEN** 对照结果 MUST 能用旧口径和新公式解释每处 `strength` / `strength_basis` 差异，MUST NOT 出现无法由口径转换解释的数值变化

#### Scenario: 旧产物可回溯

- **WHEN** 本次改动上线
- **THEN** 改动前的产物 MUST 被保留可查，以便对照与回滚
