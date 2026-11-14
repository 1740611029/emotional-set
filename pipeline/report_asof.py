"""重建完成后输出三份产物的 as_of 与落后交易日数（不联网）。

供「重建信号数据.bat」选项 1（rebuild only）调用：只读磁盘，
原始数据仍落后时如实报告，不因生成时间更新而显示为最新。
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

from bootstrap import data_gap  # noqa: E402

ROOT = PIPELINE.parent
DATA = ROOT / "data"
# 三份产物的 as_of 字段来源：指数/板块产物自带 as_of；ETF 放量事件产物亦自带
LINES = (
    ("indices", "指数信号", "index-signals.json"),
    ("sectors", "板块趋势", "sector-trends.json"),
    ("etf", "ETF 放量事件", "etf-volume-events.json"),
)


def main() -> int:
    gap = data_gap()
    expected, lag = gap["expected"], gap["lag"]
    print(f"[as_of] 最近已完成交易日：{expected}（口径 {gap['basis']}）")
    for key, label, name in LINES:
        path = DATA / name
        if not path.exists():
            print(f"[as_of] {label}（{name}）：产物缺失")
            continue
        try:
            as_of = json.loads(path.read_text(encoding="utf-8")).get("as_of")
        except Exception as exc:
            print(f"[as_of] {label}（{name}）：读取失败 {type(exc).__name__}")
            continue
        lag_text = lag.get(key)
        lag_text = "未知" if lag_text is None else f"{lag_text} 个交易日"
        print(f"[as_of] {label}（{name}）：as_of={as_of}，落后 {lag_text}")
    print("[as_of] 说明：本模式不联网、不会改变数据所属交易日；"
          "as_of 只由原始数据尾日决定，与生成时间无关。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
