# live-data-api Specification

## Purpose
定义常驻数据服务的刷新调度与只读接口契约，使前端能在 30 秒粒度上获取接近实时的指数、板块与信号数据，并在数据源异常时保持可解释的降级行为。

## Requirements

### Requirement: 统一快照端点

服务 MUST 提供单一快照端点，一次性返回指数列表、板块分组、最近信号、候选信号、数据时间戳与运行模式。前端 MUST 能仅凭该端点完成所有页面的首屏渲染。

#### Scenario: 快照包含完整字段
- **WHEN** 客户端请求快照端点
- **THEN** 响应 MUST 包含 `as_of`（数据所属交易日）、`mode`（`live` 或 `close`）、`generated_at`（生成时刻）、`indices`、`sectors`、`recent_signals`、`candidates` 与 `algorithm_version`

#### Scenario: 指数条目字段完整
- **WHEN** 客户端读取快照中的任一指数
- **THEN** 该条目 MUST 包含代码、名称、最新价、涨跌幅、当前信号、历史收盘序列、已确认信号列表、准确率与候选信号

### Requirement: 启动时优先补齐落后数据

服务启动时 MUST 先判定本地原始日线是否已覆盖最近已完成交易日：落后则先做增量补齐，补齐完成之后才对外提供首屏快照；数据已覆盖时 MUST NOT 发起任何远端请求。

补齐失败（网络异常、接口不可用或数据源尚未发布）MUST NOT 阻断启动：服务 MUST 用现有落盘数据继续启动，并保留失败原因供健康检查读取，MUST NOT 因为补齐失败而返回空数据或伪造数值。

#### Scenario: 启动时数据落后
- **WHEN** 服务启动且本地板块日线尚未覆盖最近已完成交易日
- **THEN** 服务 MUST 先完成增量补齐与信号重算，再提供首屏快照；期间快照端点 MUST 返回明确的“数据准备中”状态

#### Scenario: 补齐失败也继续启动
- **WHEN** 启动补齐因网络异常或源未发布而失败
- **THEN** 服务 MUST 用现有数据完成首屏构建并正常提供服务，且健康检查 MUST 给出失败原因

#### Scenario: 数据已是最新
- **WHEN** 服务启动时本地日线已覆盖最近已完成交易日
- **THEN** 服务 MUST NOT 发起日线增量请求，直接基于落盘数据出首屏

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

由于板块等数据源常在收盘数小时后才发布，服务 MUST 把「当天已完成」判定为原始数据确实覆盖到最近已完成交易日：仍未覆盖时 MUST NOT 把当天标记为完成，并 MUST 在收盘后的固定补齐时刻（17:30、20:00、22:00）再试。固定补齐时刻 MUST 只在数据仍落后时才发起远端请求，且同一批积压时刻 MUST 只执行最靠后的一轮，不形成重试风暴。

#### Scenario: 收盘后自动重算
- **WHEN** 服务在交易日 15:00 之后启动或运行
- **THEN** 服务 MUST 完成一次日线数据刷新与信号重算，并更新落盘文件

#### Scenario: 幂等重算
- **WHEN** 同一交易日已重算过且原始数据未变化
- **THEN** 再次重算 MUST 产出完全相同的结果，且不重复拉取已缓存原始数据

#### Scenario: 源尚未发布时不标记当天完成
- **WHEN** 收盘重算完成后板块日线仍未覆盖最近已完成交易日
- **THEN** 服务 MUST NOT 把当天记为已完成，并 MUST 在下一个固定补齐时刻再次尝试

#### Scenario: 晚间补齐成功
- **WHEN** 到达固定补齐时刻且数据源此时已发布最新日线
- **THEN** 服务 MUST 补齐原始数据、重算并原子落盘两份信号产物，同时刷新内存快照

#### Scenario: 数据未落后时补齐时刻不发请求
- **WHEN** 到达固定补齐时刻而本地数据已覆盖最近已完成交易日
- **THEN** 服务 MUST NOT 发起远端请求，并把当天记为已完成

### Requirement: 实时源异常降级

