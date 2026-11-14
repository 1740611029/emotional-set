# Proposal: 游资详情页开关状态同步修复

## Why

上一轮变更 `lhb-exit-visibility-toggle` 实施后引入了一个回归 bug：游资详情页（`TraderDetail`）的 `selected` trader 被改为从一份独立于开关的 `detailView`（`includeExpired=false` 硬编码）取，而非从受 `showExpired` 控制的 `mainView` / `sorted` 取。这导致开关状态对详情页永远不生效——用户点了「隐藏 5 日外离场个股」后进入详情页，超期离场的个股操作记录仍然展示。

主规格 `lhb-traders-view` 第 1 条 Requirement 已明确要求「【游资总览】（**卡片与游资详情**）与【个股游资总览】MUST 默认只展示...不超过 5 个交易日的配对」——即详情页必须跟随开关。当前实现违反了该规格。

## What Changes

- 删除 `LhbTraders.tsx` 中的 `detailView` useMemo（始终 `includeExpired=false` 的冗余视图）。
- 将 `selected` 的取值源从 `detailView.traders` 改回 `sorted`（即 `mainView.traders`），使开关状态对卡片列表与详情页统一生效。
- 不新增视图、不新增参数、不改动纯函数 `lhbExitVisibility.ts`、不改动管道与产物。
- 更新单元测试：新增断言确认 `selected` 来自受开关控制的视图（开关关闭时详情页不含超期配对、开关打开时详情页含超期配对）。

## Capabilities

- **Modified Capabilities**: `lhb-traders-view`（现有主规格 `openspec/specs/lhb-traders-view/spec.md`，修正「游资详情跟随开关」这一已有但被违反的 Requirement）

## Impact

- 代码：仅 `signal-hub-web/src/pages/LhbTraders.tsx`（删 `detailView`、改 `selected` 取值源）与 `signal-hub-web/tests/lhbExitVisibility.test.ts`（可选：页面级测试断言）。
- 不影响：`src/lib/lhbExitVisibility.ts`、`pipeline/lhb_traders.py`、`data/lhb-traders.json`、服务端、调度器、信号链路。
- 回滚：还原 `detailView` 即可（但还原后 bug 重现）。
