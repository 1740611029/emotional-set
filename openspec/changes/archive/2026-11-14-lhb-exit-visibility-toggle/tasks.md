# Tasks

## 1. 过滤纯函数抽取与参数化（含单元测试）

- [ ] 1.1 新增 `signal-hub-web/src/lib/lhbExitVisibility.ts`：从 `pages/LhbTraders.tsx` 迁出并导出 `ViewTrader` 类型、`exitAgeInTradingDays()`、`buildViewTraders(rawTraders, tradingDates, asOf, options)`，其中 `options = { hideAfterDays, includeExpired }`；集中定义 `EXIT_VISIBILITY_DAYS = 5`（游资总览 + 个股游资总览）与 `DAILY_VIEW_MARK_DAYS = 3`（每日动向既有口径，原 `EXIT_MARK_DAYS` 改名），并在过滤时收集 `hiddenCodes: Set<string>`。验证：`npx tsc --noEmit` 通过，且 `node --experimental-strip-types -e "import('./src/lib/lhbExitVisibility.ts')"` 可无 DOM 加载。
- [ ] 1.2 实现边界与异常分支：`age = tradingDates.indexOf(asOf) - tradingDates.indexOf(exit_date)`，仅当 `age > hideAfterDays` 隐藏（5 日视图 `age=5` 展示 / `age=6` 隐藏，3 日视图 `age=3` 展示 / `age=4` 隐藏）；`includeExpired = true` 时跳过隐藏并把配对写入 `exits` Map；`active` / `positions` 缺失 / `exit_date` 为 null 或不在序列 / `asOf` 缺失 / `j < i` 一律保守展示；「整卡隐藏」分支仅在 `includeExpired = false` 时生效。验证：1.3 的测试用例全部通过，且 `git diff` 显示页面内原有 `EXIT_MARK_DAYS = 3` 常量与 `buildViewTraders` 已删除。
- [ ] 1.3 新增 `signal-hub-web/tests/lhbExitVisibility.test.ts`（`node:test` + `assert/strict`，范式照 `tests/etfVolumeSeries.test.ts`），覆盖：`age=5` 展示 / `age=6` 隐藏、三种离场状态同等处理、`active` 始终展示、`exit_date` 不在序列与旧版无 `positions` 的保守展示、`includeExpired` 开启后回显且带标记、关闭后集合复原、`hiddenCodes.size` 精确计数、`asOf` 前移一日后计数更新、`hiddenCodes` 含「同股既有可见又有隐藏配对」的混合场景。验证：`npm test` 全绿且新测试文件被 `tests/*.test.ts` 收集。
- [ ] 1.4 固化【每日动向】基线等价：在同一份 `data/lhb-traders.json` 上，断言 `buildViewTraders(..., { hideAfterDays: 3, includeExpired: false })` 产出的 `records` / `stats` / `exits` 与改动前旧实现逐项一致（可在本测试文件中内联旧逻辑对照），并按日期聚合核对每日动向可见行数（实测 355 行，5 日视图会是 432 行）。验证：对照断言通过，确认每日动向逐日行集合未被改写。
- [ ] 1.5 固化 5 日视图独立性：断言 `buildViewTraders(..., { hideAfterDays: 5, includeExpired: false })` **直接基于原始 `data.traders`** 构建，能保留 `age=4` / `age=5` 的配对，且其 `hiddenCodes` 不含这两类配对。验证：该断言通过，确认不存在「3 日裁剪先删掉第 4~5 日配对」的问题。

## 2. 页面接线、切换按钮与口径说明（含页面级验证）

