"""统一限速器单元测试：冷却进入/退出、跳过返回、跨发行方隔离、快照可观测。"""
from __future__ import annotations

import unittest
from unittest import mock

from rate_limiter import RateLimiter


class CooldownTest(unittest.TestCase):
    def setUp(self) -> None:
        self.limiter = RateLimiter(cooldown_seconds=1800)

    def test_acquire_allowed_when_no_cooldown(self):
        self.assertTrue(self.limiter.acquire("eastmoney", line="sector-daily"))

    def test_mark_limited_enters_cooldown(self):
        with mock.patch("rate_limiter.time.time", return_value=1000.0):
            state = self.limiter.mark_limited("eastmoney")
        self.assertEqual(state["issuer"], "eastmoney")
        self.assertEqual(state["cooldown_until"], 2800.0)
        with mock.patch("rate_limiter.time.time", return_value=1500.0):
            self.assertAlmostEqual(self.limiter.cooldown_remaining("eastmoney"), 1300.0)

    def test_acquire_returns_skip_during_cooldown(self):
        self.limiter.mark_limited("eastmoney")
        self.assertFalse(self.limiter.acquire("eastmoney", line="etf-daily"))
        self.assertFalse(self.limiter.acquire("eastmoney", line="etf-spot"))

    def test_cooldown_exits_after_expiry(self):
        with mock.patch("rate_limiter.time.time", return_value=1000.0):
            self.limiter.mark_limited("eastmoney")
        with mock.patch("rate_limiter.time.time", return_value=2800.5):
            self.assertEqual(self.limiter.cooldown_remaining("eastmoney"), 0.0)
            self.assertTrue(self.limiter.acquire("eastmoney", line="sector-daily"))

    def test_skip_is_recorded_in_snapshot(self):
        self.limiter.mark_limited("eastmoney")
        self.limiter.acquire("eastmoney", line="etf-daily")
        self.limiter.acquire("eastmoney", line="etf-spot")
        snapshot = self.limiter.snapshot()
        self.assertEqual(snapshot["eastmoney"]["skipped_lines"], ["etf-daily", "etf-spot"])
        self.assertGreater(snapshot["eastmoney"]["cooldown_remaining"], 0)

    def test_issuers_are_isolated(self):
        """东财冷却不得影响同花顺（ths）线，反之亦然。"""
        self.limiter.mark_limited("eastmoney")
        self.assertTrue(self.limiter.acquire("ths", line="index-daily"))
        self.limiter.mark_limited("ths")
        self.assertFalse(self.limiter.acquire("eastmoney", line="sector-daily"))
        snapshot = self.limiter.snapshot()
        self.assertGreater(snapshot["eastmoney"]["cooldown_remaining"], 0)
        self.assertGreater(snapshot["ths"]["cooldown_remaining"], 0)
        self.assertEqual(snapshot["eastmoney"]["skipped_lines"], ["sector-daily"])
        self.assertEqual(snapshot["ths"]["skipped_lines"], [])

    def test_snapshot_reports_zero_when_clean(self):
        snapshot = RateLimiter().snapshot()
        self.assertEqual(set(snapshot), {"eastmoney", "sina", "tencent", "ths"})
        for issuer in snapshot.values():
            self.assertEqual(issuer["cooldown_remaining"], 0.0)
            self.assertEqual(issuer["skipped_lines"], [])


if __name__ == "__main__":
    unittest.main()
