# SignalHub · 股市顶部/底部信号可视化

基于**确认型信号**（候选 → 1-3 个交易日 → 价格/动量/量能三层确认）的指数信号站点。
前端为 React + Vite，后端为 Python + FastAPI，信号计算复用经过回归验证的确认型多因子算法。

## 快速开始

```bash
# 1. 安装依赖
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate
pip install -r requirements.txt

# 2. 启动（自动构建前端、按需拉取原始行情）
python start.py
```

打开 <http://127.0.0.1:8000>。也可用 `启动.bat`（Windows）或 `start.sh`（macOS/Linux）。

环境变量：`HOST`（默认 `127.0.0.1`）、`PORT`（默认 `8000`）、`REFRESH_SECONDS`（默认 `30`）。

## 什么时候会重建信号

**启动时会先看数据有没有更新。** 如果本地日线还没覆盖最近已完成交易日，服务会**先补齐、补齐之后才出首屏数据**；
补齐失败（无网络、或数据源当天尚未发布）不阻断启动，会用现有数据照常启动，**下一次启动或当晚的补齐时刻会再试**。

| 触发 | 时机 | 是否拉行情 | 是否重算信号 | 是否写盘 |
| --- | --- | --- | --- | --- |
| 启动 | 每次运行 | 仅落后于最近交易日时（阻塞补齐） | 补齐后重算；`data/*.json` 缺失时也会构建 | 是 |
| 收盘自动 | 交易日 15:00 后 | 是（增量拉日线） | 是 | 是 |
| 晚间补齐 | 交易日 17:30 / 20:00 / 22:00 | 仅当收盘后仍落后时 | 是（补齐后重算） | 是 |
| 盘中 | 交易时段每 30 秒 | 否（读实时快照） | 是（内存合成当日 K 线） | 否 |
| 手动 | 随时 | 可选（见下） | 是 | 是 |

> 收盘/晚间补齐只有在**真的覆盖到最近交易日**时才把当天记为完成；没补到就留给下一个时刻或下次启动。
> 补齐进度可在 `GET /api/health` 的 `stage`/`detail`/`backfill` 字段查看。

### 手动重建

```bat
重建信号数据.bat              :: Windows，交互选择：回车=仅重建
重建信号数据.bat fetch       :: 先补落后的日线，再重建
重建信号数据.bat fetch --force :: 忽略“当日已补齐”标记，强制再拉一次
```

```bash
./rebuild.sh                   # macOS / Linux，仅重建
./rebuild.sh fetch             # 先补数据再重建
```

仅重建等价于 `.venv\Scripts\python.exe pipeline\build_dashboard.py`；
先补数据再重建等价于 `.venv\Scripts\python.exe pipeline\refresh_and_rebuild.py`。

- 重建前会打印当前生效的算法（`ACTIVE_ALGORITHM`）与版本号；
- 校验失败时不会写盘，旧数据保持不变；
- **重建后无需重启服务**，调度器检测到文件变化后 30 秒内自动刷新快照；
- 重建是幂等的：数据未变化时产出字节级一致的文件。

只想校验不写盘：`.venv\Scripts\python.exe pipeline\build_dashboard.py --validate`。

### 开发模式

```bash
# 终端 1：数据服务
python -m server

# 终端 2：前端热更新
cd signal-hub-web && npm install && npm run dev   # http://localhost:5173
```

`vite.config.ts` 已把 `/api` 代理到 `http://127.0.0.1:8000`，可用 `API_TARGET` 覆盖。

## 数据与刷新机制

| 场景 | 数据源 | 发布时点 | 刷新 |
| --- | --- | --- | --- |
| 指数日线（历史 + 收盘） | 同花顺公开日线 | 当日 15:00 后陆续到位，部分指数要晚间才聚合完整 | 启动/收盘后补齐 |
| 板块日线（31 个东财行业板块，不复权） | akshare 东财行业板块接口 | 收盘后即发布，15:01 那轮通常已含当日 bar | 启动/收盘后增量（起点含本地尾日） |
| 盘中指数快照 | akshare 新浪指数快照（备源：东方财富） | 实时 | 交易时段每 30 秒 |
| 机构多空单（前 20 会员持仓排名） | akshare 中金所持仓排名 | 当日晚间披露 | 启动/收盘后增量更新 |

