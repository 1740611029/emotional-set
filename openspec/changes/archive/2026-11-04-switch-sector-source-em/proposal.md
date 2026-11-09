# Proposal

## Why

申万板块接口（akshare `sw_index_first_info` / `sw_index_second_info` / `index_hist_sw`）是爬虫聚合源，稳定性差：2026-11-03 本机实测单板块全量日线 14.1 秒且尾日只到 T-1（2026-11-02）、接口不支持按日期过滤导致增量只能全量回拉 162 个板块；申万官网（swsresearch.com）本机直连 TLS 握手失败，上游无稳定官方通道。每日补齐与启动阻塞经常卡在板块源上。需要换到更稳的板块数据源。

同日候选源实测：东财行业板块日线 0.3–0.5 秒/板块且当日收盘即定稿（2026-11-03 复测，比申万快约 30 倍）、支持 `start_date/end_date` 增量；同花顺行业板块 0.4 秒/请求但尾日同样 T-1；中证官网近即时但行业分类体系完全不同。用户已确认：**整体换到东财行业板块，板块结构改为单层行业**。

单层清单来源已实测确定：东财行业板块共 496 个且接口**不携带任何层级字段**（`f199` 全为 `-`，带 `Ⅱ`/`Ⅲ` 后缀的仅 82 个，其余 414 个不带后缀的仍含三级细分），因此不存在可直接拉取的「东财一级行业」名单。改用申万一级 31 个行业名称去东财清单映射：**31/31 全部精确命中**（农林牧渔→BK0433、电子→BK1201、有色金属→BK0478 等），历史可回溯至 2000-01-04（部分板块 2010 年起）。用户已确认清单方式为**硬编码 31 组 BK 代码 + 每次名称校验**。

## What Changes

- 板块数据源从 akshare 申万接口（`sw_index_first_info`/`sw_index_second_info` + `index_hist_sw`）整体换为东财行业板块（`stock_board_industry_name_em` 板块清单 + `stock_board_industry_hist_em` 日 K，不复权）。
- **BREAKING** 板块清单从「申万一级 31 个 + 二级 124 个」两层树改为**单层 31 个东财行业板块**（BK 代码硬编码，数量与现有 31 个一级一一对应）：`level`/`parent`/`subsectors`/`subsector_evaluation` 等层级结构移除。
- **BREAKING** 板块口径变更 = 换了一套板块信号历史：全部板块日线与四态/强度历史按东财数据重取重算。四态判定算法 `sector-donchian20-ma60-v1.0` 与统一有向强度公式**保持不变**，只换输入数据；已有申万↔东财调研显示两源状态不一致率 24.7%（3695/14973 行业日）、方向相反 4.0%，因此必须：
  - 旧申万原始 CSV 与旧 `sector-trends.json` 产物保留为对照基线（算法变更留痕）；
  - 输出新旧口径对照报告，含样本量与统计区间（板块数、可比交易日、四态分布差异）；
  - `algorithm_version` 升级并在产物中标注数据源。
- **BREAKING** 板块换源同时改变**指数信号的行业宽度输入**：`build_dashboard.load_breadth()` 读取板块一级日线计算横截面宽度（站上 MA20/MA60 行业占比），宽度是指数六维评分的一维并参与三层确认的量能层。因此 7 大指数的顶底信号历史、候选信号与准确率统计都会随板块源切换而重算。用户已确认：**breadth 一起换东财**，并接受指数信号历史整套重算；重验必须遵守调参与验证分离（规则 7）与准确率带样本量（规则 8）。
- 增量补齐改为按日期段：东财接口支持 `start_date/end_date`，按本地尾日增量拉取，替代申万「不支持按日期过滤、只能全量回拉」的方式。
- 收盘定稿约束固化为需求：东财盘中即返回未完成 K 线，当日 bar 只允许在收盘后（15:00 分界，沿用交易日历「最近已完成交易日」判定）采集，MUST NOT 把盘中 bar 当已收盘数据。
- 历史深度不均的板块沿用现有机制：有效历史不足 60 个交易日的板块标记 `insufficient_history`、状态置空，不丢弃。
- 前端板块趋势页改为单层行业列表：移除二级弹窗、父子导航、父级状态差异提示、二级独立评估披露；四态标签、有向强度语义、走势图（收盘+MA60+状态带）、筛选/排序/检索、加载失败与陈旧降级全部保留。

