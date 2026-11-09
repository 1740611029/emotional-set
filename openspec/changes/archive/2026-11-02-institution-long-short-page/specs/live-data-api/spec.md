# Spec Delta

## ADDED Requirements

### Requirement: 机构持仓查询端点

服务 MUST 提供只读端点 `GET /api/institutions/positions`，返回机构持仓摘要、按机构与品种组织的序列、同向率统计与口径说明。响应 MUST 包含 `as_of`、`generated_at`、`stale`、`institutions`（分组定义）、`varieties`、`summary`、`series` 与 `disclosure`。该端点 MUST 只读取已落盘产物，MUST NOT 在请求路径内触发远端抓取。

#### Scenario: 默认响应字段完整
- **WHEN** 客户端请求该端点且未传任何参数
- **THEN** 响应 MUST 包含 `as_of`、`generated_at`、`stale`、`institutions`、`varieties`、`summary`、`series`、`disclosure`，且 `series` MUST 覆盖默认时间范围（最近 60 个交易日）

#### Scenario: 请求不触发抓取
- **WHEN** 客户端连续多次请求该端点
- **THEN** 服务 MUST 只读缓存产物，MUST NOT 发起任何外部数据请求，且响应时间不随历史长度显著增长

### Requirement: 查询参数与校验

端点 MUST 支持 `institution`、`variety`、`from`、`to`、`limit` 参数。`institution` MUST 限定为 `guotai` / `citic` / `others` / `all`，`variety` MUST 限定为 `all` / `IF` / `IC` / `IH` / `IM`；非法取值 MUST 返回 400 并说明允许值；日期格式非法或区间倒置 MUST 返回 400。合法但无匹配数据的区间 MUST 返回空序列而非错误。

#### Scenario: 非法机构参数
- **WHEN** 客户端传入不在枚举内的 `institution`
- **THEN** 服务 MUST 返回 400，且响应 MUST 列出允许取值

#### Scenario: 区间倒置
- **WHEN** `from` 晚于 `to`
- **THEN** 服务 MUST 返回 400 并说明区间非法

#### Scenario: 空区间
- **WHEN** 合法区间内没有任何持仓记录（如全部为节假日）
- **THEN** 服务 MUST 返回 200 且序列为空数组，MUST NOT 返回 404 或 500

### Requirement: 快照端点不承载机构长序列

`/api/snapshot` MUST NOT 内联机构持仓的历史序列或明细列表；机构数据 MUST 仅通过独立端点提供，以避免 30 秒轮询响应体积随历史增长。若需在快照中提供入口摘要，MUST 只包含最新交易日与三家机构的净持仓摘要字段。

#### Scenario: 快照体积不受历史影响
- **WHEN** 机构持仓历史从 60 个交易日增长到 750 个交易日
- **THEN** `/api/snapshot` 的响应体积 MUST 不随该历史增长而显著变化

### Requirement: 机构数据缺失与陈旧降级

当机构持仓产物不存在时，端点 MUST 返回明确的"数据准备中"状态（`data_state` 为 `preparing`）而非空结构；当产物所属交易日落后于最近交易日时，响应 MUST 标记 `stale` 为真并给出 `stale_since` 与 `stale_reason`。MUST NOT 返回伪造数值或以其他品种、其他日期数据顶替。

#### Scenario: 产物尚未生成
- **WHEN** 服务启动后首次请求且机构持仓产物不存在
- **THEN** 端点 MUST 返回 `data_state=preparing` 与可读说明，前端可据此展示加载态

#### Scenario: 产物落后于最新交易日
- **WHEN** 产物 `as_of` 早于最近交易日
- **THEN** 响应 MUST 标记 `stale=true`，并给出 `stale_since` 与降级原因，数据本身 MUST 原样返回
