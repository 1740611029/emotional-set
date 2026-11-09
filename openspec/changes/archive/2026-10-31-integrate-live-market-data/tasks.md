# Tasks

## 1. 仓库与依赖准备

- [x] 1.1 创建 `pipeline/`、`server/`、`data/` 目录骨架；`server/` 含 `__init__.py`，`pipeline/` 保持扁平模块（与原项目一致，避免改动算法文件的 import 结构），验证：`python -c "import server; import sys; sys.path.insert(0,'pipeline'); import build_dashboard"` 不报错
- [x] 1.2 在项目根新增 `requirements.txt`（`fastapi`、`uvicorn[standard]`、`akshare`、`pandas`、`numpy`、`requests`、`jsonschema`、`pytest`，固定版本区间），验证：全新虚拟环境中 `pip install -r requirements.txt` 成功
- [x] 1.3 新增/补充 `.gitignore`（`data/raw/`、`__pycache__/`、`.venv/`、`node_modules/`、`signal-hub-web/dist/`、`*.tmp`），验证：`git status --ignored` 中 `data/raw` 被忽略而 `data/index-signals.json` 未被忽略
- [x] 1.4 将 `D:\信号指标\data\index-signals.json` 与 `sector-trends.json` 复制为仓库初始数据，验证：`python -c "import json;json.load(open('data/index-signals.json',encoding='utf-8'))"` 无异常，且文件约 928KB / 28KB

## 2. 流水线迁入与去绝对路径

- [x] 2.1 迁入 8 个模块（`fetch_market`、`research_algorithms`、`multifactor_research`、`confirmed_index_signals`、`confirmed_sector_trends`、`build_dashboard`、`io_utils`、`schemas`）到 `pipeline/`，保持内部 import 关系，验证：`python -c "from pipeline import build_dashboard, confirmed_index_signals"` 成功
- [x] 2.2 将所有路径定位改为基于 `Path(__file__).resolve().parents[1]` 的项目根，验证：对 `pipeline/` 执行盘符绝对路径扫描（`[A-Za-z]:/`）无命中
- [x] 2.3 废弃 `build_production_data.py`，确认其输出结构已被 `build_dashboard.py` 完全覆盖，验证：`pipeline/` 下不存在该文件，且 `build_dashboard` 产出的 `recent_signals` 条数不少于旧版
- [x] 2.4 新增 `pipeline/bootstrap.py`：缺失时拉取原始日线、已存在且达最小长度时复用（指数 1250 行、板块 60 行），验证：清空 `data/raw/` 后运行，`data/raw/indices-ths/` 生成 7 个 CSV 与 manifest，`data/raw/sectors/{first,second}/` 分别 31 与 131 个 CSV

## 3. 数值回归基线

- [x] 3.1 编写 `pipeline/compare_snapshot.py`：逐指数比对 `latest`、`change_pct`、`current_signal`、`signals`（日期+类型+价格）、`accuracy`，验证：用现有 `data/raw` 重建后与仓库初始 JSON 比对输出全 PASS
- [x] 3.2 校验准确率摘要必须含样本数与区间，验证：比对脚本对缺少 `triggers`/`start`/`end` 的字段报 FAIL，且正常数据报 PASS
- [x] 3.3 新增 `pipeline/test_pipeline.py` 覆盖 `validate_history` 的拒绝场景（重复日期、非升序、OHLC 关系异常、非正价格），验证：`pytest pipeline/` 全部通过

## 4. 候选层状态导出

- [x] 4.1 新增 `pipeline/candidate_state.py`：复用 `multifactor_indicators()` 与候选生成函数（以公开别名暴露），对最近 3 个交易日内的候选输出 `{date, type, price, layers:{price,momentum,volume}, passed}`，验证：对现有指数运行后输出结构与 `design.md` D4 一致
- [x] 4.2 在 `build_dashboard.py` 中把候选结果挂到每个指数的 `candidates` 字段，验证：`compare_snapshot.py` 报告 `signals` 逐条不变（PASS）
- [x] 4.3 新增候选单测：构造仅通过 1 层与通过 3 层的两组样本，验证 `passed` 分别为 1 与 3；并验证已确认信号不会被重复计入候选

## 5. 实时行情适配层

- [x] 5.1 新增 `server/realtime.py`：单次批量请求取 7 大宽基指数快照，输出 `{code, name, open, high, low, close, volume, ts}`，验证：调用一次返回 7 条，`code` 与 `fetch_market.INDEXES` 完全一致
- [x] 5.2 实现"当日合成 bar"拼接：日线末行日期等于当日则替换末行，否则追加，验证：单测断言拼接后日期唯一且升序，且 `validate_history` 不抛异常
- [x] 5.3 实现主源/备源切换与失败返回：主源异常时尝试备源，两者皆失败返回 `None` 而不抛异常，验证：注入异常源后调用返回 `None` 且日志含降级原因
- [x] 5.4 实现盘中快照计算：复用 `confirmed_signals()` 并标记 `provisional`，验证：连续两次不同实时价得到的盘中信号允许不同，且 `data/*.json` 的修改时间保持不变

## 6. 服务与调度

