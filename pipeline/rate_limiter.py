"""跨数据线统一限速队列：按发行方冷却 + 批次参数收敛 + 识别即止损。

所有外部行情取数（指数 / 板块 / ETF 的清单校验与日线）在发起前必须经
`LIMITER.acquire(issuer, line)` 取令牌：冷却期内立即返回 False（跳过本轮），
绝不重试延长封禁。任一发行方的任一条数据线触发限流 → `mark_limited(issuer)`
进入全局冷却，冷却覆盖该发行方的全部数据线（含 ETF 清单校验与日线）。

批次参数（批大小 / 批间间隔 / 单请求间隔）收敛为本模块常量，运行前声明；
`snapshot()` 供健康检查与手动重建入口读取冷却剩余秒数与被跳过的数据线。

本模块只做请求节奏与冷却治理，不参与任何信号计算。
"""
from __future__ import annotations

import time

# ---- 统一分批参数（原 fetch_market 的 SECTOR_* 经验值收敛而来，运行前声明）----
BATCH_SIZE = 6          # 每批标的数
BATCH_PAUSE = 20.0      # 批间休息（秒）
REQUEST_GAP = 1.0       # 单请求间隔（秒）
# 触发限流后的全局冷却时长（沿用"停高频、30 分钟低频重试"口径）
COOLDOWN_SECONDS = 30 * 60

# 发行方标识：冷却按发行方隔离（东财限流不影响新浪/腾讯线，反之亦然）。
# 2026-11-14 换源后：指数与 ETF 日线走新浪、ETF 清单走腾讯，东财只剩板块线。
ISSUERS = ("eastmoney", "sina", "tencent")


class RateLimiter:
    """进程内单例限速器：冷却状态与被跳过的数据线快照。"""

    def __init__(self, *, cooldown_seconds: float = COOLDOWN_SECONDS) -> None:
        self.cooldown_seconds = float(cooldown_seconds)
        self._limited_until: dict[str, float] = {}   # issuer -> epoch 秒
        self._limited_at: dict[str, float] = {}      # issuer -> 触发时刻
        self._skipped: dict[str, list[str]] = {}     # issuer -> 冷却期内被跳过的数据线

    # ---------- 冷却判定 ----------

    def cooldown_remaining(self, issuer: str) -> float:
        until = self._limited_until.get(issuer)
        if until is None:
            return 0.0
        remaining = max(0.0, until - time.time())
        if remaining == 0.0:  # 冷却自然到期：清理状态，下次请求即解封探测
            self._limited_until.pop(issuer, None)
            self._limited_at.pop(issuer, None)
        return remaining

    def acquire(self, issuer: str, line: str) -> bool:
        """取令牌：冷却期内返回 False（跳过该请求）且记录被跳过的数据线；True 才可发请求。

        调用方拿到 False 后 MUST 中止本轮该线的后续请求，MUST NOT 原地重试。
        """
        if self.cooldown_remaining(issuer) > 0:
            skipped = self._skipped.setdefault(issuer, [])
            if line not in skipped:
                skipped.append(line)
            return False
        return True

    def mark_limited(self, issuer: str) -> dict:
        """限流识别即止损：进入全局冷却并记录触发时刻；返回冷却说明。"""
        now = time.time()
        self._limited_at[issuer] = now
        self._limited_until[issuer] = now + self.cooldown_seconds
        return {
            "issuer": issuer,
            "limited_at": now,
            "cooldown_until": self._limited_until[issuer],
            "cooldown_seconds": self.cooldown_seconds,
        }

    # ---------- 可观测 ----------

    def snapshot(self) -> dict[str, dict]:
        """各发行方冷却剩余秒数与冷却期内被跳过的数据线；0 表示不在冷却期。"""
        return {
            issuer: {
                "cooldown_remaining": round(self.cooldown_remaining(issuer), 1),
                "skipped_lines": list(self._skipped.get(issuer, [])),
            }
            for issuer in sorted(set(ISSUERS) | set(self._limited_until))
        }

    def reset(self) -> None:
        """仅供测试：清空全部冷却状态。"""
        self._limited_until.clear()
        self._limited_at.clear()
        self._skipped.clear()


# 进程级单例：板块 / ETF / 指数各线共用，保证冷却状态与请求节奏全局一致
LIMITER = RateLimiter()