实测（本地文件时间戳）：东财行业板块收盘后即发布，同花顺指数日线往往晚间才补齐，
因此**同一批重建里板块 `as_of` 允许领先指数 `as_of` 至多 1 个交易日**——指数 `as_of` 取七个指数尾日的
最早值，板块 `as_of` 取 31 个板块尾日的最晚值，属两种数据源发布节奏差异，不是计算缺陷。
两份产物与界面各自显式标注自身时点（板块页的「数据截至」与滞后提示即为此而设），
领先超过 1 个交易日按发布节奏异常核查后再发布。

板块日线已于 2026-11 由申万行业指数**换源为东财行业板块**，并把「一级 + 二级」两层
改为**单层 31 个行业**（换源前的调研结论「不换源」已被这次改动取代）。换源前后四态一致率：
研究段 **70.59%**（样本 30,140 行业日，2020-10-30～2024-12-31）、验证段 **75.46%**
（样本 13,702 行业日，2025-01-02～2026-11-03），其中 bull↔bear 方向相反 5.20%/4.25%，
详见 `data/research/sector-source-migration-sw-vs-em.md`；指数侧横截面宽度同步换源，
顶底信号由 73 条变为 81 条（日期一致 45），对照见 `data/research/breadth-source-migration-sw-vs-em.md`。

**盘中态与收盘态严格隔离**：

- 盘中以实时行情合成「当日未完成 K 线」，复用同一套确认算法，结果标记 `provisional`，
  只存在于内存与 API 响应，**不写入历史、不计入准确率分母**；
- 收盘后由真实日线重算并原子落盘，作为唯一正式信号；
- 盘中信号会随价格波动重绘，界面统一标注「盘中临时」。

## 目录结构

```
pipeline/            信号流水线（扁平 import，保持与原研究项目一致）
  fetch_market.py      日线抓取（同花顺指数 / 东财行业板块）
  trading_calendar.py  交易日历与“最近已完成交易日”判定
  research_algorithms.py, multifactor_research.py   指标与因子
  confirmed_index_signals.py, confirmed_sector_trends.py  确认型信号
  candidate_state.py   候选与三层确认状态（只读旁路）
  institution_positions.py  机构多空单（中金所前 20 会员持仓排名，事后统计）
  build_dashboard.py   组装前端 JSON
  io_utils.py, schemas.py  校验与原子写入
  bootstrap.py         原始数据补齐与增量刷新（含新鲜度判定）
  refresh_and_rebuild.py 先补落后的日线再重建 JSON（供「重建信号数据.bat fetch」）
  sector_source_survey.py 板块数据源换源影响调研（只出报告，不改口径）
  sector_source_migration.py, breadth_source_migration.py  换源前后四态/宽度/信号对照（只写 data/research/）
  compare_snapshot.py  信号事实回归比对
server/              数据服务（快照、调度、API）
data/                落盘数据（raw/ 不入库，首次运行自动拉取）
signal-hub-web/      前端（React + Vite + Tailwind）
```

## 接口

| 路径 | 说明 |
| --- | --- |
| `GET /api/snapshot` | 统一快照：`mode`/`as_of`/`generated_at`/`stale`/`indices`/`sectors`/`recent_signals`/`candidates` |
| `GET /api/health` | 存活、最近刷新时间、模式、数据源、算法版本 |
| `GET /api/indices/{code}/history` | 单指数历史与信号 |
| `GET /api/institutions/positions` | 机构多空单：`institution`（`all`/`guotai`/`citic`/`others`）、`variety`（`all`/`IF`/`IC`/`IH`/`IM`）、`from`/`to`、`limit`（默认 60） |
| `GET /api/sectors/{code}/history?days=60\|120\|250` | 单板块历史序列（31 个东财行业板块之一，凭 code 定位），默认 120 |

