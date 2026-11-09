# Spec Delta

## ADDED Requirements

### Requirement: 固定标的池下的训练与验证分段冻结

本变更**不扩展标的池**：调参与评价 MUST 全部在固化的 7 大宽基指数上进行（`sh000001`、`sz399001`、`sz399006`、`sh000688`、`sh000300`、`sh000905`、`sh000852`）。标的清单 MUST 在进入训练前一次性冻结并登记每个标的的代码、名称、数据源、复权方式与历史起止日期，MUST NOT 在验证段新增或替换标的。训练段 MUST 沿用固定结束日期 `2024-12-31`，MUST NOT 随行情更新而延长；独立验证段 MUST 自隔离期结束后的首个交易日起算。训练段与验证段 MUST NOT 重叠，MUST NOT 用同一段数据既调阈值又报告准确率。

#### Scenario: 标的池冻结
- **WHEN** 冻结参数集生成后进入独立验证段
- **THEN** 验证段使用的标的清单 MUST 与训练段逐项一致，MUST NOT 出现训练段没有的标的

#### Scenario: 训练段不随数据延长
- **WHEN** 原始数据更新到更晚交易日
- **THEN** 训练段结束日期 MUST 仍为 `2024-12-31`，MUST NOT 被扩展

#### Scenario: 调参与报告分离
- **WHEN** 报告新算法的准确率
- **THEN** 报告 MUST 明确标注参数在哪段数据上选取、准确率在哪段数据上验证，两段 MUST 有明确起止日期且 MUST NOT 重叠

### Requirement: 隔离期覆盖最长前瞻窗口

训练段与验证段之间 MUST 设置隔离期（embargo），长度 MUST 不少于本次评价所需的最长前瞻窗口 **30 个交易日**，且 MUST 与 5% 浮亏约束所用的 20 日窗口叠加后的最大跨度一致。训练段末尾 30 个交易日内产生的信号 MUST NOT 被用于调参或计入训练段准确率，MUST NOT 跨入验证段。

#### Scenario: 隔离期长度达标
- **WHEN** 生成研究输出的分段定义
- **THEN** 输出 MUST 给出训练段结束日、隔离期起止日与验证段起始日，且隔离期交易日数 MUST ≥ 30

#### Scenario: 跨段信号不串用
- **WHEN** 某信号发生在训练段最后 10 个交易日内，其 30 日持有期跨越到隔离期或验证段
- **THEN** 该信号 MUST NOT 计入训练段准确率，MUST NOT 被用于参数筛选

### Requirement: 固定池研究结果留痕

每次研究 MUST 输出：参与比较的候选参数组合清单（含被淘汰者及淘汰原因）、冻结后的拐点幅度与间隔阈值、六维闸门阈值、浮亏阈值、**28 条有效幅度线及其训练段 75 分位依据**、标的池清单、输入数据哈希与随机种子。验证段 MUST 按标的、方向、持有期分别给出样本数与准确率，MUST NOT 只报告合并最优值。研究脚本 MUST NOT 写入生产信号文件（`data/index-signals.json`、`data/sector-trends.json`），产物 MUST 落在 `data/research/` 独立目录并附 manifest。

#### Scenario: 淘汰组合可见
- **WHEN** 训练段比较了多组拐点幅度与闸门阈值
- **THEN** 报告 MUST 列出全部组合及其训练段样本数与结果、被淘汰者的淘汰原因，MUST NOT 只展示最终选定组合

#### Scenario: 生产产物不受影响
- **WHEN** 研究脚本完成一次完整运行
- **THEN** `data/index-signals.json` 与 `data/sector-trends.json` MUST 与运行前逐字节一致，删除研究产物后生产构建 MUST 不受影响
