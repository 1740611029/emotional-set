# Design

## Context

动机见 [proposal.md](file:///d:/情绪集合/openspec/changes/lhb-exit-visibility-toggle/proposal.md) - Why，行为契约见 `specs/lhb-traders-view/spec.md`。以下为影响方案的现状事实（均已读码核对）：

- `signal-hub-web/src/pages/LhbTraders.tsx` 中 `buildViewTraders()` 是**唯一**的展示过滤入口，产出 `ViewTrader[]`（在 `LhbOverviewTrader` 上附加 `exits: Map<code, LhbPosition>`）。`LhbTraders()` 只调用一次，结果 `traders` **同时**喂给三个 Tab：`sorted` → 游资总览卡片/详情、`dailyRows` → 每日动向、`StockOverviewView` → 个股游资总览。因此**任何对 `traders` 的原地改动都会同时改变三个 Tab**。
- 现有判定为 `if (j - i > EXIT_MARK_DAYS) return false;`，其中 `EXIT_MARK_DAYS = 3`，`i = tradingDates.indexOf(position.exit_date)`、`j = tradingDates.indexOf(asOf)`；`i < 0 || j < 0 || j < i` 时 `return true`（保守显示）。过滤后同时重算 `stats`（「展示什么就统计什么」），并在某游资全部记录被过滤掉时整卡 `continue`。`exits` Map 只在标记期内写入，驱动 `ExitBadge`。
- **每日动向确实受该过滤影响**：`dailyRows` 遍历的是过滤后的 `trader.records`，超期配对的**全部历史记录**（含离场前的操作）都不会出现在按日表格里，`exit_status` 也只在标记期内有值。实测当前产物下，3 日视图可见 355 行、5 日视图会是 432 行（差 77 行、涉及 16 个交易日）；游资总览卡片数 21 → 22。用户要求「每日动向原本怎么展示就怎么展示」，即**保持 3 日过滤结果**。
- `StockOverviewView` 用 `aggregateStocks(traders, asOf)` 从 `trader.records` 聚合个股行；`stock.traders[].exitStatus` 取自 `trader.exits`。表格头部有「共 N 只个股」与一段固定口径说明，是放置开关的现成位置；游资总览的 Tab 按钮行（现有排序按钮处）是另一个放置点。
- 产物字段已齐备：`positions[].status / exit_date / last_seen`、`trading_dates`、`as_of`、`scope_note` 均由 `server/lhb.py:overview()` 原样透传，前端**无需新端点、无需新字段**。
- 实测产物 `data/lhb-traders.json`（`as_of=2026-11-13`，24 个交易日）：488 个离场配对（`exited_confirmed` 227 / `exited_probable` 23 / `exited_forced` 238），其中 182 个距 `exit_date` 为 0~5 个交易日、306 个 ≥6 个交易日。这是「默认隐藏」而非「默认展示」的直接依据。
- 边界日期已核对：`2026-11-06` 是 `as_of` 前第 5 个交易日，`2026-11-05` 是第 6 个。
- 前端测试为 `node --experimental-strip-types --test tests/*.test.ts`，范式见 `tests/etfVolumeSeries.test.ts`：把纯函数放在 `src/lib/*.ts` 中导出，测试直接 import，不渲染 React。
- `pipeline/lhb_traders.py` 的 `EXIT_MARK_DAYS = 3` 是**纯文档常量**（不参与 `_compute_positions()` 计算），出现在第 58 行注释、第 340 行 docstring 与第 549 行 `scope_note` 文案；`scope_note` 会随产物落盘。前端 `Notice` 则把「3 个交易日」硬编码在第 589 行。

## Goals / Non-Goals

**Goals:**

- 让【游资总览】与【个股游资总览】共用一份 5 交易日过滤视图与一个开关状态，**零回归**地保留【每日动向】的既有展示。
- 把过滤逻辑做成可单测的纯函数，边界、异常分支与每日动向基线由自动化测试覆盖。
- 口径文案（页面 `Notice` + 产物 `scope_note`）与实现一致，准确写出两套阈值。

**Non-Goals:**

- 不改 `pipeline/_compute_positions()`、判定阈值、`ALGORITHM_VERSION`、产物数值或 schema；不做产物全量重算。
- 不改服务端、路由、类型契约 `LhbOverview` / `LhbPosition`；不新增组件库或依赖；不引入新的主色。
- 不做开关状态持久化、不做 URL 同步；不给每日动向加按钮或状态联动。

## Decisions

### 1. 两份视图，分割线是「5 日 / 3 日」而不是「个股总览 / 其他 Tab」

把 `buildViewTraders()` 参数化为 `buildViewTraders(rawTraders, tradingDates, asOf, options)`，`options = { hideAfterDays, includeExpired }`，从**同一份** `data.traders` 各自独立构建两份视图：

| 视图 | 参数 | 消费方 |
| --- | --- | --- |
| `mainTraders` | `{ hideAfterDays: 5, includeExpired: showExpired }` | 游资总览（卡片/详情）、个股游资总览 |
| `dailyTraders` | `{ hideAfterDays: 3, includeExpired: false }` | 仅每日动向（保持原样） |

这里仍需要两份视图，原因与第一版设计**不同**：不再是「5 日视图被上游 3 日裁剪卡住」，而是「每日动向必须继续消费 3 日过滤结果，而另两个 Tab 要 5 日」。`mainTraders` 直接从原始 `data.traders` 按 5 日构建，**不经过任何 3 日视图**，第 4~5 日配对不会被提前删除；`dailyTraders` 的参数组合与现状逐位等价（`includeExpired=false` 时走原有 `j - i > hideAfterDays` 分支），是「不变的基线」。

- 备选 A：把 `EXIT_MARK_DAYS` 常量从 3 直接改成 5、继续用一份共享 `traders` → 排除。这会让每日动向一起变成 5 日，违背用户「每日动向原本怎么展示就怎么展示」。
- 备选 B：只给 `dailyRows` 在渲染层做二次 3 日过滤 → 排除。`mainTraders` 在 `includeExpired=true` 时包含全部超期配对，渲染层要重建已丢失的 `exits` 与 `stats` 语义，易与卡片口径漂移。
- 备选 C：让服务端按参数返回不同阈值 → 排除。改变 `/api/lhb/*` 契约，且引入请求往返，与「纯展示、不重算」不符。

### 2. 过滤纯函数抽到 `src/lib/lhbExitVisibility.ts`

把 `exitAgeInTradingDays(position, tradingDates, asOf)` 与 `buildViewTraders(...)` 连同 `ViewTrader` 类型迁到 `signal-hub-web/src/lib/lhbExitVisibility.ts` 并导出，`LhbTraders.tsx` 只做 import 与渲染。

- 理由：`buildViewTraders` 目前是 `.tsx` 内的模块私有函数，`node --test` 无法直接 import；而边界（第 5 / 第 6 个交易日）、三种状态一致性、异常保守展示、两份视图互不影响、开关前后统计自洽都必须有回归测试。抽库与项目既有 `src/lib/etfVolumeSeries.ts` + `tests/etfVolumeSeries.test.ts` 范式一致。
- 备选：从页面模块直接 `export` → 排除。测试进程要加载含 JSX 与 React 的 `.tsx`，引入 DOM 假设与额外依赖，纯函数测试不应承担这些。

### 3. 阈值、起算日与边界判定集中在纯函数内

- 常量：`EXIT_VISIBILITY_DAYS = 5`（游资总览 + 个股游资总览）、`DAILY_VIEW_MARK_DAYS = 3`（每日动向既有口径，原 `EXIT_MARK_DAYS` 改名以表明作用域），均定义在 `lhbExitVisibility.ts`，不在组件里散落魔法数字。
- 天数：`i = tradingDates.indexOf(exit_date)`、`j = tradingDates.indexOf(asOf)`，`age = j - i`；**交易日序号差**，不是自然日差，不是 `last_seen`。
- 边界：`age > hideAfterDays` 才隐藏（与现有 `>` 语义一致）→ 5 日视图 `age = 5` 展示、`age = 6` 隐藏；3 日视图 `age = 3` 展示、`age = 4` 隐藏（现状）。`includeExpired = true` 时跳过隐藏并把该配对写入 `exits` Map，因此回显的配对照常带 `ExitBadge`。
- 异常：`status === 'active'`、`positions` 缺失、`exit_date == null`、`i < 0 || j < 0 || j < i` → 全部 `return true`（保守展示），且不写入 `exits`。这是沿用现有行为，不新增隐藏路径。
- 备选：用日历日或 `Date` 差值 → 排除。会引入周末/节假日误差，属于变更数据口径，违反 AGENTS 第 5 条。

### 4. 开关状态提升到页面级，两个 Tab 各渲染一个按钮

`const [showExpired, setShowExpired] = useState(false)` 定义在 `LhbTraders()` 中；`mainTraders` 用 `useMemo` 依赖 `rawTraders/tradingDates/asOf/showExpired` 构建，`dailyTraders` 的 `useMemo` **不依赖** `showExpired`。按钮在【游资总览】Tab 的按钮行（卡片列表态，与排序按钮同排）与【个股游资总览】Tab 表头各渲染一个，共用同一 `showExpired` / `setShowExpired`。【每日动向】不渲染按钮。样式复用现有 `rounded-lg px-4 py-2 text-sm` + `brand-50/brand-500`，不新增主色（AGENTS 第 13 条）。

- 理由：用户要求两个 Tab 都有按钮，共享状态可避免「在游资总览点开、切到个股总览还是关着」的困惑；把 `dailyTraders` 的 `useMemo` 依赖排除在 `showExpired` 之外，是「每日动向不受开关影响」的结构性保证。
- 备选：两个 Tab 各自 `useState` 独立状态 → 排除。同一份数据的同一套口径出现两个开关态，容易误读；若用户后续要求独立，只需把 state 下沉、无需改过滤层。
- 备选：`localStorage` 持久化 → 排除。proposal 已明确不做；且跨交易日缓存开关态会让用户误以为看到的仍是当前口径。
- **设计假设**：两个按钮共享状态是本次采用的默认实现（见 proposal「待确认的设计假设」）。若用户要求各自独立，只影响本决策与 `specs` 中「两处按钮状态同步」场景，不影响阈值口径与任务拆分主体。

### 5. 按钮计数取自过滤结果，不做二次推断

计数定义为：**被默认隐藏的（游资, 个股）配对涉及的去重股票代码数**。由 `buildViewTraders` 在过滤时顺带收集 `hiddenCodes: Set<string>`，两个按钮标签读同一个 `hiddenCodes.size`。这样计数与表格可见性同源，不会因 `aggregateStocks` 的聚合规则（同一股票可能有多个游资、其中部分在场）而出现「数字与点击效果对不上」。计数 MUST 基于 5 日视图，MUST NOT 混入 3 日视图的隐藏集合。

- 说明：若某股票同时存在可见配对与被隐藏配对，它仍计入 `hiddenCodes`——因为开关确实会改变该行（增加游资明细与金额）。标签统一表述为「5 日外离场个股（N）」，避免「被隐藏」二字在混合场景下产生歧义。
- 备选：统计被隐藏的**配对**数 → 排除，与用户「个股」的表述不符；备选：对 `aggregateStocks` 前后结果做集合差 → 排除，混合场景下会漏计，且多一次全量聚合。

### 6. 口径文案同步采用「定点替换」，不重跑产物

需要改的三处文案：前端 `Notice`（第 589 行）、`pipeline/lhb_traders.py` 第 58/340 行注释与 docstring、第 549 行 `scope_note` 生成串。统一改为分 Tab 描述：游资总览与个股游资总览统一 5 个交易日、第 6 日起默认隐藏且可切换显示；每日动向保持既有 3 个交易日口径；数据完整保留于产物。

落盘的 `data/lhb-traders.json` 用**字符串级定点替换**只更新 `scope_note` 的值：替换前后解析 JSON 做 deep-equal 校验，断言除 `scope_note` 外所有键与数值完全不变，并过 `pipeline/schemas.py` 的 lhb schema。

- 备选 A：`python pipeline/lhb_traders.py --aggregate-only` 全量重算 → 排除。会改写 `generated_at`，且 `ensure_board_map()` 可能带来板块字段变化；为一句说明文字触发数据重算，违反 AGENTS 第 5 条「口径固定」与第 3 条「只做需求」。
- 备选 B：不动落盘产物，等下次日更自然刷新 → 排除。交付时页面 `Notice` 与接口 `scope_note` 会互相矛盾，`specs` 中「口径说明文案同步」场景不成立。
- **不 bump `ALGORITHM_VERSION`**：本变更没有算法改动，改版本号会制造一条虚假的算法留痕（AGENTS 第 9 条针对的是信号算法变更）。

### 7. 统计一致性由现有「展示什么就统计什么」自然承接

`aggregateStocks` 只读 `trader.records`；`buildViewTraders` 已按可见记录重算 `stats` 并排序。因此开关切换会同步改变游资卡片的活跃天数/操作笔数/买卖合计/净买入，以及个股行的合计金额、在场游资列表与「共 N 只个股」，无需新增统计代码。需注意的是当 `includeExpired = true` 时不应触发「整卡隐藏」分支（`trader.records.length > 0 && records.length === 0 → continue`）；实现上该分支只在 `includeExpired = false` 时生效，避免开关开启后反而漏掉游资。`dailyTraders` 恒为 `includeExpired = false`，保持现状的整卡隐藏行为。

## Risks / Trade-offs

- **[风险] 每日动向与另两 Tab 口径不一致**（第 4~5 个交易日配对在游资总览/个股总览可见、在每日动向隐藏）→ 这是用户明确要求的「原本怎么展示就怎么展示」。缓解：页面 `Notice` 把两套数字写清楚，并在测试中用 3 日基线断言锁死每日动向逐日行集合（实测 355 行），防止后续被「好心对齐」。
- **[风险] 每日动向的「保持原样」被误解为「完全不过滤」** → 已读码核实现状是 3 日过滤；若用户本意是展示全部历史操作，需回退确认后再改，本版按字面「保持现状」执行。
- **[风险] 产物 `scope_note` 定点替换破坏 JSON** → 缓解：替换前后 deep-equal 校验 + schema 校验；只允许改这一个字符串值，其他字段逐字节不变。
- **[风险] `trading_dates` 只覆盖 24 个交易日，早期 `exit_date` 可能落在窗口外** → 缓解：沿用保守展示分支（`i < 0` 时 `return true`），不隐藏；不插值、不外推交易日历。
- **[风险] 开关开启后表格行数显著增加**（实测约 306 个配对）→ 缓解：表格容器已是 `overflow-auto` + sticky 表头，沿用原生滚动；不引入分页或虚拟列表（超出本期范围）。
- **[权衡] 计数按「涉及个股」而非「隐藏配对」** → 更贴合用户「个股」表述，但混合场景下计数会小于实际新增行数。已在决策 5 中固定标签措辞，避免误导。
- **[权衡] 不持久化开关状态** → 用户每次进入页面都要重新点开；换来的是口径永远由当前 `as_of` 与默认态决定，不会出现「上次看的是旧口径」的误解。

## Migration Plan

1. 纯前端改动：新增 `src/lib/lhbExitVisibility.ts`、改 `src/pages/LhbTraders.tsx`、新增 `tests/lhbExitVisibility.test.ts`。执行 `npm test`（node:test）与 `npm run build`（`tsc && vite build`）；`dist/` 按项目既有约定随提交入库。
2. 文案改动：`pipeline/lhb_traders.py` 注释/docstring/`scope_note` 生成串；对 `data/lhb-traders.json` 做定点字符串替换并通过 deep-equal + schema 校验。
3. **无数据迁移、无 API 迁移、无调度改动**：`server/lhb.py`、`scheduler.py`、`schemas.py` 均不改；产物数值不变，不需要回补历史。
4. **回滚**：还原上述前端源码与文案文件即可，产物数据无需回滚（本变更从未改动其数值）。回滚后页面回到现行 3 交易日统一过滤，无残留状态（开关为页面内 state）。
5. **验收**：以当前产物 `as_of = 2026-11-13` 手工核对——`exit_date = 2026-11-06` 的配对在游资总览与个股游资总览默认可见，`2026-11-05` 的默认不可见且点击任一按钮后两个 Tab 同时可见；【每日动向】在开关前后每个日期的行集合完全一致（基线 355 行），且无按钮。
