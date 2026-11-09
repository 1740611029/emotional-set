# Spec Delta

## ADDED Requirements

### Requirement: 板块评估四态统计产物契约

板块趋势产物的 `evaluation` MUST 按研究段（`research`）/ 验证段（`exploratory_validation`）× 前瞻窗口（10 日 / 20 日）× 四态（`bull` / `warn` / `bear` / `rise`）输出方向胜率统计，每个「段 × 窗口」项 MUST 以 `states` 结构给出四态，MUST NOT 再输出该段全部板块的聚合 `horizons` 结构或以聚合单一胜率作为唯一口径。

四态 MUST 由同一批状态切换事件按状态键分组得到：MUST NOT 为拆分而重算判据、增删事件或改变方向判定。方向口径 MUST 固定为 `bull` / `rise` 对应上涨、`warn` / `bear` 对应下跌。每个格子 MUST 给出触发条目数、已判定样本数、成功数、未决数、胜率与 Wilson 95% 区间，未决样本 MUST NOT 进入胜率分母；某状态在该段该窗口无可判定样本时 MUST 输出空值，MUST NOT 补造数值。

评估 MUST 只读取已落盘的日线与状态序列，MUST NOT 反向影响四态判据、有向强度、持续天数、历史序列或任何指数/板块信号计算；产物中的算法版本号与 `research_status` MUST 保持表明「判定规则未变、结论为事后探索性观察」的取值。

#### Scenario: 四态键齐备

- **WHEN** 构建板块趋势产物
- **THEN** 每个「段 × 窗口」项 MUST 含 `bull`、`warn`、`bear`、`rise` 四个状态键，各键下 MUST 含 10 日与 20 日两组统计

#### Scenario: 四态样本之和等于全部已判定样本

- **WHEN** 对某一「段 × 窗口」把四态的已判定样本数相加
- **THEN** 其和 MUST 等于该段该窗口全部板块事件的已判定样本数，且四态触发条目数之和 MUST 等于全部触发条目数（拆分只改分组，不改事件集合）

#### Scenario: 无聚合结构残留

- **WHEN** 读取任一「段 × 窗口」项
- **THEN** 该项 MUST NOT 再含聚合 `horizons` 结构或聚合单一胜率字段，产物 MUST NOT 同时提供两套口径

#### Scenario: 未决样本不计入分母

- **WHEN** 某状态在某窗口存在尚未到期的事件
- **THEN** 该事件 MUST 计入触发条目数与未决数，MUST NOT 进入胜率分母

#### Scenario: 空值不补造

- **WHEN** 某状态在某段某窗口没有任何可判定样本
- **THEN** 该状态对应统计 MUST 为空值，MUST NOT 以相邻窗口、其他状态或示例数值填充

#### Scenario: 评估不进入信号计算

- **WHEN** 构建板块趋势产物与指数信号产物
- **THEN** 板块的 `state`、`strength`、`days` 与历史序列 MUST 与拆分前逐条相等，MUST NOT 因评估口径变化而改动任何信号数值

#### Scenario: 结论口径仍为事后观察

- **WHEN** 产物输出板块评估
- **THEN** 其 `research_status` MUST 保持标注为事后探索性分析、样本区间 MUST 可追溯，MUST NOT 表述为独立验证或可交易优势
