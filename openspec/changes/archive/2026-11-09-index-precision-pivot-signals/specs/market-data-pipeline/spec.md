# Spec Delta

## ADDED Requirements

### Requirement: 生产标的清单固化与 manifest

本变更不扩展标的池：调参、统计验证与生产展示 MUST 全部使用固化的 7 大宽基指数（`sh000001`、`sz399001`、`sz399006`、`sh000688`、`sh000300`、`sh000905`、`sh000852`）。该清单 MUST 落盘为独立 manifest（置于 `data/research/`），逐项登记代码、名称、数据源、复权方式、行数、起止日期与相对路径，且 MUST 与实际 CSV 逐项一致。所有标的 MUST 使用**同一价格口径**——同花顺公开日线接口、前复权、相同字段顺序与价格单位，MUST NOT 引入第二套价格口径或混算补值。清单 MUST NOT 自动新增展示卡片或改变 `as_of` 的取值规则。历史不足 1250 个交易日的标的 MUST 显式标记而 MUST NOT 静默丢弃。研究脚本运行前后 `data/index-signals.json` 与 `data/sector-trends.json` MUST 逐字节一致。

#### Scenario: 口径一致
- **WHEN** 读取 manifest 中任一标的日线
- **THEN** 该文件 MUST 来自同花顺公开日线接口且为前复权，字段与单位 MUST 与其余 6 个指数完全一致

#### Scenario: 生产清单不受影响
- **WHEN** 扩展标的池落盘后重建生产信号产物
- **THEN** `index-signals.json` 的展示指数集合 MUST 仍为原有 7 个，MUST NOT 因扩展池而新增展示卡片

#### Scenario: 标的清单固化校验
- **WHEN** 研究脚本启动前校验固化清单
- **THEN** 系统 MUST 逐个校验冻结清单中的代码、名称、行数与起止日期；校验不通过 MUST 报错并保留上一版产物，MUST NOT 自动改用其他标的

### Requirement: 指数信号准确率摘要结构

指数信号输出中的 `accuracy` 与 `evaluation` MUST 适配多持有期矩阵：按研究段 / 独立验证段 × 5 / 10 / 20 / 30 交易日 × `bottom` / `top` 分格，每格 MUST 给出触发次数、已判定样本数、成功数、失败数、未决数、方向正确率、有效幅度率、Wilson 95% 区间、平均与最差最大不利偏移、浮亏超限失败数与统计起止日期。输出 MUST 同时保留旧口径结果作为可复算的历史基线字段，MUST NOT 用新字段覆盖旧口径。产物 MUST 标注统计所覆盖的标的清单、标的数与 `research_status`；未独立验证的结果 MUST 标注为探索性，MUST NOT 表述为独立验证结论。每次落盘前 MUST 执行结构校验，校验失败 MUST 保留上一版文件且 MUST NOT 产生半写文件。

#### Scenario: 摘要逐格完整
- **WHEN** 构建某指数的 `evaluation`
- **THEN** 每格 MUST 含触发次数、已判定样本数、方向正确率、有效幅度率、未决数与 Wilson 区间，缺任一字段 MUST 视为构建失败

#### Scenario: 统计覆盖范围可见
- **WHEN** 产物合并了 7 个标的的统计结果
- **THEN** 产物 MUST 给出覆盖标的清单与标的数，且 MUST 与页面 7 张指数图卡范围一致

#### Scenario: 旧口径保留
- **WHEN** 新矩阵结构上线
- **THEN** 旧「均线反转」口径的结果 MUST 仍存在于独立字段中并可复算，MUST NOT 被删除
