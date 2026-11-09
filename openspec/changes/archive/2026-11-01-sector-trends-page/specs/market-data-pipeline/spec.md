# Spec Delta

## ADDED Requirements

### Requirement: 板块趋势产物契约

流水线 MUST 输出板块趋势产物，覆盖全部申万一级与二级板块，包含算法版本号、数据所属交易日与分组结构。四态取值 MUST 固定为 `bull` / `warn` / `bear` / `rise`，判定口径 MUST 保持 `sector-donchian20-ma60-v1.0`：以唐奇安 20 日通道中轨与 60 日均线两条判据组合成四态，MUST NOT 因展示需要调整阈值或引入新的判据。

#### Scenario: 四态取值固定
- **WHEN** 构建板块趋势产物
- **THEN** 每个板块的 `state` MUST 为 `bull` / `warn` / `bear` / `rise` 之一，或为空表示数据不足，MUST NOT 出现其他取值

#### Scenario: 一级判定口径不变
- **WHEN** 本次改动前后对同一份原始日线各跑一次
- **THEN** 全部 31 个一级板块的 `state`、`strength`、`days` 与分组归属 MUST 逐条相等；二级板块的 `strength` MUST 逐条相等

#### Scenario: 算法版本可追溯
- **WHEN** 产物生成
- **THEN** 产物 MUST 包含算法版本号与 `as_of`，且版本号 MUST 与所用判定模块一致

### Requirement: 二级板块独立判定

二级板块 MUST 使用自身日线独立计算 `state`、`strength` 与 `days`，MUST NOT 直接赋值或继承所属一级板块的状态与持续天数。一级与二级板块 MUST 使用同一判定口径与同一算法版本。

#### Scenario: 二级状态独立计算
- **WHEN** 构建某二级板块的趋势
- **THEN** 其 `state` 与 `days` MUST 来自该板块自身的日线序列，且 MAY 与其父级板块不同

#### Scenario: 二级历史不足
- **WHEN** 某二级板块有效历史不足 60 个交易日
- **THEN** 该板块的 `state` MUST 为空并 MUST 被标记为数据不足，MUST NOT 回退到父级状态

#### Scenario: 数据滞后
- **WHEN** 某板块最新交易日早于产物 `as_of`
- **THEN** 该板块 MUST 输出自身 `date` 并标记 `data_lag`；滞后超过 30 个自然日时 MUST 将 `state` 置空

#### Scenario: 找不到父级
- **WHEN** 某二级板块的父级名称不在一级板块清单中
- **THEN** 流水线 MUST 报错终止，MUST NOT 丢弃该板块

### Requirement: 板块条目字段完整

每个板块条目 MUST 包含层级、代码、名称、父级名称、数据日期、状态、方向强度、持续天数、强度构成、数据异常标记与数据滞后标记。方向强度 MUST 为 0–100 的数值；持续天数 MUST 为该状态连续保持的交易日数。

#### Scenario: 一级板块字段完整
- **WHEN** 读取任一一级板块条目
- **THEN** 该条目 MUST 含 `level`、`code`、`name`、`date`、`state`、`strength`、`strength_parts`、`strength_basis`、`days`、`data_anomaly`、`data_lag` 与 `subsectors` 列表

#### Scenario: 二级板块条目可独立定位
- **WHEN** 读取某一级板块的 `subsectors`
- **THEN** 每个二级条目 MUST 含 `code`、`name`、`state`、`strength`、`strength_parts`、`strength_basis`、`days`、`date`、`data_anomaly` 与 `data_lag`，且 `code` MUST 能用于单独查询该板块的历史序列

#### Scenario: 强度取值范围
- **WHEN** 某板块状态有效
- **THEN** 其 `strength` MUST 落在 0–100 闭区间内

### Requirement: 强度构成导出

产物 MUST 为每个板块导出强度构成：`range_position`（收盘价在近 20 日高低区间中的位置分，0–100）与 `ma_score`（相对 60 日均线偏离的映射分，0–100），以及 `strength_basis` 标明该强度的口径：`directional` 表示未取反、`inverted` 表示按方向取反、`neutral` 表示通道宽度为零按中性处理。构成分 MUST 与 `strength` 数值自洽：`directional` 时为两项均值，`inverted` 时为 `100 - 两项均值`，误差 MUST 小于 0.01。

