"""板块成分股快照读取（【板块趋势】页个股弹窗数据）。

只读展示层：缓存 data/sector-stocks.json，按 mtime 判脏；
缺文件 / 读盘异常一律降级为 None（接口层转 preparing），绝不向客户端抛 5xx。
与 etf_volume.EtfVolumeStore 同风格，但不参与任何信号计算（AGENTS 第 1 条）。

附加 LimitCycleStore：把 data/limit-cycle.json 涨停池建成 code -> limit 索引，
供接口层按 6 位代码 join 到成分股（非涨停股 limit 为 None），同样只读展示层。
"""

from __future__ import annotations

import json
import threading

from . import paths


class SectorStocksStore:
    """单文件快照缓存：文件 mtime 变化即重读；缺文件/读盘失败返回 None。"""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._result: dict | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._mtime, self._result = None, None

    def load(self) -> dict | None:
        path = paths.SECTOR_STOCKS
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        try:
            with self._lock:
                if self._mtime == mtime and self._result is not None:
                    return self._result  # 命中缓存：重复请求逐字节一致
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(
                f"[sector_stocks] 成分股快照读取失败（降级 preparing）：{exc}",
                flush=True,
            )
            return None
        with self._lock:
            self._mtime, self._result = mtime, payload
        return payload


class LimitCycleStore:
    """连板周期涨停池索引：code -> {first_seal, boards, seal_amount, status}。

    与 SectorStocksStore 同风格：按 mtime 判脏；缺文件 / 读盘异常返回 None。
    仅供成分股响应 join 展示，不参与任何信号计算；产物 data_state != ready 时返回 None。
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._mtime: float | None = None
        self._index: dict[str, dict] | None = None
        self._as_of: str | None = None

    def invalidate(self) -> None:
        with self._lock:
            self._mtime, self._index, self._as_of = None, None, None

    def as_of(self) -> str | None:
        with self._lock:
            return self._as_of

    def load(self) -> dict[str, dict] | None:
        path = paths.LIMIT_CYCLE
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        try:
            with self._lock:
                if self._mtime == mtime and self._index is not None:
                    return self._index
            payload = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(
                f"[sector_stocks] 涨停池索引读取失败（成分股 limit 置空）：{exc}",
                flush=True,
            )
            return None
        index: dict[str, dict] = {}
        if isinstance(payload, dict) and payload.get("data_state") == "ready":
            for item in payload.get("stocks") or []:
                if not isinstance(item, dict):
                    continue
                code = str(item.get("code") or "")
                if not code:
                    continue
                index[code] = {
                    "first_seal": item.get("first_seal"),
                    "boards": item.get("boards"),
                    "seal_amount": item.get("seal_amount"),
                    "status": item.get("status"),
                }
        with self._lock:
            self._mtime, self._index = mtime, index
            self._as_of = payload.get("as_of") if isinstance(payload, dict) else None
        return index
