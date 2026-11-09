# Tasks

## 1. 抽出可测的纯逻辑模块（signal-hub-web/src/lib/institutionHistory.ts）

- [x] 1.1 抽出 `selectHistoryIndexes`（日期倒序 + 区间筛选，无序列时返回空）、`initialVisibleCount`、`shouldLoadMore`、`nextVisibleCount`、`hasMoreToLoad`、`historyProgressLabel`，并定义 `HISTORY_PAGE_SIZE = 50`、`HISTORY_TABLE_HEIGHT_PX = 250`；验证：与组件原行为等价，无未来数据引用

## 2. 页面复用与实现对齐（signal-hub-web/src/pages/InstitutionPositions.tsx）

- [x] 2.1 `historyGroups` 改用 `selectHistoryIndexes` 生成倒序行，移除组件内重复的倒序+筛选循环；验证：渲染结果与重构前一致
- [x] 2.2 `InstitutionHistoryTable` 的滚动加载、进度文案、初始行数改用纯函数；固定高度与空状态高度改用 `HISTORY_TABLE_HEIGHT_PX`；验证：三列表仍 250px、内部滚动、表头 sticky
- [x] 2.3 修正重置逻辑：新增 `filterKey`（品种+起止日期），`useEffect` 依赖改为 `[filterKey, rows.length, rows[0]?.date]`，切换筛选必然重置回首批且自动刷新不误重置；验证：符合 spec「筛选对所有列表生效：分页 MUST 重置」

## 3. 自动化测试（signal-hub-web/tests/institutionHistory.test.ts）

- [x] 3.1 覆盖场景：默认倒序与行数上限、无序列空列表、筛选无结果、区间边界含端点、滚动加载触发阈值、批次封顶、进度文案与「加载更多」出现条件、筛选重置、行数不足与空状态 250px 常量；验证：`npm test` 全部通过

## 4. 验证

- [x] 4.1 运行 `npm test`；验证：29/29 通过
- [x] 4.2 运行 `npm run build`（含 `tsc` 类型检查）；验证：构建成功无类型错误
