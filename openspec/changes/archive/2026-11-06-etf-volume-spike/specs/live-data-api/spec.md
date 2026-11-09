# Spec Delta

## ADDED Requirements

### Requirement: 快照携带 ETF 放量事件

`/api/snapshot` MUST 在响应顶层返回 `etf_volume_events` 数组，每项 MUST 含 `date`、`level`、`etf_codes`、`total_amount` 四个字段，其中 `level` 取值 MUST 为 `L1`/`L2`/`L3`，`etf_codes` MUST 为当日命中阈值的核心池代码列表，`total_amount` MUST 为当日命中标的的成交额合计（元）。数组 MUST 按日期升序排列。

#### Scenario: 字段完整

- **WHEN** 请求 `/api/snapshot` 且事件产物存在
- **THEN** 响应顶层 MUST 含 `etf_volume_events`，且每项含上述四个字段

#### Scenario: 排序稳定

- **WHEN** 同一份事件产物被多次请求
- **THEN** 返回数组的日期顺序 MUST 一致且为升序

### Requirement: 事件产物缺失时降级

当事件产物不存在、解析失败或数据尚未准备时，接口 MUST 返回空的 `etf_volume_events` 数组，MUST NOT 返回 5xx，也 MUST NOT 阻塞快照其余字段返回。

#### Scenario: 产物缺失返回空数组

- **WHEN** `data/etf-volume-events.json` 不存在
- **THEN** `/api/snapshot` MUST 正常返回 200 且 `etf_volume_events` 为空数组

#### Scenario: 产物损坏不影响其余字段

- **WHEN** 事件产物内容无法解析
- **THEN** 响应其余字段 MUST 与产物存在时一致，`etf_volume_events` MUST 为空数组

### Requirement: 指数历史端点返回同一份事件

`/api/indices/{code}/history` MUST 返回与该快照完全相同的 `etf_volume_events` 数组，使单独查询指数历史时也能复现同样的标记。该字段 MUST NOT 改变既有字段的语义与取值范围。

#### Scenario: 与快照一致

- **WHEN** 分别请求 `/api/snapshot` 与 `/api/indices/sh000300/history`
- **THEN** 两处的 `etf_volume_events` MUST 逐项相同

#### Scenario: 既有字段不受影响

- **WHEN** 对比本变更前后的同一指数历史响应
- **THEN** 除新增的 `etf_volume_events` 外，其余字段 MUST 逐值一致
