# Spec Delta

## Purpose

定义常驻数据服务的刷新调度与只读接口契约，使前端能在 30 秒粒度上获取接近实时的指数、板块与信号数据，并在数据源异常时保持可解释的降级行为。

## ADDED Requirements

### Requirement: 统一快照端点

服务 MUST 提供单一快照端点，一次性返回指数列表、板块分组、最近信号、候选信号、数据时间戳与运行模式。前端 MUST 能仅凭该端点完成所有页面的首屏渲染。

#### Scenario: 快照包含完整字段
- **WHEN** 客户端请求快照端点
- **THEN** 响应 MUST 包含 `as_of`（数据所属交易日）、`mode`（`live` 或 `close`）、`generated_at`（生成时刻）、`indices`、`sectors`、`recent_signals`、`candidates` 与 `algorithm_version`

#### Scenario: 指数条目字段完整
- **WHEN** 客户端读取快照中的任一指数
- **THEN** 该条目 MUST 包含代码、名称、最新价、涨跌幅、当前信号、历史收盘序列、已确认信号列表、准确率与候选信号

### Requirement: 盘中 30 秒刷新

在交易日交易时段内（09:30–11:30、13:00–15:00，按市场时区判定），服务 MUST 以不高于 30 秒的间隔刷新盘中快照。非交易时段 MUST NOT 发起实时行情请求，MUST 直接返回最近一次收盘结果。

#### Scenario: 交易时段内按时刷新
- **WHEN** 当前时间处于交易时段
- **THEN** 快照的 `generated_at` 与上一次请求相比 MUST 在 30 秒内更新，且 `mode` 为 `live`

#### Scenario: 非交易时段不请求实时源
- **WHEN** 当前时间处于非交易时段（含周末与节假日）
- **THEN** 服务 MUST 返回 `mode` 为 `close` 的快照，且 MUST NOT 调用实时行情接口

### Requirement: 收盘重算

服务 MUST 在交易日收盘后（15:00 之后）首次运行时触发一次日线重算，并以原子方式更新落盘数据。同一交易日内重复触发 MUST NOT 产生不同结果。

#### Scenario: 收盘后自动重算
- **WHEN** 服务在交易日 15:00 之后启动或运行
- **THEN** 服务 MUST 完成一次日线数据刷新与信号重算，并更新落盘文件

#### Scenario: 幂等重算
- **WHEN** 同一交易日已重算过且原始数据未变化
- **THEN** 再次重算 MUST 产出完全相同的结果，且不重复拉取已缓存原始数据

### Requirement: 实时源异常降级

当实时行情源不可用时，服务 MUST 继续提供最近一次成功的数据，MUST 在响应中标记数据陈旧（`stale` 为真并给出最近成功时间），MUST NOT 返回空数据或伪造数值。

#### Scenario: 实时源超时
- **WHEN** 实时行情请求超时或返回结构异常
- **THEN** 快照 MUST 返回上一次成功结果，`stale` 为真，`generated_at` 保持为上次成功时间

#### Scenario: 冷启动无任何数据
- **WHEN** 服务首次启动且落盘数据尚不存在
- **THEN** 服务 MUST 先完成一次日线数据补齐再对外提供快照，期间端点 MUST 返回明确的"数据准备中"状态而非空结构

### Requirement: 健康检查

服务 MUST 提供健康检查端点，返回服务存活状态、最近一次刷新时间、数据模式与所用数据源。

#### Scenario: 健康检查可观测
- **WHEN** 客户端请求健康检查端点
- **THEN** 响应 MUST 包含存活标记、`last_refresh_at`、`mode`、`data_source` 与当前算法版本

### Requirement: 单实例刷新与并发安全

同一时刻 MUST 只允许一个刷新任务执行；并发请求 MUST 复用正在进行或已完成的快照，MUST NOT 触发重复计算。文件写入 MUST 为原子替换，读取 MUST 不会读到半写内容。

#### Scenario: 并发请求不重复计算
- **WHEN** 多个客户端在同一刷新周期内请求快照
- **THEN** 服务 MUST 只执行一次计算，并将同一结果返回给所有请求

### Requirement: 跨平台可运行

服务 MUST 在 Windows、macOS、Linux 上以相同命令启动，端口 MUST 可通过配置或环境变量覆盖，MUST NOT 依赖本机绝对路径或平台专属组件。

#### Scenario: 端口可配置
- **WHEN** 通过环境变量指定端口
- **THEN** 服务 MUST 监听该端口，且默认端口在未指定时保持固定

#### Scenario: 生产构建托管
- **WHEN** 前端已构建为静态产物
- **THEN** 服务 MUST 能同时托管该静态产物与 API，使单进程即可访问完整站点
