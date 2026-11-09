# 回滚演练（任务 7.4）

生成时间：2026-11-09T13:40:09+08:00。

- 方式：把 `data/research/precision-backup/` 中重建前的 3 份产物复制到**临时目录**，在临时副本上验证，**生产文件未被覆盖**。
- 旧产物信号条数 82；不含新矩阵字段：True。
- 旧聚合 `accuracy.rate` 仍可读，前端 `overallAccuracy` 与【指标详情】页继续工作。
- 前端 `precisionMatrix` 对不含矩阵的旧快照返回全格空态（显示 `--` 而非 0%），见 `signal-hub-web/tests/precisionMatrix.test.ts`。
- `validate_payload` 已改为「precision 块可选、一旦存在则必须完整」，旧产物通过结构校验；部分矩阵则被拒绝（见 `test_precision_schema.py`）。

**结论：通过**，问题数 0。

恢复方式（需人工执行时）：把备份目录的 `index-signals.json`、`sector-trends.json`、`etf-volume-events.json` 覆盖回 `data/`，并把 `confirmed_index_signals.ACTIVE_ALGORITHM` 切回所需版本；本变更未改动该开关，生产信号算法仍为 `index-swing6-v2.0`。

