# Spec Delta

## ADDED Requirements

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
