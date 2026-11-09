# Design

## Context

现状与约束（动机见 `proposal.md`，行为契约见 `specs/`）：

- `signal-hub-web` 是 React 19 + Vite 7 + TypeScript + Tailwind 4 的纯静态 SPA，路由用 `react-router-dom`，全部数据来自 `src/data/mock.ts`（46 行，5 条信号 + 6 个指数卡片）。
- `D:\信号指标` 是 Python 数据项目：`pipeline/` 内 8 个模块构成完整链路（`fetch_market.py` 抓同花顺日线 / akshare 申万 → `research_algorithms.py` + `multifactor_research.py` 指标与因子 → `confirmed_index_signals.py` / `confirmed_sector_trends.py` 确认型信号 → `build_dashboard.py` 组装 JSON → `io_utils.py` 校验原子写）。算法版本 `index-mf4-participation-v1.1-confirmed`。
- 信号算法天然具备"候选 → 3 个交易日内经价格/动量/量能三层确认"的结构（`_candidate_signals` → `_check_confirmation` → `confirmed_multifactor_signals`），与前端已有的"确认层数 0–3"UI 语义正好对应，只是当前没有导出。
- `build_production_data.py` 硬编码 `D:/信号指标/...`，且只输出 10 条最近信号，与 `build_dashboard.py` 逻辑重复，属于必须废弃的遗留路径。
- 数据体量：`data/raw` 45MB（7 指数 × 约 1450 行 + 162 个板块 CSV），`index-signals.json` 928KB，`sector-trends.json` 28KB。
- AGENTS.md 第 4、5、9 条要求：影响信号逻辑须先报告、数据口径固定、算法变更留痕。

## Goals / Non-Goals

**Goals:**

- 让站点数据与 `D:\信号指标` 口径完全一致，且不依赖任何本机绝对路径。
- 盘中以 30 秒粒度提供实时价与盘中临时信号；收盘后以真实日线产出正式信号。
- 建立一套前端唯一数据入口，新增页面零成本接入。
- 在无网络、无数据源的机器上仍能启动并展示随仓库分发的最近一次数据。

**Non-Goals:**

- 不修改信号算法、因子阈值、确认窗口与评价口径（准确率第一）。
- 不实现分指标（均线/布林/量能/回撤等）的明细计算——现有数据不包含，指标详情页只做"壳层真实数据 + 明细待接入"。
- 不引入数据库、用户体系、鉴权。
- 不做 WebSocket/SSE 推送；30 秒轮询已满足需求，实现与运维成本更低。
- 不改造 UI 视觉规范（沿用 `openspec/changes/signal-dashboard-ui-spec.md` 的配色与字体）。

## Decisions

### D1. 后端采用 Python + FastAPI，而非 Node 重写

**决策**：新增 `server/`（FastAPI + uvicorn），直接复用迁入的 `pipeline/` 模块。

**理由**：信号算法基于 pandas/numpy 的滚动窗口与向量化实现，重写为 JS 必然引入数值差异，直接违背"准确率第一"和"指标结果可对照验证"。Python 复用可保证同一份代码产出同一份信号。

**备选**：Node 端重写全部指标 → 数值不可对照、回归成本极高，否决。纯静态 JSON + 外部定时任务 → 无法做到盘中 30 秒重算，且仍需一个进程，否决。

### D2. 盘中信号 = 在日线序列尾部合成"当日未完成 K 线"后复用同一算法

**决策**：实时行情提供当日 `open/high/low/close(最新价)/volume(累计量)`，构造一行合成 bar：

- 若日线最后一行的日期已是今天（收盘后重算过），则**替换**末行而非追加，避免重复日期触发 `validate_history` 的唯一性校验；
- 否则追加一行；
- 用同一 `confirmed_signals()` 计算，结果标记 `provisional: true`，只存在于内存快照。

**理由**：复用同一算法可保证盘中与收盘口径一致，不产生"两套逻辑"。用实时价充当未完成 bar 的收盘价不构成未来函数（只用到已发生的价格），但会随价格波动重绘，因此必须限定为临时态。

**备选**：盘中只更新价格、不重算信号 → 不满足"盘中信号"诉求，否决。为盘中单独写一套轻量算法 → 两套逻辑必然漂移，否决。

