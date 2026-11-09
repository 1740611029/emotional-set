# Spec Delta

## ADDED Requirements

### Requirement: ETF 日线成交额数据线

系统 MUST 维护一条与指数、板块、机构并列的 ETF 日线成交额数据线：数据源 MUST 固定为东财 ETF 历史日线接口，复权口径 MUST 固定为不复权，字段 MUST 统一为 `date/open/close/high/low/volume/amount`，其中 `amount` 单位为元。落盘路径 MUST 为 `data/raw/etf-daily/<code>.csv`，并 MUST 生成 `data/raw/etf-daily-manifest.json` 记录数据源、标的清单、每只的行数、起止日期与相对路径。

#### Scenario: 字段与单位固定

- **WHEN** 写入某只 ETF 的日线
- **THEN** 该文件 MUST 只含统一的七个字段，且 `amount` MUST 为元为单位的成交额

#### Scenario: manifest 与文件一致

- **WHEN** 完成一次抓取
- **THEN** manifest MUST 列出全部标的的代码、名称、行数、起止日期与路径，且与实际 CSV 一致

### Requirement: 标的清单固化与每日校验

生产清单 MUST 为硬编码的固定标的列表，每项包含代码与名称；系统 MUST 在每次请求前校验接口侧清单仍包含该代码且名称一致。校验不通过时 MUST 报错并保留上一版 CSV，MUST NOT 自动改用同指数的其他 ETF，因为更换标的等于更换一套放量历史。

#### Scenario: 代码消失时报错

- **WHEN** 某只固化标的在接口清单中查不到
- **THEN** 系统 MUST 报错并保留上一版数据

#### Scenario: 名称不一致时报错

- **WHEN** 接口返回的名称与固化名称不一致
- **THEN** 系统 MUST 报错并保留上一版数据，MUST NOT 静默沿用旧名称

### Requirement: 增量合并与尾日自愈

增量起点 MUST 包含本地最后一日（含），终点 MUST 取交易日历给出的最近已完成交易日，MUST NOT 把盘中未完成的 bar 当作收盘数据写入。合并时 MUST 逐值比对：仅当同一日期的数值确实发生变化时才覆写，使收盘瞬间未定稿的成交额能在下一轮被修正。历史不足预先声明最低行数的标的 MUST 显式标记，MUST NOT 静默丢弃。

#### Scenario: 盘中不落未完成 bar

- **WHEN** 在交易日 15:00 前触发刷新
- **THEN** 当日未完成的 bar MUST NOT 被写入 ETF 日线文件

#### Scenario: 尾日量额可被修正

- **WHEN** 源侧某一日定稿后的成交额与本地已存值不同
- **THEN** 下一轮刷新 MUST 将该日覆写为源侧最新值，且 MUST NOT 产生重复日期行

#### Scenario: 历史不足显式标记

- **WHEN** 某只标的可用日线少于最低行数要求
- **THEN** manifest MUST 标记该标的为历史不足，MUST NOT 静默排除

### Requirement: ETF 数据线失败不阻塞既有数据线

ETF 拉取失败 MUST 只产生告警日志，MUST NOT 抛出中断既有刷新与重建的异常。ETF 数据线 MUST NOT 参与"本地数据是否覆盖最近已完成交易日"的判定，即使 ETF 数据缺失，指数、板块、机构三条线判定新鲜时 MUST 仍返回"数据已覆盖"。

#### Scenario: 单只失败不影响其余

- **WHEN** 某只 ETF 抓取失败
- **THEN** 其余标的 MUST 继续处理，刷新流程 MUST 正常结束并打印告警

#### Scenario: 不改变新鲜度判定

- **WHEN** ETF 数据停在更早日期而指数、板块、机构均已覆盖最近已完成交易日
- **THEN** 新鲜度判定结果 MUST 仍为"不落后"
