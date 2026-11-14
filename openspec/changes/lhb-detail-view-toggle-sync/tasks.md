# Tasks

## 1. 删除 detailView 并恢复 selected 取值源

- [ ] 1.1 在 `signal-hub-web/src/pages/LhbTraders.tsx` 中删除 `detailView` useMemo（约第 506-512 行），并删除其所有引用。验证：`npx tsc --noEmit` 报 `detailView` 未定义 → 确认所有引用已清除后编译通过。
- [ ] 1.2 将 `selected` 的取值从 `detailView.traders` 改回 `sorted.find(...)`，依赖从 `[detailView, sortKey, selectedId]` 改为 `[sorted, selectedId]`。验证：`npx tsc --noEmit` 零错误，`git diff` 显示 `selected` 的取值源为 `sorted`。

## 2. 单元测试与集成验证

- [ ] 2.1 确认 `tests/lhbExitVisibility.test.ts` 既有 12 项测试不受影响（纯函数无改动）。验证：`npm test` 全绿。
- [ ] 2.2 确认 `npm run build` 通过、`dist/` 产物正常生成。验证：退出码为 0。
- [ ] 2.3 手工验收（当前产物 `as_of = 2026-11-13`）：开关关闭时点入任一游资详情页 → 不含 `exit_date ≤ 2026-11-05`（age ≥ 6）的记录；开关开启时点入 → 含这些记录并带离场标记；切换开关后重新点入同一游资 → 即时反映新状态。验证：在实施记录中写明核对结果。

## 3. 变更范围核对

- [ ] 3.1 `git diff --name-only` 仅含 `src/pages/LhbTraders.tsx` 与 `dist/` 构建产物。验证：逐项比对，确认纯函数、管道、产物、服务端、信号链路文件均未改动。