**代价**：盘中成交量是当日累计部分量，与历史完整日成交量不同量级，量能确认层在盘中天然偏保守；该偏差仅影响临时态，不进入正式数据与准确率。

### D3. 正式信号与盘中信号物理隔离

**决策**：盘中快照只进内存与 API 响应；`data/index-signals.json`、`data/sector-trends.json` 仅由收盘后的日线重算写入。准确率只基于落盘数据计算。

**理由**：一旦盘中信号写入历史，准确率分母会被污染，且历史不可复现（同一天不同时刻跑出不同结果）。物理隔离是最省心的保证方式。

### D4. 候选层状态作为只读旁路导出

**决策**：新增 `pipeline/candidate_state.py`，复用 `multifactor_indicators()` 与 `_candidate_signals()`（迁入时以公开别名 `candidate_signals` 暴露），对最近 3 个交易日内的候选逐层判定价格/动量/量能是否通过，输出 `layers: {price, momentum, volume}` 与 `passed: 0-3`。不修改 `confirmed_multifactor_signals` 的任何一行。

**理由**：UI 已有"确认层数"位置，旁路导出可零风险补齐；直接改确认函数会改变信号结果，违反 AGENTS.md 第 4 条。

**验收**：改造前后 `signals` 数组逐条比对（日期、类型、价格）必须完全一致，作为独立回归用例。

### D5. 数据源分层：日线为主，实时快照为辅

**决策**：

| 用途 | 数据源 | 频率 |
|---|---|---|
| 指数日线（历史+收盘） | 同花顺 `d.10jqka.com.cn` 公开日线（沿用现状） | 每日一次 |
| 板块日线 | akshare 申万行业 | 每日一次 |
| 盘中指数快照 | akshare `stock_zh_index_spot_sina`（代码形如 `sh000001`，与现有 code 对齐） | 30 秒，单次批量取全部 7 个指数 |
| 盘中快照备用源 | akshare `stock_zh_index_spot_em` | 主源失败时 |

**理由**：Sina 批量接口一次返回全部指数，30 秒 × 1 次请求即可，避免 7 次/轮的限频风险；其字段包含今开/最高/最低/最新价/成交量，可直接构造合成 bar。板块实时接口稳定性不确定，本次板块仍走日线，30 秒轮询只更新 `as_of` 展示，不做盘中板块信号。

**备选**：直连同花顺实时接口 → 无公开稳定契约，弃用。

### D6. 单进程 + 内存快照 + 原子落盘

**决策**：服务内维护一份 `Snapshot`（含 `mode`、`as_of`、`generated_at`、`stale`），由后台 asyncio 循环每 30 秒（仅交易时段）刷新；请求直接读内存快照，永不触发同步计算。落盘复用现有 `protected_write()`（临时文件 + `os.replace`）。

**理由**：并发请求不会引发重复计算或文件竞争；读路径恒为 O(1)。复用已有原子写可保持"校验失败保留上一版"的既有保障。

### D7. 交易时段判定用时间窗 + 实时源回执双重校验

**决策**：先按 `Asia/Shanghai` 判断是否落在 09:30–11:30 / 13:00–15:00 且为周一至周五；再要求实时源返回的最新交易日等于当日，否则视为非交易时段（覆盖节假日）。

**理由**：不引入额外的交易日历依赖（节假日表需要持续维护），用数据源自证更省事且不易过期。

### D8. 前端：单一 Context + 30 秒轮询 Hook，页面只消费

**决策**：

- `src/data/types.ts`：与 JSON schema 对齐的类型（`SignalType = 'bottom' | 'top' | 'none'`）。
- `src/data/client.ts`：`fetchSnapshot()`，带超时、`cache: 'no-store'`、结构校验与错误归一化。
- `src/data/MarketDataProvider.tsx`：Context + `useSyncExternalStore` 风格的状态（`data | error | isLoading | lastSuccessAt`），30 秒 `setInterval`，`visibilitychange` 暂停/恢复即刷，`AbortController` 取消在途请求，失败时保留上一次成功数据。
- 页面通过 `useMarketData()` 取数，不直接 `fetch`。

**理由**：避免每个页面各自轮询造成请求风暴；页面切换复用同一份快照，保证数值一致（对应 spec 的"页面数据一致性"）。

