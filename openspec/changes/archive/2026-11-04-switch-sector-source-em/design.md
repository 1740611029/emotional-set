# Design

## Context

- 现状：板块日线来自 akshare 申万接口（`sw_index_first_info`/`sw_index_second_info` + `index_hist_sw`），按 `data/raw/sectors/{first,second}/{code}.csv` 落盘，`sectors-manifest.json` 以 `level/parent` 组织 31+124 两层树；`build_dashboard.build_sectors` 硬校验一级 31 个；`server/app.py` 有二级遍历与父子定位；前端 `Sectors.tsx` 为一级列表 + 二级弹窗。动机与候选源实测数据见 proposal.md。
- 约束：换源属于口径变更（规则 5/9/11）：旧口径数据与产物必须保留可对照，判定算法本体不动；东财接口盘中返回未完成 K 线，必须只采收盘后定稿数据；项目无 git 仓库，回滚依赖 `.backup/` 目录惯例。
- 耦合点：板块日线不只服务于板块页，还经 `load_breadth()` 馈入指数信号的横截面宽度（六维之一 + 量能确认层），因此本变更同时影响指数顶底信号历史。

## Goals / Non-Goals

**Goals:**

- 板块日线采集切换到东财行业板块，增量按日期段拉取，不再全量回拉。
- 板块产物、服务端接口、前端页面全部改为单层结构，判定算法与强度公式不动。
- 旧申万口径完整留痕，产出带样本量的新旧对照报告。

**Non-Goals:**

- 不改指数（同花顺）日线采集链路、三层确认机制的判定规则、机构持仓链路。
- 不改四态判定算法 `sector-donchian20-ma60-v1.0` 的阈值与判据，不做参数调优，也不调指数信号的六维权重。
- **注意**：指数日线本身不动，但 `build_dashboard.load_breadth()` 用板块一级日线算横截面宽度，宽度是指数六维评分之一并参与量能确认，因此**指数顶底信号历史会随板块换源而重算**（用户已确认接受）。
- 不做备源自动降级（主源失败仍按现有"报错并保留旧产物"处理）。
- 不重写同花顺/中证等其他源的采集实现。

## Decisions

1. **接口与复权口径**：板块清单校验用 `ak.stock_board_industry_name_em`（仅取名核对，不取清单），日线用 `ak.stock_board_industry_hist_em`（`period="日k"`、`adjust=""` 不复权），列名重命名为既有 `date/open/close/high/low/volume/amount`。备选对比：同花顺行业（更快但尾日 T-1 且端点非公开契约）、中证官网（分类体系不同、全表接口解析失败）、申万官网（本机 TLS 失败）。东财是唯一同时满足「当日定稿 + 支持日期段增量」的候选。
2. **清单粒度（已定，推翻初稿假设）**：初稿假设「不带 `Ⅱ`/`Ⅲ` 后缀＝一级行业约 86 个」，2026-11-03 实测推翻：496 个板块里仅 82 个带后缀，剩下 414 个不带后缀的仍是三级细分（涂料、氨纶、生猪养殖…），且 push2 接口无任何层级/父级字段（`f199` 恒为 `-`）、数据中心估值接口是另一套 127 个混合层级行业。因此不存在可直取的「东财一级名单」。最终方案：**硬编码 31 组 BK 代码（与申万一级 31 个名称 31/31 精确映射）**，`stock_board_industry_name_em` 只用于每日名称校验，不用于动态定清单。备选：全量 496 个（三级细分会使宽度同一产业链重复计数、产物体积与 UI 不可接受）。
3. **数据目录与 manifest**：生产数据落 `data/raw/sectors-em-industry/{BKxxxx}.csv`（单层，按东财板块代码命名，改名不丢历史），manifest 为 `data/raw/sectors-em-industry-manifest.json`，结构为 `{source, universe, universe_snapshot, universe_changes, boards}`。**不复用** `data/raw/sectors-em/`：该目录是 `sector_source_survey.py` 的调研缓存（按板块名称命名的 31 个 CSV，仅覆盖调研窗口），与生产目录同名同级会让 `load_breadth()`/`build_sectors` 的 `glob("*.csv")` 重复计数且混入短窗口数据。旧 `data/raw/sectors/`、`sectors-manifest.json` 原位保留、只读不再更新。
4. **增量补齐**：`bootstrap.update_sectors` 改为逐板块读本地尾日，尾日 < 最近已完成交易日且未超停更阈值（沿用 60 交易日）时，调 `stock_board_industry_hist_em(start_date=尾日（含）, end_date=最近已完成交易日)` 增量合并；无缓存则 `start_date="20000101"` 全量。串行请求 + 现有 `call_with_retry` + 间隔 sleep。终点由既有 `last_completed_trading_day()`（15:00 分界）决定，天然满足收盘定稿约束。**起点含尾日**（2026-11-04 代码审查后修正，H-1）：每轮重取最后一根 bar，配合按日期去重 `keep="last"` 与逐值比对写盘，可覆盖掉收盘刚过时未定稿的量额；首次全量 `fetch_sectors()` 的默认终点也改为同一日历口径，避免盘中初始化把当日未完成 bar 写入缓存。
5. **产物与算法版本**：`confirmed_sector_trends` 移除二级父级校验与 `build_subsector_evaluation`；`build_dashboard.build_sectors` 移除 31 个一级硬校验、`subsectors` 组装与 `level/parent` 字段；`ALGORITHM_VERSION` 升级（如 `sector-donchian20-ma60-v2.0-em-source`），产物新增 `data_source: "东财行业板块"` 字段。四态与强度计算函数本体不动。
6. **服务端**：`app.py` 的 `_find_sector` 改为按 code 在扁平 `groups` 内查找，板块历史响应移除 `level/parent`；`sectors.evaluation` 保留，`subsector_evaluation` 键移除。
7. **前端**：`types.ts` 中 `SectorItem` 扁平化；`Sectors.tsx` 删除 `SubsectorDialog` 与二级列表，保留四态分组列表 + 单层详情；`sectorNavigation.ts` 移除 `sub` 参数与 `findSubsectorUnderFirst`；`sectorStates.ts` 四态分组逻辑保留。视觉沿用现有淡色系统（主色 `#3a9a6a`）。
8. **对照报告**：新增独立脚本（`data/research/` 输出），用同一算法分别跑旧申万缓存与新东财数据的共有日期区间，输出四态一致率、翻转分布、可比板块数与交易日数；不进生产链路。`sector_source_survey.py` 保留不动（其输入为旧申万 manifest，恰好构成对照基线读取器）。
9. **横截面宽度换源**：`build_dashboard.load_breadth()` 改读 `data/raw/sectors-em-industry/*.csv`（单层生产目录，避开与调研缓存 `data/raw/sectors-em/` 重复计数），文件数阀值沿用「不足 20 个则报错」，并把报错文案与注释里的「申万一级」改为东财行业板块；`swing_indicators.breadth_table()` 算法本体不动。`factor_survey.load_breadth` 同源读取，需同步指向新目录以保证调研口径与生产一致。宽度换源后 `index-signals.json` 必须同批重建，并输出新旧信号对照（信号条数、日期一致数、新增/消失数，带区间与样本量）；准确率重验沿用既有研究段/验证段划分，不调任何阈值（规则 7/8）。
10. **实施顺序**：先数据与流水线（新源可全量落盘、产物可重建），出板块对照报告与指数信号对照报告；确认后再切 server 与前端。避免数据未就绪就切换 UI。

