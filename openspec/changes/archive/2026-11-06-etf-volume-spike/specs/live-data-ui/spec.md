# Spec Delta

## ADDED Requirements

### Requirement: 指数图表 ETF 放量标记

指数信号卡图表 MUST 在 ETF 放量事件日期上叠加标记，并与现有信号标记共存。标记 MUST 按事件级别区分视觉强度（`L3` 最重、`L2` 次之、`L1` 最轻），样式 MUST 沿用现有色板中的品牌绿与薄荷绿系，MUST NOT 新增主色或改动现有信号标记配色（底部薄荷绿 `#14b8a6`、顶部珊瑚橙 `#f26522`）。

#### Scenario: 事件日显示标记

- **WHEN** 当前图表时间范围内存在 ETF 放量事件日
- **THEN** 该日 MUST 显示放量标记，且位置不得与同日信号标记完全重叠

#### Scenario: 级别可区分

- **WHEN** 同一时间范围内同时存在 `L1`、`L2`、`L3` 事件
- **THEN** 三者的标记 MUST 可通过尺寸或描边强度区分，且不依赖颜色区分

#### Scenario: 沿用既有色板

- **WHEN** 渲染放量标记
- **THEN** 所用颜色 MUST 取自现有色板变量，MUST NOT 引入新的主色调

### Requirement: 放量标记图例与 tooltip

图表区域 MUST 提供图例说明放量标记含义与阈值口径（核心宽基 ETF 单日成交额 ≥ 100 亿元人民币）。tooltip MUST 显示事件日期、级别、命中 ETF 数量与合计成交额，金额 MUST 以亿元为单位并保留一位小数。当同一天既有信号又有放量事件时，tooltip MUST 同时展示两者内容，MUST NOT 互相覆盖。

#### Scenario: 同日信号与事件同时提示

- **WHEN** 某日既有已确认信号又有放量事件
- **THEN** tooltip MUST 同时包含信号信息与放量事件信息

#### Scenario: 阈值口径可读

- **WHEN** 用户查看图表图例
- **THEN** 图例 MUST 说明"单日成交额 ≥ 100 亿元"这一阈值口径

#### Scenario: 金额单位统一

- **WHEN** tooltip 展示合计成交额
- **THEN** 数值 MUST 以亿元为单位并保留一位小数

### Requirement: 事件数据缺失时静默降级

当接口未返回 `etf_volume_events` 或返回为空时，图表 MUST 只渲染现有信号标记，MUST NOT 报错、MUST NOT 显示空标记占位、MUST NOT 影响图例中的信号说明。

#### Scenario: 无事件数据不影响信号

- **WHEN** `etf_volume_events` 为空数组
- **THEN** 图表 MUST 正常渲染，且已确认/候选信号标记 MUST 与变更前完全一致