**备选**：SWR/React Query → 增加依赖，本项目只需一个端点，自研 40 行 Hook 足够。

### D9. 开发/生产两种运行形态

**决策**：

- 开发：`python -m server`（默认 8000）+ `npm run dev`（5173），`vite.config.ts` 增加 `server.proxy['/api'] → http://127.0.0.1:8000`。
- 生产：`npm run build` → FastAPI 挂载 `signal-hub-web/dist`，非 `/api` 路径回落到 `index.html`（SPA history 模式必需）。
- 一键启动：根目录 `start.py`（跨平台，检测依赖 → 必要时构建 → 启动服务并打印地址），配套 `.bat` / `.sh` 薄封装。

**理由**：满足"上传 GitHub 后在其他电脑运行"，且开发体验不变。

### D10. 仓库策略

**决策**：`.gitignore` 排除 `data/raw/`、`__pycache__/`、`.venv/`、`node_modules/`、`signal-hub-web/dist/`；提交 `data/index-signals.json`、`data/sector-trends.json` 作为离线首屏兜底。

**理由**：45MB 原始 CSV 不适合入库且可重建；两份 JSON 共约 950KB，入库换来"克隆即可看到真实数据"的体验，值得。

## Risks / Trade-offs

- **[盘中信号重绘]** 同一交易日内信号会反复出现/消失，用户可能误读为信号失效 → 前端强制"盘中临时"标识，盘中信号不进入"最近触发信号"历史表，仅出现在独立的盘中区。
- **[盘中量能不可比]** 累计量偏小使量能确认层几乎不通过，盘中信号偏少 → 在盘中区展示三层各自状态（而非只给结论），让用户看到"卡在量能层"。
- **[实时源反爬/限频]** Sina 接口可能限流或改结构 → 单轮仅 1 次批量请求；失败即降级到最近收盘并标 `stale`，连续失败不重试风暴（退避到 2 分钟）。
- **[首次拉取耗时]** 45MB 原始数据首次补齐约 1–2 分钟（162 个板块 × 0.3s 间隔）→ 后台任务执行，端点先返回"数据准备中"；已提交的 JSON 保证 UI 不至于空白。
- **[akshare 版本漂移]** 接口字段名变动会导致解析失败 → 固定依赖版本区间，解析处做字段存在性校验并给出明确错误，不静默补值。
- **[数值回归风险]** 迁入过程可能改变算法行为 → 迁移后必须做"新旧 JSON 逐条比对"回归（指数 latest/change_pct/signals/accuracy 全等），不一致即阻断。
- **[时区与夏令时]** 用户可能在非中国时区运行 → 统一用 `ZoneInfo("Asia/Shanghai")` 判定交易时段，不使用本机时区。

## Migration Plan

1. **迁入流水线**：复制 8 个模块到 `pipeline/`，移除 `D:/信号指标` 硬编码，删除 `build_production_data.py`（逻辑由 `build_dashboard.py` 覆盖）。
2. **回归基线**：用现有 `data/raw` 重新构建，与仓库中现有 `index-signals.json` / `sector-trends.json` 逐条比对，确认完全一致后再继续。
3. **新增服务**：`server/` 快照、调度、API；用 `curl /api/snapshot` 验证字段与 `mode`。
4. **前端数据层**：先落地 types/client/Provider，保留 `mock.ts` 供回退。
5. **页面改造**：Dashboard → Indices → 指标详情页壳层；每页改完即删对应 mock 引用。
6. **删除 mock**：全部页面接入后删除 `src/data/mock.ts`。
7. **打包与启动脚本**：验证 `npm run build` + 单进程托管；在干净目录模拟克隆后启动。

**回滚**：数据文件由 git 管理，可 `git checkout` 回退；前端改造分页提交，单页出问题只回退该页；`mock.ts` 保留至第 6 步，随时可切回。

## Open Questions

- 申万板块是否存在稳定的实时行情接口（`ak.index_realtime_sw` 等），若有可在后续变更中把板块也接入盘中态；本次不影响架构。
- 实时源在节假日与非交易时段的返回值形态（返回上一交易日数据 or 空），实现时以实测为准，两种都已在 D7 的双重校验下安全降级。