## Risks / Trade-offs

- [东财接口限流或改版] → 请求间隔 + `call_with_retry` + 失败不静默替换（保留旧产物报错）；清单变更走 manifest 留痕，改版可及时发现。
- [换源导致四态历史大面积变化（调研：不一致率 24.7%）、历史深度不均（部分板块 <60 日）] → 对照报告先行并交用户核对；不足板块沿用 `insufficient_history` 标记，报告单列。
- [板块清单随东财分类调整增减改名] → 硬编码 BK 代码 + 每日名称校验，不一致则报错保留旧产物（不静默重新匹配）；按 BK 代码落盘保历史；停更板块按滞后规则标记不阻塞。
- [宽度换源带动指数顶底信号历史变化] → 不改任何阈值与权重，只换输入；先出旧/新信号对照（带样本量与区间）交用户核对，再覆盖生产 `index-signals.json`；旧产物与旧申万数据保留可回退。
- [无 git，误删无法回退] → 动手前将受影响文件与旧产物复制到 `.backup/2026-11-03-板块换源/`（沿用既有备份目录命名约定）。
- [与既有 change `center-sector-detail-dialog` 冲突] → 用户已决定：**先放着不动**，本 change 不归档、不废止、不修改它；实施中不触碰其文件。

## Migration Plan

1. 备份：受影响 pipeline/server/前端文件 + 旧 `sector-trends.json`/manifest → `.backup/2026-11-03-板块换源/`。
2. 实现东财抓取与增量（fetch_market、bootstrap），首次全量落盘。
3. 改造产物构建（单层、新版本号、data_source），重建 `sector-trends.json`。
4. 生成新旧对照报告（板块带样本量与统计区间）+ 宽度换源后的指数信号对照报告，交用户核对后再继续。
5. server 接口扁平化 + 前端单层页面改造。
6. pytest / npm test / npm run build 全绿，浏览器验证板块页。

回滚：恢复 `.backup/` 中文件；旧申万数据与旧产物原位未动，可直接还原为旧口径产物（需同步还原代码文件）。

## Open Questions

（无——清单粒度、breadth 换源、旧 change 处置均已由用户 2026-11-03 确认：31 个东财板块硬编码 BK 代码 + 名称校验；breadth 一起换东财并接受指数信号历史重算；`center-sector-detail-dialog` 先放着不动。）