当实时行情源不可用时，服务 MUST 继续提供最近一次成功的数据，MUST 在响应中标记数据陈旧（`stale` 为真并给出最近成功时间），MUST NOT 返回空数据或伪造数值。

#### Scenario: 实时源超时
- **WHEN** 实时行情请求超时或返回结构异常
- **THEN** 快照 MUST 返回上一次成功结果，`stale` 为真，`generated_at` 保持为上次成功时间

#### Scenario: 冷启动无任何数据
- **WHEN** 服务首次启动且落盘数据尚不存在
- **THEN** 服务 MUST 先完成一次日线数据补齐再对外提供快照，期间端点 MUST 返回明确的"数据准备中"状态而非空结构

### Requirement: 健康检查

服务 MUST 提供健康检查端点，返回服务存活状态、最近一次刷新时间、数据模式与所用数据源，并 MUST 暴露数据补齐的可观测信息：当前阶段与说明、最近一次错误，以及收盘重算尝试次数、当天完成日期与已执行的补齐时刻。

#### Scenario: 健康检查可观测
- **WHEN** 客户端请求健康检查端点
- **THEN** 响应 MUST 包含存活标记、`last_refresh_at`、`mode`、`data_source` 与当前算法版本

#### Scenario: 补齐进度可追溯
- **WHEN** 数据正在补齐或补齐失败
- **THEN** 响应 MUST 给出当前阶段与说明，并包含补齐尝试次数、当天是否已完成与已试过的补齐时刻

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

### Requirement: 快照板块条目字段

快照中的板块条目 MUST 提供展示板块趋势所需的字段：代码、名称、数据日期、四态状态、方向强度、强度构成、强度口径、持续天数、数据异常标记、数据滞后标记与最近 30 个交易日的收盘缩略序列。每个板块的状态与持续天数 MUST 为其自身日线的独立判定结果。条目 MUST NOT 包含层级、父级名称或二级板块列表。

#### Scenario: 板块条目完整
- **WHEN** 客户端读取快照的 `sectors`
- **THEN** 每个板块 MUST 含 `code`、`name`、`state`、`strength`、`strength_parts`、`strength_basis`、`days`、`date`、`data_anomaly`、`data_lag` 与 `spark`，且 MUST NOT 含 `level`、`parent` 或 `subsectors`

#### Scenario: 板块独立状态
- **WHEN** 客户端读取任一板块
- **THEN** 其 `state` 与 `days` MUST 为该板块自身日线的独立判定结果，MUST 携带 `strength_parts`、`strength_basis` 与 `date`，且 MUST 能凭 `code` 单独查询历史序列

#### Scenario: 快照不携带长序列
- **WHEN** 客户端读取快照的 `sectors`
- **THEN** 响应 MUST NOT 包含各板块的完整日期与收盘序列，只 MUST 包含最近 30 个交易日的缩略序列与摘要字段

#### Scenario: 未分类板块不被丢弃
- **WHEN** 某板块历史不足导致状态不可用
- **THEN** 该板块 MUST 仍出现在 `sectors` 中，`state` 为空，MUST NOT 被静默移除

### Requirement: 快照携带板块评估

快照的板块评估 MUST 提供行业板块级的四态方向评估结果，MUST NOT 包含二级板块评估部分。评估 MUST 按研究段 / 验证段与 10 日 / 20 日前瞻窗口**分别按四态（`bull` / `warn` / `bear` / `rise`）**给出触发条目数、已判定样本数、成功数、未决数、胜率与 Wilson 95% 区间，并 MUST 携带说明文字；胜率 MUST NOT 以不带样本数的单一百分比呈现。口径为**按四态分组的板块级方向胜率**，MUST NOT 再以该段全部板块的聚合单一胜率作为唯一口径（旧产物中按四态分别给出的是二级板块评估，已随单层结构移除；本次把该口径补回到板块级评估）。

#### Scenario: 板块评估随快照返回
- **WHEN** 客户端读取快照的 `sectors.evaluation`
- **THEN** 响应 MUST 含板块级评估结构且每个「段 × 前瞻窗口」项同时给出胜率与已判定样本数，MUST NOT 再含二级板块评估部分

