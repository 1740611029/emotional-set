# Tasks

实现已在本次会话完成，勾选表示已交付并通过对应验证。

## 1. 新鲜度判定与重试语义（pipeline/bootstrap.py）

- [x] 1.1 `data_gap()` 按交易日历（缺失退化为工作日近似，标注 `basis`）逐类判定是否落后最近已完成交易日 —— 验证：`pipeline/test_trading_calendar.py` 的 `DataGapTest`/`LagCountTest`/`LastCompletedTradingDayTest`
- [x] 1.2 完成标记改为以最近交易日为键：新增 `_mark_done`/`_done_marks`/`_attempt_count`，`tries` 仅用于日志，取消每天每类 3 次硬上限 —— 验证：`AttemptStateTest.test_expected_change_invalidates_done_marker`、`test_failed_backfill_is_retried_on_next_startup`、`test_success_records_done_marker`
- [x] 1.3 未真的补到时输出"下次启动会再试"的日志，`--force` 语义改为忽略"当日已补齐"标记 —— 验证：`pipeline -q` 全绿；`python pipeline/bootstrap.py --check` 可复现判定输出

## 2. 启动阻塞补齐与晚间时刻（server/scheduler.py）

- [x] 2.1 `_bootstrap` 顺序改为 原始数据 → `_backfill_blocking()` → `_ensure_close_data()` → `_rebuild_close()`，任一步失败只记 `last_error` 不阻断后续 —— 验证：`StartupBackfillTest`（顺序断言 + 补齐失败仍启动 + 原始数据失败仍继续）
- [x] 2.2 抽出同步 `_backfill_blocking()`：补到才重算并原子落盘两份 JSON、`invalidate()` 内存 store，返回是否真补到 —— 验证：`EveningBackfillTest.test_stale_data_triggers_backfill_and_rebuild`（写盘 2 次）
- [x] 2.3 `_tick_close` 仅在 `data_gap()["stale"]` 为空时才设 `_close_date`，删除"跑过即完成"的无条件标记 —— 验证：`CloseCompletionGateTest` 两个用例
- [x] 2.4 新增 `EVENING_SLOTS`（17:30/20:00/22:00）与 `_tick_evening`：数据不落后不发请求、积压只跑最靠后一轮、按日重置、成功才标记完成 —— 验证：`EveningBackfillTest` 5 个用例
- [x] 2.5 `status()` 暴露 `backfill`（收盘尝试次数、当天完成日期、已试时刻） —— 验证：`EveningBackfillTest` 断言 + 运行态 `GET /api/health` 返回该字段

## 3. 手动重建的补数据开关

- [x] 3.1 新增 `pipeline/refresh_and_rebuild.py`：判定 → 增量补齐 → 无论是否拉到新行情都重建，退出码只表达重建是否完成 —— 验证：`pipeline/test_refresh_and_rebuild.py` 7 个用例
- [x] 3.2 `重建信号数据.bat` 增加参数解析（`fetch`/`rebuild`/`--force`）与交互菜单（回车=仅重建，非法输入重试） —— 验证：实测 `fetch`、无参回车、非法输入三条路径
- [x] 3.3 `rebuild.sh` 增加 `fetch` 分支与未知模式报错 —— 验证：本机（Windows）没有 bash，未做实机执行；需在 macOS/Linux 上跑 `./rebuild.sh` 与 `./rebuild.sh fetch` 确认（已归档为待环内验证项）

## 4. 板块数据源换源影响调研

- [x] 4.1 新增 `pipeline/sector_source_survey.py`：东财行业板块指数与申万指数在相同算法下比较四态、有向强度与累计净值 —— 验证：`pipeline/test_sector_source_survey.py` 11 个用例
- [x] 4.2 产出报告 `data/research/sector-source-em-vs-sw.{md,json}`，含区间、行业样本数、日线与状态样本量、翻转类型分布、未覆盖范围 —— 验证：报告已落盘，31/31 行业纳入，14973 行业日样本
- [x] 4.3 结论保持申万口径，生产数据源与复权口径未变 —— 验证：`data/raw/sectors/` 与两份信号产物的生成路径未被改动

## 5. 文档与回归

- [x] 5.1 `README.md` 更新「什么时候会重建信号」「数据与刷新机制」「目录结构」「手动重建」「常见问题」，写清发布时点实测与不换源结论 —— 验证：文档与代码行为逐条对照
- [x] 5.2 全量回归 —— 验证：`pytest server` 55 passed；`pytest pipeline server` 272 passed / 4 skipped / 1 failed（既有 `run_self_test.py::test_openspec_validate` 因引用已归档 spec 失败，与本变更无关）
