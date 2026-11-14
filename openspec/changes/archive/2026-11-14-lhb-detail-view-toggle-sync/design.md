# Design: 游资详情页开关状态同步修复

## 问题根因

上一轮变更 `lhb-exit-visibility-toggle` 实施后，为修复「开关开启后详情页展示超期记录」的投诉，在 `LhbTraders.tsx` 中新增了一份 `detailView`（`includeExpired=false` 硬编码），并将 `selected` 的取值源从 `sorted` 改为 `detailView.traders`。这导致详情页与开关完全脱钩——开关关闭时详情页表现正确，但开关开启后详情页仍然只展示 5 日内配对；反之，若用户在开关开启态进入详情页、返回后关闭开关、再次进入同一游资详情页，详情页会残留超期记录。

实际上主规格第 1 条 Requirement 已明确「（卡片与游资详情）」需遵循同一窗口与开关——原始实现（`selected` 从 `sorted` 取）本身就是正确的。上一版的「修复」反而引入了违反规格的 bug。

## 修复方案

**删掉 `detailView`，将 `selected` 改回从 `sorted` 取。**

具体变更（`signal-hub-web/src/pages/LhbTraders.tsx`）：

1. 删除 `detailView` useMemo（约 6 行）。
2. 将 `selected` 的 useMemo 依赖从 `[detailView, sortKey, selectedId]` 改为 `[sorted, selectedId]`，取值源从 `detailView.traders` 改回 `sorted`：
   ```ts
   const selected = sorted.find((t) => t.id === selectedId) ?? null;
   ```
3. 不新增任何视图、参数、state 或组件。

这样 `selected` 直接来自 `mainView.traders`（受 `showExpired` 控制），开关对卡片列表、详情页、个股总览三处统一生效。

## 为什么不需要新的视图或参数

`mainView` 已经是受开关控制的 5 日视图，`sorted` 是 `mainView.traders` 排序后的副本。详情页只需要从中取出 `selectedId` 对应的 trader 即可。引入 `detailView` 是多余的——它创建了一份与 `mainView` 完全相同（`hideAfterDays=5`）但 `includeExpired` 硬编码为 `false` 的视图，唯一效果就是把详情页从开关链路中摘出去。

## 风险

- **无新增风险**：此修复恢复的是上一轮变更之前的原始逻辑，该逻辑已通过 `npm test` 和 `npm run build` 验证。
- **不涉及产物/管道/服务端**：仅前端组件内部变更。
- **回滚**：还原 `detailView` 即可回到当前（有 bug 的）状态。

## 验证

- `npx tsc --noEmit` 零错误
- `npm test` 全绿（既有 72 项不受影响）
- `npm run build` 通过
- 手工验证：开关关闭 → 点入详情页 → 不含超期配对；开关开启 → 点入详情页 → 含超期配对；切换开关后重新点入 → 即时反映
