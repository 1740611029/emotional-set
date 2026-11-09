# 无未来函数自检结论与三层确认日志字段说明

## 1. 因果性约束

- 拐点采用 ZigZag 事后确认制：某日仅在其后价格反向运动达冻结幅度阈值、且满足最小间隔时才被事后标注为拐点；基准集只作评价标尺，不参与信号产生。
- 六因子闸门在**拐点极值日**取值（design D11）：所有因子数据当日已可知，而候选本身在拐点自然确认日才产生，因此不使用任何未来信息。
- 三层确认起点 MUST 为拐点自然确认日，窗口自该日起最多 3 个交易日（含当日），代码中带 `position < natural` 断言，确认日不可能早于拐点可知日。
- 无全量归一化、无未来 K 线、无事后修订数据；所有滚动窗口只向前看。

## 2. 截断自检

- 规则：把 T 日之后行情置空或打乱后重新计算，T 日及之前的信号集合（日期、类型、价格、等级、确认日志）MUST 逐条完全一致。
- 用例：`pipeline/test_precision_engine.py` 的 TestCausality 四项。
- 本次实跑结果：截断自检 **通过**、未来数据打乱 **通过**，对比 0 条 T 日及之前信号（截断点 2024-12-11）。
- 输入数据哈希 `df453b2fc13660a767d4aaed4741927314f5251af2c4e62ba4b57777851cd338`，据此可复算全部信号与拐点。

## 3. 三层确认逐层日志字段

| 层 | 字段 | 含义 |
| --- | --- | --- |
| 价格 | `close` / `candidate_reference` / `threshold` / `rule` / `passed` | 确认日收盘 vs 拐点极值日高/低价；规则为底部收盘高于候选日最高价、顶部低于候选日最低价 |
| 动量 | `open` / `close` / `rsi14` / `rsi14_previous` / `ma5` / `ma5_previous` / `rule` / `passed` | 底部要求阳线且 RSI14 上行；顶部要求阴线且 RSI14 下行且 MA5 下行 |
| 量能 | `volume_ratio` / `volume_ratio_threshold` / `breadth_delta5` / `breadth_threshold` / `rule` / `passed` | 量比达 1.2 或宽度 5 日变化达 0.05 |
| 汇总 | `date` / `offset` / `passed_count` / `passed` | 逐交易日记录，三层全通过才确认 |

日志覆盖**全部候选**：六因子闸门未通过者记 `reason=six_factor_gate_failed`；三层确认超时作废者记 `reason=three_layer_unconfirmed` 并附完整逐日逐层日志。每条已确认信号另记 `pivot_date`、`natural_confirm_date`、`signal_confirm_offset_from_pivot` 与 `signal_confirm_offset_from_natural`。