`data_state` 取值：`ready` / `preparing`（首次拉取数据中，返回 202）/ `degraded`（数据源异常，`stale=true`）。

## 测试

```bash
python -m pytest pipeline server -q
```

- `pipeline/`：信号算法、候选状态、校验与原子写入（含从原项目迁移的回归用例）
- `server/`：快照契约、盘中合成、实时源归一化、接口状态

回归比对（信号事实必须逐条一致）：

```bash
python pipeline/compare_snapshot.py --baseline <旧 index-signals.json> --current data/index-signals.json
```

## 板块趋势口径（/sectors）

- **清单与数据源**：31 个东财行业板块，`fetch_market.EM_SECTOR_UNIVERSE` 硬编码 BK 代码 + 每日名称校验
  （名称对不上直接报错，不静默重新匹配）；**单层、无父子关系**，不复权日线，
  缓存 `data/raw/sectors-em-industry/{BKxxxx}.csv`；盘中与收盘同源
  （`server/paths.SECTOR_EM_RAW == fetch_market.EM_SECTOR_RAW`，已由单测固化）。
- **四态判据**（算法版本 `sector-donchian20-ma60-v2.0-em-source`，判定规则本体与 `v1.0` 一致，
  版本号升位只标识数据源与清单变更）：
  收盘价相对唐奇安 20 日通道中轨、相对 60 日均线两条判据组合成
  `bull`（多头，两者皆上）、`warn`（退潮，破中轨仍在 MA60 上）、
  `bear`（空头，两者皆下）、`rise`（反转，上穿中轨但仍在 MA60 下）。有效样本要求 60 个交易日。
- **每个板块用自身日线独立判定**，记录中无 `level`/`parent`/`subsectors`，也没有“与父级不一致”这类概念。
- **有向强度 -50 ~ +50**：`(区间位置分 + MA60 偏离分) / 2 - 50`，其中区间位置分为收盘价在近 20 日高低区间中的百分位，
  MA60 偏离分为 `50 + 50·tanh(偏离/10%)`。0 为中性，正值偏强、负值偏弱，
  四种状态使用同一公式，不按多空取反；通道宽度为零时固定为 0 并标记 `data_anomaly`。
  例如两项均值 80 对应强度 30，两项均值 20 对应强度 -30。
  改动前 0–100 且空头/退潮取反的旧产物已备份为 `data/sector-trends.pre-signed-strength.json`。
- **滞后与展示降级**（自换源前沿用，本次未收紧也未放宽）：尾日早于产物 `as_of` 的板块标记 `data_lag`，
  仍展示其状态；滞后超过 30 个自然日则清空 `state`/`strength`/`strength_parts`/`strength_basis`/`days`，
  宁可显示无状态也不用陈旧结论示人。该规则只作用于最新条目，不进入任何历史序列与信号计算。
