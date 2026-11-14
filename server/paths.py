"""项目路径与 sys.path 配置。

流水线模块使用扁平 import（与原项目一致，避免改动算法文件），
因此这里在包导入时把 `pipeline/` 加入 sys.path。
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipeline"
DATA = ROOT / "data"
WEB_DIST = ROOT / "signal-hub-web" / "dist"
INDEX_SIGNALS = DATA / "index-signals.json"
SECTOR_TRENDS = DATA / "sector-trends.json"
INSTITUTION_POSITIONS = DATA / "institution-positions.json"
# ETF 放量事件产物：只做展示标记，不参与任何信号计算
ETF_VOLUME_EVENTS = DATA / "etf-volume-events.json"
# 盘中横截面宽度的板块日线目录：必须与收盘重建（`fetch_market.THS_SECTOR_RAW`）读同一份
# 同花顺行业指数日线（2026-11-14 起由东财行业板块换源，90 个细分行业），由单测防路径漂移；
# 这里不直接 import fetch_market 是为了不把 akshare 拉进服务启动链路。
SECTOR_THS_RAW = DATA / "raw" / "sectors-ths-industry"
SECTOR_THS_MANIFEST = DATA / "raw" / "sectors-ths-industry-manifest.json"
# ETF 成交额序列（【ETF 放量】页）：只读原始日线，展示层专用，不参与任何信号与放量计算。
# 同样不 import fetch_etf，避免把 akshare 拉进服务启动链路；universe 只取自 manifest。
ETF_DAILY_RAW = DATA / "raw" / "etf-daily"
ETF_DAILY_MANIFEST = DATA / "raw" / "etf-daily-manifest.json"
# 龙虎榜知名游资聚合产物：只做事后统计与展示，不参与任何信号计算（AGENTS 第 1 条）
LHB_TRADERS = DATA / "lhb-traders.json"
# 连板周期产物：只读展示层；产物未落地时接口返回 preparing，不生成演示数值
LIMIT_CYCLE = DATA / "limit-cycle.json"
INDEX_THS_RAW = DATA / "raw" / "indices-ths"
# 上证指数代码：与 ETF 日线同为 `date` 字符串升序，按日期查表对齐
SH_INDEX_CODE = "sh000001"

for _path in (str(ROOT), str(PIPELINE)):
    if _path not in sys.path:
        sys.path.insert(0, _path)


def ensure_dirs() -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "raw").mkdir(parents=True, exist_ok=True)