#### Scenario: 多头状态强度可复算
- **WHEN** 某板块 `strength_basis` 为 `directional`
- **THEN** 其 `strength` MUST 等于 `(range_position + ma_score) / 2`（误差 < 0.01）

#### Scenario: 空头与退潮状态取反
- **WHEN** 某板块 `strength_basis` 为 `inverted`
- **THEN** 其 `strength` MUST 等于 `100 - (range_position + ma_score) / 2`（误差 < 0.01）

#### Scenario: 通道宽度为零
- **WHEN** 某板块近 20 日最高价等于最低价
- **THEN** 其 `strength` MUST 固定为 50、`strength_basis` MUST 为 `neutral` 并标记 `data_anomaly`，`strength_parts` MUST 仍可解析

### Requirement: 板块历史序列导出

产物 MUST 为每个板块导出最近不少于 250 个交易日的 `date`、`close`、`state` 序列，供走势展示使用；同时 MUST 导出最近 30 个交易日的收盘价作为列表缩略序列。序列 MUST 按交易日升序、无重复日期，且 MUST 只包含已发生的交易日。交易日日期 MUST 在产物内共享一份，MUST NOT 在每个板块中重复存储。

#### Scenario: 历史序列可对照
- **WHEN** 读取某板块的导出序列
- **THEN** 其最后一个交易日 MUST 等于该板块条目的 `date`，最后一日 `state` MUST 等于条目 `state`

#### Scenario: 序列长度不足
- **WHEN** 某板块可用历史少于 250 个交易日
- **THEN** 产物 MUST 导出实际可用长度，并 MUST 标明覆盖区间，MUST NOT 用补值填充

#### Scenario: 无未来数据
- **WHEN** 构建任一板块的历史序列
- **THEN** 序列中 MUST NOT 出现晚于 `as_of` 的交易日

### Requirement: 二级板块独立状态评估

产物 MUST 输出二级板块独立四态的方向评估，按状态分别给出 10 日与 20 日两个观察期的触发数、已判定样本数、成功数、胜率与 Wilson 95% 区间，并 MUST 按研究段与验证段分别给出。样本数不足时 MUST 标记小样本。产物 MUST 附带说明：二级板块独立状态的方向胜率不稳定，仅作描述性分类。

#### Scenario: 评估带样本量与区间
- **WHEN** 读取二级板块的评估结果
- **THEN** 每个状态与观察期 MUST 含触发数、已判定样本数、成功数、胜率、Wilson 95% 区间与分段名，MUST NOT 只输出单一百分比

#### Scenario: 分段独立
- **WHEN** 评估结果生成
- **THEN** 研究段与验证段 MUST 分开输出，MUST NOT 合并成一个胜率

#### Scenario: 未决样本不计入分母
- **WHEN** 某次触发在观察期结束时尚未走满 10 日或 20 日
- **THEN** 该次触发 MUST 计入触发数但 MUST NOT 计入已判定样本数

### Requirement: 变更留痕与对照

本次改动 MUST 保留改动前的产物作为对照基线，并 MUST 输出一级板块不变、二级板块变化的对照结果。一级板块的任何字段变化 MUST 视为回归失败；二级板块的 `state`/`days` 变化 MUST 以变化清单形式记录。

#### Scenario: 一级板块零变化
- **WHEN** 对同一份原始日线分别用改动前与改动后代码生成产物
- **THEN** 31 个一级板块的 `state`、`strength`、`days` 与分组归属 MUST 完全一致

#### Scenario: 二级变化可追溯
- **WHEN** 对比改动前后的二级板块状态
- **THEN** 产物或对照报告 MUST 列出所有状态发生变化的板块（代码、名称、旧状态、新状态），MUST NOT 只给变化数量

#### Scenario: 旧产物可回溯
- **WHEN** 本次改动上线
- **THEN** 改动前的产物 MUST 被保留可查，以便对照与回滚
