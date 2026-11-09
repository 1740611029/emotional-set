# Spec Delta

## ADDED Requirements

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