- **准确率披露**：产物 `evaluation` 按研究段 / 验证段 × 10 / 20 日、**再按四态分组**给出方向胜率，
  每格同时给已判定样本、成功数、未决数与 Wilson 95% 区间。**四态样本之和恒等于拆分前的聚合样本**，
  需要总体口径时直接相加即可（研究段 10 日 5,075 / 20 日 5,049；验证段 10 日 2,184 / 20 日 2,120）。
  方向按状态期望：`bull` / `rise` 期望上涨，`warn` / `bear` 期望下跌；未决事件不计入分母。
  格内格式为「方向胜率（已判定样本，Wilson 95% 区间）」，数据照录生产 `data/sector-trends.json`
  （as_of 2026-11-04）：

  研究段（`research`，2020-10-30～2024-12-31）

  | 状态 | 10 日 | 20 日 |
  | --- | --- | --- |
  | `bull` 多头 | 52.14%（1,404，49.52%–54.74%） | 48.21%（1,398，45.60%–50.83%） |
  | `warn` 退潮 | 53.90%（1,128，50.98%–56.79%） | 54.33%（1,108，51.39%–57.25%） |
  | `bear` 空头 | 53.42%（1,359，50.76%–56.06%） | 53.42%（1,359，50.76%–56.06%） |
  | `rise` 反转 | 50.84%（1,184，48.00%–53.68%） | 53.04%（1,184，50.19%–55.87%） |

  验证段（`exploratory_validation`，2025-01-02 起）

  | 状态 | 10 日 | 20 日 |
  | --- | --- | --- |
  | `bull` 多头 | 57.75%（684，54.01%–61.40%） | 61.38%（668，57.63%–64.99%） |
  | `warn` 退潮 | 50.62%（565，46.51%–54.72%） | 51.29%（544，47.09%–55.46%） |
  | `bear` 空头 | 43.12%（494，38.82%–47.52%） | 39.32%（468，35.00%–43.81%） |
  | `rise` 反转 | 72.11%（441，67.75%–76.09%） | 74.32%（440，70.04%–78.18%） |

  产物自带 `research_status=post_hoc_exploratory_not_independent`：**验证段为事后观察、不构成独立验证**；
  说明文字为「板块趋势为描述性分类，方向胜率不代表交易收益；空头状态在验证段胜率低于 50%、
  研究段约 53%，仅供参考」——**仅作描述性分类，不代表交易收益**。

重新生成板块产物：

```bash
python -c "import sys; sys.path.insert(0,'pipeline'); from build_dashboard import build_sectors; from io_utils import protected_write; protected_write('sector-trends.json', build_sectors(), compact_arrays=True)"
```

板块回归比对（`compare_snapshot.py` 不带参数时现场重建并与两份生产产物逐板块/逐指数对账，
板块只比 `state`/`strength`/`days`/分组；`--baseline-sectors` 指向不存在的文件则跳过板块部分）：

```bash
python pipeline/compare_snapshot.py
```

换源前的申万两层基线 `data/research/sector-trends-baseline.json`（`v1.0`，as_of 2026-10-30）
仅作换源前留痕保留，不再参与比对（旧新可比性已由 `pipeline/sector_source_migration.py` 的对照报告接管）。

## 信号算法（index-swing6-v2.0）

生产信号由 `pipeline/swing_signal_engine.py` 生成，`confirmed_index_signals.ACTIVE_ALGORITHM` 控制生效算法（`swing` / `legacy`）。

- **六个正交方向**：价格位置、动量背离、量能极值、波动状态、趋势状态、横截面宽度（31 个东财行业板块站上 MA20/MA60 的占比及 5 日变化）；每个方向独立打分，等权合成。
- **两道闸门**：核心三项（位置 / 宽度 / 量能）至少两项达到极端；趋势处于"加速恶化"时否决底部候选（顶部对称）。
- **波段交替**：`bottom` 与 `top` 严格交替，一个波段只发一个转折信号；同向候选在出现反向信号前一律丢弃，并要求最小波段间隔与振幅。
- **三层确认**：价格（突破候选日高低点）→ 动量（K 线方向 + RSI 转向，顶部额外要求 MA5 掉头）→ 量能（放量或宽度改善 ≥5pp），窗口 1–3 个交易日，超时作废；信号日期记为确认日。
- **分级**：A 级要求核心三项全部极端且分数不低于本指数信号中位数，其余为 B 级。

### 回撤口径

- 每个信号以信号日收盘价为基准，输出 `max_adverse_excursion`（至确认日的极值回撤）、`max_adverse_excursion_20`、`max_drawdown_20` 与 5/10/20 日前瞻收益；
- 汇总层输出平均/最差最大回撤、平均 20 日收益与收益回撤比；
- 回撤**不参与胜率计算**，修改回撤窗口不改变任何胜率数值；
- 所有页面凡展示胜率或收益，必须同屏展示回撤；无样本时显示 `--`，不显示 0。

