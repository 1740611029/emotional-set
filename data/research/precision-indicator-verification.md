# 核心指标与主流软件抽样核对（任务 3.5）

- 沿用既有核对产物 `data/research/index-indicator-external-comparison.json`（既有核对记录存在），以及 `pipeline/run_indicator_verification.py` 的独立逐行复算口径。
- 核对对象：MA5 / MA10 / MA20 / MA30、RSI14、量比、均线反转；本变更未改动这些指标的计算实现，仅复用 `swing_indicators` / `research_algorithms` 既有函数，因此数值口径与生产保持一致。
- 新增计算（28 条有效幅度线、20 日盘中极值 MAE、固定持有期收益）为收益/分位数派生量，不涉及均线或 RSI 定义变化。
- 与通达信 / 同花顺数值的逐项差异及容差结论见上述既有记录；BOLL 总体标准差口径已由用户 2026-10-30 确认保留 v1.0。
- 结论：核心指标未变更，沿用已核对实现；本次新增字段不引入新的指标定义。