## Capabilities

### New Capabilities

（无）

### Modified Capabilities

- `market-data-pipeline`: 「数据源与口径固定」中申万板块日线改为东财行业板块日线；板块原始数据补齐、清单结构（一级/二级→单层）、增量按日期段、收盘定稿约束、板块趋势产物契约（移除一级/二级分组与二级独立判定/评估要求）、新旧口径迁移留痕要求变更。
- `sector-trend-explorer`: 板块层级组织与导航（一级/二级树）改为单层行业列表；移除二级详情弹窗、父级差异提示、二级独立状态评估披露三个需求的适用性；其余（四态语义、强度语义、图表、筛选排序检索、降级）保持。
- `live-data-api`: 快照板块条目字段（移除层级/父级/二级列表）、板块评估（移除二级部分）、板块历史查询（移除二级查询场景）改为单层结构。
- `index-signal-engine`: 「横截面宽度」的输入行业日线由申万一级改为东财行业板块（31 个单层清单），并新增宽度换源后指数信号同批重建与新旧对照重验的要求。判定阈值、六维权重、三层确认机制本身不变。

## Impact

- **流水线**：`pipeline/fetch_market.py`（`sector_list`/`fetch_sectors`）、`pipeline/bootstrap.py`（`update_sectors` 增量逻辑）、`pipeline/build_dashboard.py`（`build_sectors` 的 31 个一级硬校验、`pack` 字段、`subsectors` 组装）、`pipeline/confirmed_sector_trends.py`（`build_subsector_evaluation`、二级父级校验）。
- **数据**：`data/raw/sectors/`（申万 CSV）与 `data/raw/sectors-manifest.json` 保留为对照基线，不再更新；新增东财板块生产目录与 manifest（复用现有调研缓存 `data/raw/sectors-em/` 的同名日线，需统一列名与覆盖范围）；`data/sector-trends.json` 结构变化。
- **指数信号链路（受牵连）**：`pipeline/build_dashboard.py` 的 `load_breadth()`（现读 `data/raw/sectors/first/*.csv`）、`pipeline/swing_indicators.breadth_table()`（算法不动，仅换输入）、`pipeline/factor_survey.py`（同源读取，需同步）；产物 `data/index-signals.json` 的信号历史、候选与准确率全部重算。
- **服务端**：`server/app.py` 板块接口含二级遍历与父子定位（`subsectors` 遍历、`_find_sector`、板块历史接口的 `level`/`parent` 字段），需改为单层查询；`server/test_server.py` 中 `subsector_evaluation`、`subsectors`、`level/parent` 断言相应重写。
- **前端**：`signal-hub-web/src/data/types.ts`、`pages/Sectors.tsx`、`lib/sectorNavigation.ts`、`lib/sectorStates.ts`、`data/useSectorHistory.ts`、`components/SectorTrendChart.tsx` 等；`tests/sectorNavigation.test.ts`、`tests/sectorStates.test.ts` 重写。
- **既有 change**：`openspec/changes/center-sector-detail-dialog`（二级弹窗居中/详情列）依赖将被移除的二级弹窗。用户已决定：**先放着不动**，本 change 不归档、不废止、不修改它。
- **不受影响**：7 大指数自身的同花顺日线采集、机构持仓链路、辅助口径数据、四态判定算法与强度公式本体、三层确认机制的判定规则。
- **调研脚本**：`pipeline/sector_source_survey.py` 基于申万一级清单，换源后其输入 manifest 结构失效，需标注过时或同步改造（见 tasks）。
