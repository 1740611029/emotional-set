"""探测申万一级指数日线免费通道（只写 research，不碰生产数据、不请求东财）。

候选：
1) 百度股市通 finance.pae.baidu.com（申万代码 801010.SI）
2) 雪球 stock.xueqiu.com（申万代码形式待试 SI801010 / SW801010.XSHG）
3) 腾讯 web.ifzq.gtimg.cn（申万代码形式 sw801010 / sz801010）

串行 + 间隔，避免把新源也弄限流。输出 JSON 到 data/research/source-eval/。
"""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

import requests

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "data" / "research" / "source-eval"
OUT.mkdir(parents=True, exist_ok=True)

UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
)

SESSION = requests.Session()
SESSION.headers.update({"User-Agent": UA})


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def probe(
    label: str,
    url: str,
    *,
    headers: dict | None = None,
    params: dict | None = None,
    timeout: int = 30,
) -> dict:
    started = time.perf_counter()
    row = {
        "probe": label,
        "ts": now(),
        "success": False,
        "latency_seconds": None,
        "error": None,
        "status": None,
        "url": url,
        "body_preview": None,
        "body_len": None,
    }
    try:
        r = SESSION.get(url, headers=headers, params=params, timeout=timeout)
        row["status"] = r.status_code
        text = r.text
        row["body_len"] = len(text)
        row["body_preview"] = text[:800]
        row["success"] = (
            r.status_code == 200 and bool(text) and "错误" not in text[:200]
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["latency_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps(row, ensure_ascii=False)[:1500])
    time.sleep(2.0)
    return row


def main() -> None:
    results = []

    # 1) 百度股市通申万一级日线（ktype=day，query 用申万标准代码）
    results.append(
        probe(
            "baidu-sw-daily",
            "https://finance.pae.baidu.com/vapi/v1/getquotation",
            params={
                "srcid": "5353",
                "all": "1",
                "pointType": "string",
                "group": "quotation_kline_ab",
                "query": "801010.SI",
                "code": "801010.SI",
                "market_type": "ab",
                "newFormat": "1",
                "is_kc": "0",
                "ktype": "day",
                "finClientType": "pc",
            },
        )
    )

    # 2) 雪球申万指数日线（先试 SI801010）
    results.append(
        probe(
            "xueqiu-sw-daily-SI801010",
            "https://stock.xueqiu.com/v5/stock/chart/kline.json",
            params={
                "symbol": "SI801010",
                "begin": "1700000000000",
                "period": "day",
                "type": "before",
                "count": "-40",
                "indicator": "kline",
            },
        )
    )

    # 3) 腾讯申万指数日线（先试 sw801010）
    results.append(
        probe(
            "tencent-sw-daily-sw801010",
            "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
            params={"param": "sw801010,day,,,40"},
        )
    )

    OUT.joinpath("probe-sw-daily-20261114.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[probe] 已写入", OUT / "probe-sw-daily-20261114.json")


if __name__ == "__main__":
    main()
