"""宽基 ETF 申购净流入数据线纯函数测试（离线、确定性，不发任何网络请求）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

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


class IncrementalUpdateTest(unittest.TestCase):
    """增量刷新入口的口径不变量：不重建宽基池、份额缺口不产生假 0 值。"""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name)
        self.manifest = root / "universe.json"
        self.output = root / "flow.json"
        self.manifest.write_text(json.dumps({"universe": []}), encoding="utf-8")
        self.output.write_text(
            json.dumps({"as_of": "2026-11-18", "coverage": {"start": "2025-01-01"}}),
            encoding="utf-8",
        )
        patches = [
            patch.object(module, "MANIFEST", self.manifest),
            patch.object(module, "OUTPUT", self.output),
            patch.object(
                module, "last_completed_trading_day",
                return_value=("2026-11-23", "test"),
            ),
            patch.object(
                module, "_read_index_calendar",
                return_value=["2026-11-19", "2026-11-20", "2026-11-23"],
            ),
            patch.object(
                module, "collect_shares", return_value={"fetched": 3, "skipped": 0}
            ),
        ]
        for item in patches:
            item.start()
            self.addCleanup(item.stop)

    def test_never_rebuilds_universe(self):
        with (
            patch.object(
                module, "build_universe",
                side_effect=AssertionError("不得重建宽基池"),
            ) as build,
            patch.object(module, "_share_cache_ready", return_value=True),
            patch.object(
                module, "compute",
                return_value={"as_of": "2026-11-23", "stats": {"last_day_yi": 1.0}},
            ) as comp,
        ):
            result = module.update_share_flow()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["as_of"], "2026-11-23")
        build.assert_not_called()
        comp.assert_called_once_with("2025-01-01", "2026-11-23")

    def test_missing_last_day_share_does_not_advance(self):
        def ready(_exchange: str, day: str) -> bool:
            return day != "2026-11-23"

        with (
            patch.object(module, "_share_cache_ready", side_effect=ready),
            patch.object(
                module, "compute",
                return_value={"as_of": "2026-11-20", "stats": {"last_day_yi": 1.0}},
            ) as comp,
        ):
            result = module.update_share_flow()
        self.assertEqual(result["status"], "updated")
        self.assertEqual(result["as_of"], "2026-11-20")
        comp.assert_called_once_with("2025-01-01", "2026-11-20")

    def test_all_shares_missing_skips_compute(self):
        with (
            patch.object(module, "_share_cache_ready", return_value=False),
            patch.object(
                module, "compute", side_effect=AssertionError("份额全缺不得重算")
            ),
        ):
            result = module.update_share_flow()
        self.assertEqual(result["status"], "no_share_data")

    def test_uninitialized_does_not_touch_source(self):
        self.manifest.unlink()
        with patch.object(
            module, "collect_shares", side_effect=AssertionError("未初始化不得触网")
        ):
            result = module.update_share_flow()
        self.assertEqual(result["status"], "uninitialized")


if __name__ == "__main__":
    unittest.main()