- [x] 6.1 新增 `server/snapshot.py` 组装快照（`as_of`/`mode`/`generated_at`/`stale`/`indices`/`sectors`/`recent_signals`/`candidates`/`algorithm_version`），验证：构造后字段齐全且可 JSON 序列化
- [x] 6.2 新增 `server/scheduler.py`：30 秒循环 + `ZoneInfo("Asia/Shanghai")` 交易时段判定 + 实时源回执二次校验，验证：非交易时段运行时实时源调用计数为 0（日志断言）
- [x] 6.3 实现收盘后重算：交易日 15:00 后首次运行触发一次，复用 `protected_write()` 原子落盘，验证：同日连续触发两次产出字节级一致的结果
- [x] 6.4 实现单飞锁：并发请求复用同一次计算，验证：10 个并发请求下计算计数器为 1
- [x] 6.5 新增 `/api/health` 返回 `alive`/`last_refresh_at`/`mode`/`data_source`/`algorithm_version`，验证：`curl` 返回 200 且字段齐全

## 7. API 契约

- [x] 7.1 实现 `/api/snapshot`，响应通过 `jsonschema` 校验，验证：`curl -s /api/snapshot | python -m json.tool` 无异常且校验脚本 PASS
- [x] 7.2 实现冷启动状态：落盘数据不存在时先补齐日线，期间返回明确的"数据准备中"状态（含进度），验证：清空 `data/` 后首次请求返回该状态而非空结构或 500
- [x] 7.3 实现单指数历史端点 `/api/indices/{code}/history`，验证：返回约 1450 行、日期升序、末行与快照 `latest` 一致
- [x] 7.4 实时源不可用时快照返回上次成功数据并置 `stale=true`，验证：注入失败源后响应 `stale` 为真且 `generated_at` 保持上次成功时间

## 8. 前端数据层

- [x] 8.1 新增 `src/data/types.ts`（`SignalType = 'bottom'|'top'|'none'`，含快照/指数/板块/候选/准确率类型），验证：`npm run build`（tsc）通过
- [x] 8.2 新增 `src/data/client.ts`：`fetchSnapshot()` 带 10 秒超时、`cache: 'no-store'`、结构校验与错误归一化，验证：断网/改错地址时返回结构化错误而非抛出未捕获异常
- [x] 8.3 新增 `src/data/MarketDataProvider.tsx`：30 秒轮询、`visibilitychange` 暂停与恢复即刷、`AbortController` 取消在途请求、失败保留上次数据，验证：浏览器 Network 面板中切到后台无新请求，恢复后立即出现一次请求
- [x] 8.4 在 `vite.config.ts` 增加 `/api` 代理到 `http://127.0.0.1:8000`，验证：`npm run dev` 下访问 `/api/health` 返回 200

## 9. 页面改造

- [x] 9.1 Dashboard 汇总卡片改为真实计算（今日确认数、候选数、带样本量的加权成功率、信号最活跃指数），验证：与快照 JSON 手算结果逐项一致
- [x] 9.2 Dashboard 最新信号表改接 `recent_signals`（日期、指数、信号类型、触发价、至今涨跌幅、状态），验证：渲染行数与快照中最近信号数一致
- [x] 9.3 宽基指数页改为真实 7 大指数卡片（名称、最新价、涨跌幅、当前信号、准确率带样本），验证：卡片数量为 7 且名称与 JSON 完全一致
- [x] 9.4 指标详情页改为"壳层真实数据 + 指标明细待接入"标注，验证：页面中不存在写死的点位/涨跌幅/胜率
- [x] 9.5 顶栏显示 `as_of`、`mode`、`stale` 与最近更新时间，并提供手动刷新，验证：盘中态与收盘态文案可切换，`stale` 为真时出现陈旧提示
- [x] 9.6 删除 `src/data/mock.ts` 并清理全部引用，验证：`grep -r "data/mock" signal-hub-web/src` 无结果且 `npm run build` 通过
- [x] 9.7 补充空状态：无候选、无信号、无板块时显示明确文案，验证：构造空快照后三个区域均显示空状态而非空白

## 10. 打包与运行

- [x] 10.1 FastAPI 挂载 `signal-hub-web/dist` 并实现 SPA 回落，验证：`npm run build` 后单进程访问 `/` 与 `/indices` 均返回 200 且刷新不 404
- [x] 10.2 新增 `start.py`（检测依赖 → 必要时构建 → 启动并打印地址）与 `start.bat` / `start.sh` 薄封装，验证：在干净目录执行后终端打印可访问地址
- [x] 10.3 新增 README 运行说明（依赖安装、首次数据拉取、端口配置、盘中/收盘模式说明、常见故障），验证：按文档命令从零跑通一次

## 11. 集成验收

- [x] 11.1 端到端（盘中）：注入可控实时源后请求快照，验证 `mode=live`、盘中信号带 `provisional`、`data/*.json` 未被修改
- [x] 11.2 端到端（收盘）：触发重算后验证 `mode=close`、数据已落盘、准确率统计中不含任何盘中信号
- [x] 11.3 可移植性验收：全仓库盘符绝对路径扫描无命中，并将项目复制到新目录后按 README 启动成功
- [x] 11.4 性能验收：快照端点响应时间小于 200ms（本地），30 秒周期内 CPU 占用无明显尖峰，验证：连续 10 次请求耗时统计与进程资源观察
