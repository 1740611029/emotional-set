"""Todo#2 板块替代源探测（只写 research，不碰生产数据、不请求东财）。

候选：
1) 同花顺行业板块：stock_board_industry_name_ths 清单 -> stock_board_industry_index_ths 日线
2) 新浪行业板块：stock_sector_spot(indicator="新浪行业") 清单 -> 日线可用性
3) 申万一级：sw_index_first_info 清单 -> 日线可用性

探测节奏：串行、每次请求间隔，避免把新源也弄限流。
"""

from __future__ import annotations

import json
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "pipeline") not in sys.path:
    sys.path.insert(0, str(ROOT / "pipeline"))

OUT = ROOT / "data" / "research" / "source-eval"
OUT.mkdir(parents=True, exist_ok=True)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def probe(label: str, fn, *, gap: float = 2.0) -> dict:
    started = time.perf_counter()
    row = {
        "probe": label,
        "ts": now(),
        "success": False,
        "latency_seconds": None,
        "error": None,
        "rows": 0,
        "columns": None,
        "payload_sample": None,
    }
    try:
        frame = fn()
        row["success"] = frame is not None and len(frame) > 0
        row["rows"] = 0 if frame is None else len(frame)
        if frame is not None and len(frame):
            row["columns"] = [str(c) for c in frame.columns]
            row["payload_sample"] = frame.head(8).to_dict("records")
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["latency_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps(row, ensure_ascii=False, default=str))
    time.sleep(gap)
    return row


def main() -> None:
    import akshare as ak

    results = []

    # 1) 同花顺行业板块清单
    results.append(
        probe("ths-industry-name", lambda: ak.stock_board_industry_name_ths())
    )

    # 2) 同花顺行业板块指数日线（取清单第一个【名称】再探测；清单失败则用常见名称）。
    #    注意：stock_board_industry_index_ths(symbol=行业名称)，内部经名称->代码映射后
    #    请求 d.10jqka.com.cn/v4/line/bk_{symbol_code}/01/{year}.js。
    ths_names = []
    ths_code = None
    try:
        import pandas as pd

        frame = ak.stock_board_industry_name_ths()
        if frame is not None and len(frame):
            ths_names = [str(x) for x in frame.iloc[:, 0].tolist()]
            sample = frame.iloc[0].to_dict()
            ths_code = str(sample.get("name") or sample.get("板块名称") or "")
            results.append(
                {
                    "probe": "ths-industry-name-sample",
                    "ts": now(),
                    "success": True,
                    "rows": len(frame),
                    "columns": [str(c) for c in frame.columns],
                    "payload_sample": sample,
                    "first_name": ths_code,
                    "all_names": ths_names,
                }
            )
            time.sleep(2.0)
    except Exception as exc:
        results.append(
            {
                "probe": "ths-industry-name-sample",
                "ts": now(),
                "success": False,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
    if ths_code:
        results.append(
            probe(
                "ths-industry-index-daily",
                lambda: ak.stock_board_industry_index_ths(
                    symbol=ths_code, start_date="20261101", end_date="20261114"
                ),
            )
        )
    else:
        results.append(
            {
                "probe": "ths-industry-index-daily",
                "ts": now(),
                "success": False,
                "error": "无同花顺板块名称，跳过",
            }
        )

    # 3) 新浪行业板块实时清单
    results.append(
        probe("sina-sector-spot", lambda: ak.stock_sector_spot(indicator="新浪行业"))
    )

    # 4) 申万一级分类
    results.append(probe("sw-first-info", lambda: ak.sw_index_first_info()))

    OUT.joinpath("probe-sectors-20261114.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2, default=str), encoding="utf-8"
    )
    print("[probe] 已写入", OUT / "probe-sectors-20261114.json")


if __name__ == "__main__":
    main()
