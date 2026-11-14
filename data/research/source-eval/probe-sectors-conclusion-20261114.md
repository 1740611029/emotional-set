# 板块替代数据源探测结论（2026-11-14）

## 背景与目标口径

- 生产板块日线当前唯一依赖东财 push2 系（`push2his.eastmoney.com/api/qt/stock/kline/get`，
  经 akshare `stock_board_industry_hist_em`），自 2026-11-12 起被 IP 限流，板块 CSV 停在
  2026-11-11，落后最新交易日（11-13）2 天；多日冷却重试未恢复。
- 目标口径（openspec proposal 约束）：与 31 个单层行业（BK0433…BK1035，2026-11-03 固化，
  名称与申万一级 31 行业对齐）一一可比、不复权、字段顺序与单位不变、全量重拉 2020-10-30 起。
- 约束：只写 `data/research/source-eval/`，不碰生产数据；探测不请求东财（避免延长限流）。

## 实测结果

| 数据源 | 通道 | 结果 | 说明 |
| --- | --- | --- | --- |
| 申万一级日线（801xxx.SI） | 东财 push2his | ❌ IP 限流 | 多日未恢复，任何东财域名同属 push2 系，不能绕开 IP 封禁 |
| 申万一级日线 | 新浪 KLineData（sw/sh/sz801010） | ❌ 不支持 | 返回 null，新浪无申万代码 |
| 申万一级日线 | 腾讯 ifzq（sw/sz/sh801010） | ❌ 无数据 | 代码存在但 day 为空 |
| 申万一级日线 | 百度股市通 finance.pae.baidu.com | ❌ 403 反爬 | hit risk |
| 申万一级日线 | 雪球 stock.xueqiu.com | ❌ 需登录 | error 400016 |
| 申万一级日线 | 乐咕乐股 legulegu | ❌ 无日线接口 | sw-industry-overview 仅清单+估值（PE/PB/股息率）；all.min.js 无 kline API |
| 申万一级日线 | 申万宏源官网 swsresearch.com | ❌ SPA | 无直接历史数据下载通道 |
| 申万一级日线 | akshare 内置接口 | ❌ 无 | sw_index_first_info 仅清单；stock_industry_clf_hist_sw 仅股票→行业分类 |
| 同花顺行业指数日线 | d.10jqka.com.cn/v4/line/bk_{code}/01/{year}.js | ✅ 可用 | 90 个细分行业、覆盖最新交易日（11-13）、响应 ~2.3s/行业、无公开限流；列=日期/开盘价/最高价/最低价/收盘价/成交量/成交额 |
| 新浪行业板块 | stock_sector_spot("新浪行业") | ⚠️ 仅实时 | 49 个新浪自分类快照，非日线、非申万 |
| 申万旧缓存 | data/raw/sectors/first（801xxx.csv，尾日 2026-11-03） | ⚠️ 无拉取脚本 | 仅初始提交遗留，疑似乐咕口径（点位/亿股/亿元），不可续拉 |

## 结论

1. **31 个申万一级的免费公开日线通道在当前验证范围内全部不可用**；既有评估报告
   `source-migration-20261114.md` 中"BK 东财独家无第二免费源"的结论成立。
2. **同花顺行业指数日线是可用的免费替代**：不限流、覆盖最新交易日、字段完整；但其行业粒度
   为 90 个细分行业（≈申万二级，如半导体/白酒/化学原料/银行/综合等），**与 31 个申万一级
   不是一一对应**，不满足 proposal 的 exact_match 口径。
3. **耦合风险**：板块日线同时喂给 `build_dashboard.py::load_breadth()`（指数信号"行业横截面
   宽度"），缺失或换粒度都会影响 7 只指数信号的计算；删除板块趋势页面须同步改造该依赖，
   否则构建 MUST 失败。

## 可选路径（待用户授权）

- **A. 切换同花顺行业指数日线**：板块趋势与指数信号宽度改用 90 个细分行业，全量重拉
  2020-10-30 起历史（90 行业 × 7 年 ≈ 630 请求，串行约 25 分钟），行业口径 31→90，
  前端/算法/产物随之适配。数据可每日更新、不再被封。
- **B. 删除板块趋势页面**：移除菜单、页面、API 与产物，同时改造指数信号宽度依赖（缩小信号
  能力），保留其余功能。
- **C. 保留东财等待恢复**：不改源不删页，数据可能长期停在 11-11。

## 相关探测脚本与产物

- `pipeline/probe_sector_sources.py`（同花顺/新浪/申万清单与同花顺日线）
- `pipeline/probe_sw_daily.py`（百度/雪球/腾讯申万日线）
- `pipeline/probe_sw_daily2.py`（新浪/腾讯申万代码形式）
- `data/research/source-eval/probe-sectors-20261114.json`
- `data/research/source-eval/probe-sw-daily-20261114.json`
- `data/research/source-eval/probe-sw-daily-2-20261114.json`