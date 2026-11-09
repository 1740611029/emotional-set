"""「先补数据再重建」开关的编排逻辑测试。

只验证判定 → 增量拉取 → 重建的顺序与提示文案，不触网、不重算真实产物。
"""
from __future__ import annotations

import io
import unittest
from contextlib import redirect_stdout
from unittest import mock

import refresh_and_rebuild as module

GAP_FRESH = {
    "expected": "2026-11-02", "basis": "calendar",
    "local": {"indices": "2026-11-02", "sectors": "2026-11-02", "institutions": "2026-11-02"},
    "lag": {"indices": 0, "sectors": 0, "institutions": 0}, "stale": [],
}
GAP_LAGGED = {
    "expected": "2026-11-02", "basis": "calendar",
    "local": {"indices": "2026-11-02", "sectors": "2026-10-30", "institutions": "2026-11-02"},
    "lag": {"indices": 0, "sectors": 1, "institutions": 0}, "stale": ["sectors"],
}


class MainTest(unittest.TestCase):
    def run_main(self, result: dict, gap: dict = GAP_FRESH,
                 etf: dict | None = None) -> tuple[int, str]:
        buffer = io.StringIO()
        etf_result = etf or {"status": "skipped", "updated": [], "skipped": 6, "failed": []}
        with mock.patch.object(module, "data_gap", return_value=gap), \
                mock.patch.object(module, "ensure_fresh_data", return_value=result), \
                mock.patch.object(module, "update_etf", return_value=etf_result), \
                mock.patch.object(module.build_dashboard, "main") as rebuild, \
                redirect_stdout(buffer):
            code = module.main()
        return code, f"{buffer.getvalue()}\nrebuild_called={rebuild.called}"

    def test_rebuilds_even_when_data_is_current(self):
        code, output = self.run_main({"refreshed": {}, "gap": GAP_FRESH, "after": GAP_FRESH})
        self.assertEqual(code, 0)
        self.assertIn("无落后数据，跳过拉取", output)
        self.assertIn("重建完成", output)
        self.assertIn("rebuild_called=True", output)

    def test_reports_fetched_counts(self):
        result = {"refreshed": {"sectors": 155, "indices": ["sh000001"]},
                  "gap": GAP_LAGGED, "after": GAP_FRESH}
        _, output = self.run_main(result)
        self.assertIn("板块 155 个有更新", output)
        self.assertIn("指数 1 个有更新", output)

    def test_etf_line_is_reported_separately(self):
        result = {"refreshed": {"sectors": 155, "etf": {"status": "ok", "updated": ["510300"],
                                                             "skipped": 5, "failed": []}},
                  "gap": GAP_FRESH, "after": GAP_FRESH}
        _, output = self.run_main(result)
        self.assertIn("板块 155 个有更新", output)
        self.assertIn("ETF 日线：1 只有更新、跳过 5 只", output)

    def test_etf_only_update_still_prints_no_lag_text(self):
        """ETF 单独有更新时，三条主线的文案不得被顶掉。"""
        result = {"refreshed": {"etf": {"status": "ok", "updated": [], "skipped": 6,
                                          "failed": ["510500"]}},
                  "gap": GAP_FRESH, "after": GAP_FRESH}
        _, output = self.run_main(result)
        self.assertIn("无落后数据，跳过拉取", output)
        self.assertIn("ETF 日线：0 只有更新、跳过 6 只、1 只失败", output)

    def test_etf_failure_is_shown_as_warning_line(self):
        result = {"refreshed": {"etf": {"status": "failed", "error": "RuntimeError: 网络断开"}},
                  "gap": GAP_FRESH, "after": GAP_FRESH}
        _, output = self.run_main(result)
        self.assertIn("ETF 日线：失败（RuntimeError: 网络断开，已跳过）", output)

    def test_etf_line_appears_even_without_update(self):
        """ETF 当日无待更新标的时也要有一行台账，不能静默跳过。"""
        _, output = self.run_main({"refreshed": {}, "gap": GAP_FRESH, "after": GAP_FRESH})
        self.assertIn("ETF 日线：0 只有更新、跳过 6 只", output)

    def test_etf_uninitialized_is_reported(self):
        _, output = self.run_main(
            {"refreshed": {}, "gap": GAP_FRESH, "after": GAP_FRESH},
            etf={"status": "uninitialized", "updated": [], "skipped": 0, "failed": []},
        )
        self.assertIn("ETF 日线：尚未初始化，跳过", output)

    def test_warns_when_source_has_not_published(self):
        result = {"refreshed": {"sectors": 0}, "gap": GAP_LAGGED, "after": GAP_LAGGED}
        code, output = self.run_main(result)
        self.assertEqual(code, 0)  # 重建已完成，源未发布不算脚本失败
        self.assertIn("WARNING", output)
        self.assertIn("板块 仍停在 2026-10-30", output)
        self.assertIn("落后 1 个交易日", output)

    def test_done_marker_skips_fetch_but_still_rebuilds(self):
        result = {"refreshed": {}, "skipped": ["sectors"], "gap": GAP_LAGGED}
        _, output = self.run_main(result, gap=GAP_LAGGED)
        self.assertIn("当日已标记补齐", output)
        self.assertIn("--force", output)
        self.assertIn("rebuild_called=True", output)

    def test_first_full_bootstrap_is_announced(self):
        result = {"bootstrap": True, "gap": GAP_FRESH, "refreshed": {}}
        _, output = self.run_main(result)
        self.assertIn("首次全量拉取", output)

    def test_force_flag_is_passed_down(self):
        with mock.patch.object(module, "data_gap", return_value=GAP_FRESH), \
                mock.patch.object(module, "ensure_fresh_data",
                                  return_value={"refreshed": {}, "gap": GAP_FRESH,
                                                "after": GAP_FRESH}) as fresh, \
                mock.patch.object(module, "update_etf",
                                  return_value={"status": "skipped", "updated": [],
                                                "skipped": 6, "failed": []}), \
                mock.patch.object(module.build_dashboard, "main"), \
                redirect_stdout(io.StringIO()):
            module.main(force=True)
        self.assertTrue(fresh.call_args.kwargs["force"])


class DescribeTest(unittest.TestCase):
    def test_missing_line_is_shown_as_dash(self):
        gap = dict(GAP_FRESH, local={**GAP_FRESH["local"], "sectors": None})
        text = module.describe(gap)
        self.assertIn("板块 —", text)
        self.assertIn("口径 calendar", text)


if __name__ == "__main__":
    unittest.main()
