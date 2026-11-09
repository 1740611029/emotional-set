# Spec Delta

## MODIFIED Requirements

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
- **THEN** 该项 MUST 同时给出胜率、已判定样本数、成功数、未决数与 Wilson 95% 区间，且 `small_sample` MUST 如实反映样本不足

#### Scenario: 评估缺失时不伪造

- **WHEN** 评估数据尚未生成
- **THEN** 快照 MUST 返回空评估结构，MUST NOT 返回示例数值

#### Scenario: 说明文字随评估返回

- **WHEN** 客户端读取 `sectors.evaluation`
- **THEN** 响应 MUST 携带「板块趋势为描述性分类、方向胜率不代表交易收益」的说明，且 MUST NOT 携带任何买卖建议或预期收益表述
