# Proposal

## Why

`signal-hub-web` 目前全站使用 `src/data/mock.ts` 的静态假数据（信号类型 `buy`/`sell`、固定涨跌幅、固定胜率），与 `D:\信号指标` 已跑通的确认型信号口径完全脱节：真实信号的类型是 `top`/`bottom`，准确率必须带样本量与统计区间，且数据只存在于本机绝对路径下。这带来两个后果：页面展示的数值不可信；项目无法上传 GitHub 后在其他电脑运行。

需要一条**自包含、可移植、接近实时**的数据链路：抓取 → 信号计算 → 服务端分发 → 前端 30 秒刷新，且所有现有页面与后续新增页面统一从同一数据源取数。

## What Changes

- **流水线迁入本项目**：将 `D:\信号指标\pipeline` 中的抓取与信号计算模块迁入本项目 `pipeline/`，移除全部 `D:/信号指标` 绝对路径，改为相对项目根目录；废弃硬编码路径的 `build_production_data.py`，统一走 `build_dashboard.py` 的构建逻辑。
- **新增常驻服务**：新增 `server/`（Python + FastAPI），负责定时刷新与只读 API：
  - 盘中（交易日 09:30–11:30、13:00–15:00）每 30 秒拉取实时行情快照，重算盘中信号；
  - 收盘后重算日线并原子落盘 `data/index-signals.json`、`data/sector-trends.json`；
  - 首次运行自动补齐缺失的原始日线（`data/raw/` 不入库，运行时拉取）。
- **新增只读 API**：`/api/snapshot` 统一返回指数、板块、最近信号、候选信号、`as_of`、`mode`（`live` / `close`）、`generated_at`；另提供 `/api/health` 与单指数历史查询。
- **新增候选与三层确认状态导出**：以只读方式导出"候选信号 + 价格/动量/量能三层是否通过"，供页面展示"待确认候选"与"确认层数"，**不改变已确认信号的结果**。
- **前端接入真实数据**：以统一数据层（类型定义 + 轮询 Hook + Context）替换 `mock.ts`，Dashboard、宽基指数页及后续所有新页面从同一数据源取数，默认 30 秒轮询，页面不可见时暂停轮询。
- **BREAKING**：前端信号类型由 `buy`/`sell` 改为真实口径 `bottom`/`top`/`none`；页面内所有写死的数值（点数、涨跌幅、胜率、确认层数）改为数据驱动；`/indices` 页的 6 个 mock 指数改为真实 7 大宽基指数。
- **盘中态与收盘态严格分离**：盘中信号标记为 `provisional`，仅存在于服务端内存与 API 响应中，**不写入历史 JSON、不参与准确率统计**；收盘后由真实日线重算并落盘，作为唯一正式信号。

## Capabilities

### New Capabilities

- `market-data-pipeline`：自包含、可移植的行情抓取与确认型信号计算流水线，涵盖原始数据补齐、日线信号构建、盘中快照重算、数据校验与原子落盘。
- `live-data-api`：常驻服务的刷新调度与只读 API 契约，涵盖 30 秒盘中轮询、收盘重算、缓存、健康检查与降级行为。
- `live-data-ui`：前端统一数据层与页面接入契约，涵盖轮询策略、盘中/收盘态标识、加载与失败降级、以及"所有页面含新增页面必须走同一数据源"的约束。

### Modified Capabilities

无。`openspec/specs/` 当前为空，`openspec/changes/signal-dashboard-ui-spec.md` 为设计文档而非已归档能力，本次不改动其已定义的颜色/字体/布局规范。

## Impact

**受影响代码**

- 新增 `pipeline/`：`fetch_market.py`、`research_algorithms.py`、`multifactor_research.py`、`confirmed_index_signals.py`、`confirmed_sector_trends.py`、`build_dashboard.py`、`io_utils.py`、`schemas.py`（迁入并去绝对路径）。
- 新增 `server/`：应用入口、调度器、快照组装、API 路由、实时行情适配层。
- 修改 `signal-hub-web/src/`：`data/` 类型与客户端、新增 `hooks/useMarketData`、`App.tsx` 注入 Provider、`pages/Dashboard.tsx`、`pages/Indices.tsx`、`components/TopBar.tsx`（显示 `as_of` 与 `mode`）；`pages/MaCross.tsx` 等指标详情页改为从数据层取数或明确标注"指标明细待接入"。
- 修改 `signal-hub-web/vite.config.ts`：开发环境 `/api` 代理到本地服务。
- 新增根级 `.gitignore` 条目（`data/raw/`、`__pycache__/`、`.venv/`）、`requirements.txt`、启动脚本。

**依赖**

- 新增 Python 依赖：`fastapi`、`uvicorn`、`akshare`、`pandas`、`requests`、`jsonschema`。
- 前端不新增运行时依赖（沿用 React 19 + Vite 7）。

**数据与口径影响（对应 AGENTS.md 第 4、5 条，需在评审时确认）**

1. **盘中信号会重绘（repaint）**：盘中以实时价充当当日未完成 K 线的收盘价，同一算法重算会随价格波动反复出现/消失。因此盘中信号仅作 `provisional` 展示，不落库、不计入准确率分母，收盘后以日线重算结果为准。
2. **盘中量能不可比**：盘中成交量是当日累计的部分量，与历史完整日成交量不同量级；量能确认层在盘中天然偏保守。该偏差只影响盘中临时态，不影响收盘信号。
3. **候选层状态为只读扩展**：新增"候选 + 三层通过情况"的导出，不修改 `confirmed_multifactor_signals` 的输出；验收要求改造前后已确认信号的日期、类型、数量完全一致。
4. **数据口径不变**：指数仍为同花顺公开日线（前复权口径沿用现状），板块仍为 akshare 申万日线；实时快照仅用于盘中临时态，不参与历史。

**可移植性影响**

- 原始日线 `data/raw/`（约 45MB）不入库，首次启动自动拉取；`data/index-signals.json` 与 `data/sector-trends.json`（约 950KB）随仓库提交，作为离线首屏兜底。
- 服务与脚本需在 Windows / macOS / Linux 下可运行，不依赖任何本机绝对路径。
