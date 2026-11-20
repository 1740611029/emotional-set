"""宽基 ETF 申购净流入数据线纯函数测试（离线、确定性，不发任何网络请求）。"""
from __future__ import annotations

import unittest

import fetch_etf_share_flow as module


class BroadUniverseTest(unittest.TestCase):
    def test_broad_whitelist(self):
        self.assertTrue(module.is_broad("华泰柏瑞沪深300ETF"))
        self.assertTrue(module.is_broad("易方达创业板ETF"))
        self.assertTrue(module.is_broad("华夏上证科创板50成份ETF"))
        self.assertTrue(module.is_broad("南方中证A500ETF"))

    def test_theme_and_cross_border_excluded(self):
        self.assertFalse(module.is_broad("国联安半导体ETF"))
        self.assertFalse(module.is_broad("华夏恒生科技ETF"))
        self.assertFalse(module.is_broad("易方达中概互联网ETF"))
        self.assertFalse(module.is_broad("华安黄金ETF"))
        self.assertFalse(module.is_broad("国泰上证10年期国债ETF"))

    def test_similar_name_not_broad(self):
        # "300" 出现在非宽基命名里也不该命中（白名单是"沪深300"/"中证300"/"300ETF"）
        self.assertFalse(module.is_broad("某主题精选混合"))


class SixMonthWindowsTest(unittest.TestCase):
    def test_empty(self):
        self.assertEqual(list(module._six_month_windows([])), [])

    def test_single_group(self):
        days = ["2026-01-05", "2026-02-03", "2026-03-05"]
        self.assertEqual(list(module._six_month_windows(days)), [("2026-01-05", "2026-03-05")])

    def test_splits_over_180_days(self):
        days = ["2025-01-02", "2025-03-03", "2025-12-01"]
        groups = list(module._six_month_windows(days))
        self.assertEqual(len(groups), 2)
        self.assertEqual(groups[0], ("2025-01-02", "2025-03-03"))
        self.assertEqual(groups[1], ("2025-12-01", "2025-12-01"))


if __name__ == "__main__":
    unittest.main()
