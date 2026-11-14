"""继续探测申万一级指数日线通道（新浪 KLineData / 腾讯多种代码形式）。

只写 research，不碰生产数据、不请求东财。串行 + 间隔。
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
SESSION.headers.update({"User-Agent": UA, "Referer": "https://finance.sina.com.cn/"})


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
        row["body_preview"] = text[:600]
        row["success"] = (
            r.status_code == 200 and len(text) > 50 and "null" not in text[:100]
        )
    except Exception as exc:
        row["error"] = f"{type(exc).__name__}: {exc}"
    row["latency_seconds"] = round(time.perf_counter() - started, 3)
    print(json.dumps(row, ensure_ascii=False)[:1400])
    time.sleep(2.0)
    return row


def main() -> None:
    results = []

    # 新浪 KLineData：申万代码试 sw801010 / SW801010 / sh801010 / sz801010
    for sym in ["sw801010", "SW801010", "sh801010", "sz801010"]:
        results.append(
            probe(
                f"sina-kline-{sym}",
                "https://quotes.sina.cn/cn/api/jsonp_v2.php/var%20t=/CN_MarketDataService.getKLineData",
                params={"symbol": sym, "scale": "240", "ma": "no", "datalen": "5"},
            )
        )

    # 腾讯：sz/sh 申万代码
    for sym in ["sz801010", "sh801010"]:
        results.append(
            probe(
                f"tencent-kline-{sym}",
                "https://web.ifzq.gtimg.cn/appstock/app/kline/kline",
                params={"param": f"{sym},day,,,40"},
            )
        )

    OUT.joinpath("probe-sw-daily-2-20261114.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print("[probe] 已写入", OUT / "probe-sw-daily-2-20261114.json")


if __name__ == "__main__":
    main()
