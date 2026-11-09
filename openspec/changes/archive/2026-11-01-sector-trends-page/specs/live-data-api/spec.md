# Spec Delta

## ADDED Requirements

### Requirement: 快照板块条目字段

快照中的板块条目 MUST 提供展示板块趋势所需的字段：层级、代码、名称、父级名称、数据日期、四态状态、方向强度、强度构成、强度口径、持续天数、数据异常标记、数据滞后标记、二级板块列表与最近 30 个交易日的收盘缩略序列。一级与二级板块 MUST 各自携带独立判定得到的状态与持续天数。

#### Scenario: 一级板块条目完整
- **WHEN** 客户端读取快照的 `sectors`
- **THEN** 每个一级板块 MUST 含 `code`、`name`、`state`、`strength`、`strength_parts`、`strength_basis`、`days`、`date`、`data_anomaly`、`data_lag`、`spark` 与 `subsectors`

#### Scenario: 二级板块独立状态
- **WHEN** 客户端读取某二级板块
- **THEN** 其 `state` 与 `days` MUST 为独立判定结果（MAY 与父级不同），MUST 携带 `code`、`strength_parts`、`strength_basis` 与 `date`，且 MUST 能凭 `code` 单独查询历史序列

#### Scenario: 快照不携带长序列
- **WHEN** 客户端读取快照的 `sectors`
- **THEN** 响应 MUST NOT 包含各板块的完整日期与收盘序列，只 MUST 包含最近 30 个交易日的缩略序列与摘要字段

#### Scenario: 未分类板块不被丢弃
- **WHEN** 某板块历史不足导致状态不可用
- **THEN** 该板块 MUST 仍出现在 `sectors` 中，`state` 为空，MUST NOT 被静默移除

### Requirement: 快照携带板块评估

快照的板块评估 MUST 同时包含一级与二级板块的评估结果；二级部分 MUST 按四态分别给出 10 日与 20 日胜率、已判定样本数、Wilson 区间与分段，并 MUST 携带说明文字。

#### Scenario: 二级评估随快照返回
- **WHEN** 客户端读取快照的 `sectors.evaluation`
- **THEN** 响应 MUST 含二级板块分状态评估，且每项 MUST 同时给出胜率与已判定样本数

#### Scenario: 评估缺失时不伪造
- **WHEN** 评估数据尚未生成
- **THEN** 快照 MUST 返回空评估结构，MUST NOT 返回示例数值

### Requirement: 板块历史查询

服务 MUST 提供单板块历史查询，返回指定板块最近 N 个交易日的日期、收盘价与当日状态序列，并 MUST 同时返回该板块的层级、名称、数据日期与强度摘要。N MUST 默认为 120，MUST 支持 60 / 120 / 250 三档，且 MUST 拒绝超出上限或非法的取值。

#### Scenario: 查询成功
- **WHEN** 客户端请求某已知板块最近 120 个交易日
- **THEN** 响应 MUST 含按日期升序的 `series`，每项包含日期、收盘价与当日状态（可为空），且最后一项日期 MUST 等于该板块的数据日期

#### Scenario: 二级板块可查询
- **WHEN** 客户端以某二级板块代码请求历史
- **THEN** 服务 MUST 返回该二级板块自身的历史序列，MUST NOT 返回其父级序列

#### Scenario: 未知板块
- **WHEN** 客户端请求不存在的板块代码
- **THEN** 服务 MUST 返回 404 与明确错误信息，MUST NOT 返回空序列冒充成功

#### Scenario: 非法区间
- **WHEN** 客户端请求的交易日数量超出允许上限或不是正整数
- **THEN** 服务 MUST 返回 400 与明确错误信息，MUST NOT 静默截断后按成功返回

#### Scenario: 历史长度不足
- **WHEN** 请求的板块可用历史少于所请求长度
- **THEN** 服务 MUST 返回实际可用序列，并 MUST 标明覆盖区间与"历史不足"标记，MUST NOT 补造交易日

#### Scenario: 数据陈旧沿用既有降级
- **WHEN** 最近一次刷新失败且存在上一次成功数据
- **THEN** 该端点 MUST 返回上一次成功数据并标记陈旧，MUST NOT 返回空序列或伪造数值