#### Scenario: 四态齐备
- **WHEN** 客户端读取任一「研究段 / 验证段 × 10 / 20 日」的板块评估
- **THEN** 该项 MUST 同时包含 `bull`、`warn`、`bear`、`rise` 四个状态键，MUST NOT 只给出其中一部分或只给出聚合值

#### Scenario: 每格带样本量与区间
- **WHEN** 客户端读取任一「状态 × 前瞻窗口」的统计
- **THEN** 该项 MUST 同时给出胜率、已判定样本数、成功数、未决数与 Wilson 95% 区间，样本量字段 MUST 如实反映实际样本数

#### Scenario: 评估缺失时不伪造
- **WHEN** 评估数据尚未生成
- **THEN** 快照 MUST 返回空评估结构，MUST NOT 返回示例数值

#### Scenario: 说明文字随评估返回
- **WHEN** 客户端读取 `sectors.evaluation`
- **THEN** 响应 MUST 携带「板块趋势为描述性分类、方向胜率不代表交易收益」的说明，且 MUST NOT 携带任何买卖建议或预期收益表述

### Requirement: 板块历史查询

服务 MUST 提供单板块历史查询，返回指定板块最近 N 个交易日的日期、收盘价与当日状态序列，并 MUST 同时返回该板块的名称、数据日期与强度摘要。N MUST 默认为 120，MUST 支持 60 / 120 / 250 三档，且 MUST 拒绝超出上限或非法的取值。历史 MUST 按板块代码唯一查询，MUST NOT 依赖层级或父级名称定位。

#### Scenario: 查询成功
- **WHEN** 客户端请求某已知板块最近 120 个交易日
- **THEN** 响应 MUST 含按日期升序的 `series`，每项包含日期、收盘价与当日状态（可为空），且最后一项日期 MUST 等于该板块的数据日期

#### Scenario: 凭代码查询任一板块
- **WHEN** 客户端以任一板块代码请求历史
- **THEN** 服务 MUST 返回该板块自身的历史序列，定位 MUST 只依 `code`，MUST NOT 依赖层级或父级名称

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

### Requirement: ETF 成交额序列端点

服务 MUST 提供一个只读端点，返回核心 6 只 ETF 的日频成交额序列、与之按日期对齐的上证指数（`sh000001`）收盘价，以及取自生产放量事件产物的 `L3` 全池爆量日列表。序列 MUST 只覆盖 6 只 ETF 全部已有日线的交易日区间（"齐备区间"），终点 MUST 为 ETF 日线已落盘的最新交易日。响应 MUST 采用日期数组加等长数值数组的列式结构，成交额 MUST 以元为单位，缺失值 MUST 为 `null` 而非 0 或插值。该端点 MUST NOT 写入任何产物文件，MUST NOT 参与或改变任何信号与放量事件的计算。

#### Scenario: 字段与对齐

- **WHEN** 请求 ETF 成交额序列端点且数据齐备
- **THEN** 响应 MUST 含日期数组、每只 ETF 的名称/代码/跟踪指数与等长成交额数组、合计成交额数组、对齐后的上证指数收盘数组与 `L3` 日期列表，且各数组长度 MUST 与日期数组一致

#### Scenario: 齐备区间界定

- **WHEN** 核心池中上市最晚的标的自 2020-11 起才有日线
- **THEN** 返回序列的首个日期 MUST 为 6 只标的全部存在日线的首个交易日，MUST NOT 返回只有部分标的的更早日期

#### Scenario: 合计只由齐备日构成

- **WHEN** 齐备区间内某交易日有任一只 ETF 缺少当日成交额
- **THEN** 该日的合计成交额 MUST 为 `null` 且该日 MUST 从齐备区间剔除，MUST NOT 用部分标的之和替代

#### Scenario: 指数缺失保持断点

- **WHEN** 某齐备交易日在指数日线中查不到对应收盘
- **THEN** 该日的上证指数收盘 MUST 为 `null`，ETF 成交额数值 MUST 照常返回

#### Scenario: 长序列不进快照