### 研究口径

- 训练段 `2020-10-30 ~ 2024-12-31` 选参，验证段 `2025-01-01` 起仅作报告，隔离期 20 个交易日；
- 选参指标为底部/顶部 Wilson 下界取小，已判定样本 <20 淘汰；一致性要求 ≥4/7 个指数的 20 日前瞻收益高于同期全样本均值；
- 冻结参数与淘汰记录见 `data/research/swing-factors-index-swing6-v2.0.json`，新旧对照见 `data/research/swing-vs-legacy-index-swing6-v2.0.json`；
- 研究脚本只写 `data/research/`，不触碰生产 JSON。

## 数据口径与约束

- 复权方式与数据源已固定，变更等于更换一套信号历史，需单独评审（2026-11 那次板块换源即走了单独评审与
  新旧对照留痕，口径为**东财行业板块日线、不复权**）；
- 信号只在三层确认全部通过后产生，未确认候选不计入准确率；
- 页面展示准确率时必须同时给出已判定样本数与统计区间，并并列展示回撤；
- 原始日线 `data/raw/` 不入库，首次启动自动拉取：生产实际使用约 23MB
  （中金所 10.9MB + 东财行业板块 9.1MB + 指数等）；另有约 41MB 的旧申万板块缓存
  仅作为换源前留痕保留，已不参与任何计算（删除后重建结果逐字节一致，已验证）；
- `data/*.json` 随仓库分发，作为离线首屏兜底。

### 机构多空单口径

- 数据为中金所每日**前 20 会员**持仓排名（akshare `get_cffex_rank_table`），非全市场持仓；多单/空单均为**单边持仓量**，单位为**手**。
- 会员名按 `MEMBER_ALIASES` **精确匹配**归一（同时剥离 `(代客)`/`(自营)` 后缀）；`中信建投` 与 `中信期货` 是两个主体，不得合并。
- 四品种汇总为 IF/IC/IH/IM 手数直接相加，**未按合约乘数折算**（IF/IH 300 元/点、IC/IM 200 元/点），不可用于跨品种规模比较。
- 同向/对冲为**事后统计**：当日净持仓变化方向与**次日**沪深300 涨跌方向相同为同向、相反为对冲；次日数据未产生时为"待判定"，不计入同向率分母。该字段不参与任何信号计算。
- 重建命令（首次全量约 5–10 分钟，已缓存日期不重复抓取）：

```bash
python pipeline/institution_positions.py            # 增量
python pipeline/institution_positions.py --limit 60  # 仅最近 60 个交易日（调试）
python pipeline/institution_positions.py --force     # 忽略缓存重抓
```

原始明细缓存于 `data/raw/cffex/{YYYY-MM-DD}.csv`（不入库）。

## 常见问题

**启动时报 `SyntaxError: Non-ASCII character ... but no encoding declared`？**
说明执行脚本的 `python` 是 Python 2（本项目只支持 3.10+）。`启动.bat` 会自动挑选
`.venv` → `py -3` → `python3` → `python`（校验版本），正常不会触发。
若你手动执行 `python start.py`，请改用 `py -3 start.py`，或先激活虚拟环境。

**首次启动很慢？** 需拉取 7 个指数 + 31 个行业板块的日线，逐标的串行请求，首轮耗时主要取决于网络；页面会显示「数据准备中」。
**启动后页面长时间「数据准备中」？** 启动会先把落后的日线补完才出首屏：
可看 `GET /api/health` 的 `stage`/`detail` 确认在拉哪一类；补齐报错也会继续启动，`last_error` 保留原因。

**页面显示「数据陈旧」？** 实时源不可用，服务已回退到最近一次成功数据，恢复后自动切回。

**端口被占用？** `PORT=8080 python start.py`。

**只看到收盘数据？** 非交易时段（含周末与节假日）不会请求实时行情，属预期行为。
