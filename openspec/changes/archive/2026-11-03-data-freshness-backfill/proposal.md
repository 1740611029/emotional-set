# Proposal

## Why

三类原始数据的发布时点差别很大（实测：同花顺指数日线当日 15:01 已可得、中金所机构持仓当日 18:19 前已披露，而申万板块日线当日 16:12 尚未发布、常拖到次日上午），导致 15:00 那一次收盘重算经常拿不到板块最新日线；旧实现把「尝试过」当成「当天已完成」，且每天每类限重试 3 次，结果是页面长时间停在旧数据、当天不再补拉、次日启动也可能被限次挡掉。

## What Changes

- 服务启动时 MUST 先判定本地日线是否覆盖最近已完成交易日：落后则**先补齐、补齐之后才出首屏数据**；补齐失败（无网络或源未发布）**不阻断启动**，用现有数据照常出图
- 收盘重算的「当天完成」判定收紧为**只有真的覆盖到最近交易日才算完成**，没补到不再标记完成
- 新增收盘后的固定补齐时刻 17:30 / 20:00 / 22:00：仅当仍落后时才动网络，积压的时刻只跑最靠后的一轮
- 「已补齐」标记改为以最近交易日为键：跨过 15:00 使预期日推进时标记自动失效；**失败不计入当日次数**，下一次启动仍会重试（取消每天每类 3 次的硬上限）
- 健康检查端点新增 `backfill` 观测字段（收盘尝试次数、当天完成日期、已试过的补齐时刻）
- 手动重建脚本新增「先补数据再重建」开关：`重建信号数据.bat [fetch] [--force]` 与 `./rebuild.sh fetch`，由 `pipeline/refresh_and_rebuild.py` 承载
- 新增板块数据源换源影响调研（`pipeline/sector_source_survey.py`）：只产出报告，**不改生产口径**；结论为保持申万行业指数口径

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `live-data-api`: 收盘重算的完成判定与晚间固定补齐时刻、启动时先补齐再出首屏（失败也继续启动）、健康检查暴露补齐观测字段
- `market-data-pipeline`: 原始数据补齐新增「新鲜度判定 + 已补齐标记以最近交易日为键 + 失败不计数」规则，以及板块数据源候选调研的留痕约束（不改口径）

## Impact

- 后端：`server/scheduler.py`（启动顺序、`_backfill_blocking`、`_tick_close` 完成判定、`_tick_evening`、`EVENING_SLOTS`、`status()`）
- 流水线：`pipeline/bootstrap.py`（`ensure_fresh_data` 的标记与重试语义、`_mark_done`/`_done_marks`/`_attempt_count`）、`pipeline/refresh_and_rebuild.py`（新增）、`pipeline/sector_source_survey.py`（新增）
- 脚本与文档：`重建信号数据.bat`、`rebuild.sh`、`README.md`
- 数据：新增缓存 `data/raw/sectors-em/`（东财板块日线，仅调研用）与报告 `data/research/sector-source-em-vs-sw.{md,json}`
- 信号计算：无改动——三层确认算法、复权口径、板块数据源均保持不变
- 验证：`pytest server` 55 passed；`pytest pipeline server` 272 passed（仅 1 个既有 openspec 校验用例失败，与本变更无关）