- [ ] 2.1 改 `signal-hub-web/src/pages/LhbTraders.tsx`：改为 import `src/lib/lhbExitVisibility.ts`；从同一份 `data.traders` 构建两份视图——`mainTraders = { hideAfterDays: 5, includeExpired: showExpired }` 供游资总览（`sorted`、详情）与个股游资总览，`dailyTraders = { hideAfterDays: 3, includeExpired: false }` **仅**供每日动向（`dailyRows`）；`dailyTraders` 的 `useMemo` 依赖不得包含 `showExpired`；删除页面内旧常量与函数。验证：`npm run build`（`tsc && vite build`）通过，且 `dailyRows` 只消费 `dailyTraders`。
- [ ] 2.2 在 `LhbTraders()` 内增加页面级 `const [showExpired, setShowExpired] = useState(false)`；在【游资总览】Tab 按钮行（卡片列表态，与排序同排）与【个股游资总览】Tab 表头各渲染一个「显示 / 隐藏 5 日外离场个股（N）」按钮，二者共用同一 state，N 取 5 日视图的 `hiddenCodes.size`；复用现有 `rounded-lg px-4 py-2 text-sm` + `brand-50/brand-500` 样式、不新增主色；【每日动向】不渲染按钮；加载 / 错误 / `preparing` 状态下两个按钮都不渲染。验证：手动进入页面确认两处按钮默认关闭态且状态同步、标签数字一致，三种异常状态下页面不报错、不出现「0 只」。
- [ ] 2.3 同步口径说明文案：把 `Notice`（原第 589 行）与个股游资总览表头说明改为分 Tab 表述——游资总览与个股游资总览统一 5 个交易日、第 6 个交易日起默认隐藏且可切换显示、每日动向保持既有 3 个交易日口径、数据完整保留于产物。验证：页面与源码中不再出现笼统的「标记保留 3 个交易日，之后该游资在该股的操作不再展示」。
- [ ] 2.4 页面级验收（当前产物 `as_of = 2026-11-13`）：`exit_date = 2026-11-06`（相隔 5 日）的配对在游资总览与个股游资总览默认可见、`2026-11-05`（相隔 6 日）默认不可见且在任一 Tab 点击按钮后两个 Tab 同时可见并带离场标记；游资卡片统计与个股行的合计净买入、在场游资列表、「共 N 只个股」随开关同步自洽；【每日动向】在开关前后每个日期的行集合完全一致且无按钮。验证：手工逐项核对并在实施记录中写明结果。

## 3. 管道文案与产物 `scope_note` 定点同步（含校验）

- [ ] 3.1 只改 `pipeline/lhb_traders.py` 的文案：第 58 行 `EXIT_MARK_DAYS` 注释、第 340 行 `_compute_positions` docstring、第 549 行 `scope_note` 生成串，改为分 Tab 口径（游资总览 + 个股游资总览 5 日可切换、每日动向保持 3 日）；**不得**改动 `_compute_positions()` 逻辑、`SILENT_PROBABLE` / `SILENT_FORCED` / `HEAVY_SELL_RATIO` / `TURNOVER_COVER_X`、`ALGORITHM_VERSION` 或任何数值。验证：`git diff pipeline/lhb_traders.py` 仅显示注释与字符串行变化，且 `python -m py_compile pipeline/lhb_traders.py` 通过。
- [ ] 3.2 对 `data/lhb-traders.json` 的 `scope_note` 做**字符串级定点替换**（只替换该字段的值，不重跑 `--aggregate-only`、不整体 `json.dump` 重排文件）；替换前后解析 JSON 做 deep-equal 校验，断言除 `scope_note` 外所有键与数值完全一致，并过 `pipeline/schemas.py` 的 lhb schema 校验。验证：校验脚本输出「仅 scope_note 变化」且 `positions` / `trading_dates` / 金额逐字段不变，`git diff --numstat data/lhb-traders.json` 显示为极小改动。
- [ ] 3.3 确认服务端与调度零改动：`server/lhb.py`、`server/scheduler.py`、`pipeline/schemas.py` 均不在本次 diff 中；`scope_note` 经 `/api/lhb/overview` 透传后与页面 `Notice` 表述一致。验证：`git diff --name-only` 中无这三个文件，且接口返回的 `scope_note` 同时含「5 个交易日」与「每日动向」字样。

## 4. 集成与回归验收

- [ ] 4.1 全量跑 `npm test` 与 `npm run build`，确认既有用例（ETF、机构、板块、精度矩阵等）无回归、`dist/` 产物正常生成。验证：两条命令退出码为 0 且无新增 TypeScript 报错。
- [ ] 4.2 核对无新增网络请求与无信号链路污染：切换开关时浏览器 Network 仅既有 `/api/lhb/overview`，无新增端点；`git diff --name-only` 仅含 `src/lib/lhbExitVisibility.ts`、`src/pages/LhbTraders.tsx`、`tests/lhbExitVisibility.test.ts`、`pipeline/lhb_traders.py`、`data/lhb-traders.json` 与构建产物 `dist/`。验证：逐项比对变更清单，确认顶部/底部信号、板块趋势、三层确认相关文件均未被改动。
- [ ] 4.3 回滚演练检查：确认本变更未改动产物任何数值、未做数据迁移、开关为页面内 state，因此还原源码文件即可回到现行 3 交易日统一过滤、无残留状态。验证：在设计文档验收记录中写明「无需数据回滚」的依据。