- **WHEN** ETF 齐备区间随时间增长
- **THEN** `/api/snapshot` 的响应体积 MUST NOT 随之增长，ETF 长序列 MUST 只由该专用端点提供

#### Scenario: 数据缺失与损坏降级

- **WHEN** ETF 日线文件缺失、读取失败或尚未准备
- **THEN** 端点 MUST 返回可识别的准备中或不可用状态（而非 5xx），并给出原因，MUST NOT 伪造数值或以其他标的顶替

#### Scenario: 落后于最近交易日

- **WHEN** ETF 日线的最新日期落后于最近已完成交易日
- **THEN** 响应 MUST 标记陈旧并给出落后信息，已有序列 MUST 原样返回

#### Scenario: 只读不改盘

- **WHEN** 反复请求该端点
- **THEN** `data/` 下所有产物的内容与修改时间 MUST 保持不变

### Requirement: 快照准确率矩阵字段

`/api/snapshot` 与 `/api/indices/{code}/history` 的 `accuracy` / `evaluation` 结构 MUST 提供多持有期矩阵：按研究段 / 独立验证段 × 5 / 10 / 20 / 30 交易日 × `bottom` / `top` 分格，每格 MUST 同时给出触发次数、已判定样本数、成功数、失败数、未决数、方向正确率、有效幅度率、Wilson 95% 区间、平均最大不利偏移、最差最大不利偏移、浮亏超限失败数与统计起止日期。响应 MUST NOT 再以不带样本量的单一 `rate` 百分比作为唯一准确率字段；为兼容旧客户端保留的聚合 `rate` 字段 MUST 明确标注为旧口径历史基线，MUST NOT 被当作新口径主胜率。信号记录 MUST 携带拐点基准日、拐点自然确认日与相对拐点的偏移交易日数，以及完整的三层确认逐层日志。

#### Scenario: 矩阵逐格返回
- **WHEN** 客户端请求 `/api/snapshot`
- **THEN** 响应 MUST 包含 2 段 × 4 持有期 × 2 方向的全部格，每格 MUST 含触发次数、已判定样本数、方向正确率、有效幅度率、未决数与 Wilson 区间

#### Scenario: 旧字段不被误用
- **WHEN** 响应中仍保留旧聚合 `rate` 字段
- **THEN** 该字段 MUST 带有口径标识与「历史基线」说明，MUST NOT 与新矩阵主口径混用同一展示位

#### Scenario: 信号携带拐点信息
- **WHEN** 客户端请求某指数的历史信号
- **THEN** 每条信号 MUST 含拐点基准日、拐点自然确认日、偏移交易日数与三层确认逐层日志

### Requirement: 统计覆盖范围与口径标注

响应 MUST 在顶层给出本次统计所覆盖的标的清单、标的数、各标的历史起止日期，以及训练段 / 隔离期 / 独立验证段的起止日期。响应 MUST 携带口径说明：准确率方向判定与浮亏约束的定义、未决样本处理方式、验证段是否独立、以及「方向准确率不代表交易收益率」的提示。MUST NOT 出现买卖建议、预期收益表述或「胜率极高」等暗示性措辞。当某格已判定样本少于 30 时，`small_sample` MUST 为 `true`；样本量偏少 MUST NOT 阻止该格给出结论，但 MUST 如实返回样本量、Wilson 95% 区间与统计区间。

#### Scenario: 覆盖范围明示
- **WHEN** 统计合并了扩展标的池
- **THEN** 响应 MUST 给出覆盖标的清单与数量，且 MUST 与界面展示的 7 个指数范围可区分

#### Scenario: 小样本如实标注
- **WHEN** 某格已判定样本为 6 个
- **THEN** 该格 `small_sample` MUST 为 `true` 且 MUST 返回 `n=6`、Wilson 95% 区间与统计区间，MUST NOT 仅返回一个百分比

#### Scenario: 无收益暗示
- **WHEN** 校验响应文案
- **THEN** 响应 MUST NOT 包含「买入」「卖出」「可交易」「胜率极高」等指令性或收益暗示措辞
