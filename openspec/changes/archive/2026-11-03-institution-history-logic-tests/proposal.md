# Proposal

## Why

归档变更 `fix-institution-history-table-height` 的校验报告指出两点：

- 「历史明细列表」的 spec 场景（默认倒序、行数上限、筛选空结果、固定高度滚动加载、进度文案、筛选重置、空状态高度一致）此前仅有浏览器人工验证，无自动化测试覆盖。
- 组件内重置逻辑原来只依赖 `rows.length`：切换筛选后若行数恰好相同，不会把已加载行数重置回首批，与 spec「筛选对所有列表生效：分页 MUST 重置」不符。

本次为纯测试补强与实现对齐，不引入任何规范级行为变更。

## What Changes

- 将「历史明细列表」的倒序筛选、滚动加载触发、批次递增封顶、加载进度文案、初始可视行数等逻辑从页面组件抽出为纯函数模块 `signal-hub-web/src/lib/institutionHistory.ts`，页面复用该模块（行为等价，可测）
- 修正重置逻辑：改为依赖「筛选指纹（品种+日期区间）+ 结果行数 + 首行日期」，确保切换筛选必然重置回首批，且 30 秒自动刷新（数据引用变化但内容不变）不触发误重置
- 新增 `signal-hub-web/tests/institutionHistory.test.ts`，用项目既有 `node:test` 纯函数测试模式覆盖上述场景
- 表格固定高度常量收敛为 `HISTORY_TABLE_HEIGHT_PX = 250`（组件用内联 style 引用同一常量），保持与 spec 数值一致

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

（无 —— 本次不改任何可观察行为契约，`institution-position-view` 的相关需求与场景维持归档变更已同步的内容；重置修复是让实现符合既有需求，非新增或修改需求）

## Impact

- 前端：`signal-hub-web/src/lib/institutionHistory.ts`（新增）、`signal-hub-web/src/pages/InstitutionPositions.tsx`（复用重构 + 重置修复）、`signal-hub-web/tests/institutionHistory.test.ts`（新增）
- 数据接口 / 后端 / 信号计算：无影响
- 规范：无变更（skip_specs）
- 验证：`npm test` 29/29 通过；`npm run build`（含 `tsc`）成功
