"""交易日历与数据新鲜度判定的回归测试。

只测调度用的判定逻辑：日历缺失时的近似、收盘时刻的分界、落后交易日计数、
以及增量刷新的“已补齐”标记与重试规则，均不涉及任何信号计算。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from datetime import date, datetime
from pathlib import Path
from unittest import mock

import pandas as pd

import bootstrap
import trading_calendar
from trading_calendar import lag_trading_days, last_completed_trading_day

CN = trading_calendar.CN
# 用真实工作日构造日历：2026-10-26 ~ 2026-12-31，含 10-30（周五）与 11-02（下周一）
TRADE_DAYS = [item.date() for item in pd.bdate_range("2026-10-26", "2026-12-31")]


def make_gap(stale, expected="2026-11-02"):
    """造一份新鲜度结果：stale 里的类停在 2026-10-30，其余已覆盖 expected。"""
    keys = ("indices", "sectors", "institutions")
    return {
        "expected": expected,
        "basis": "calendar",
        "local": {key: ("2026-10-30" if key in stale else expected) for key in keys},
        "lag": {key: (1 if key in stale else 0) for key in keys},
        "stale": [key for key in keys if key in stale],
    }


class CalendarTestCase(unittest.TestCase):
    """把日历缓存指向临时文件，避免测试依赖仓库里的真实缓存。"""

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.cache = Path(self._tmp.name) / "trade-calendar.json"
        self._patch = mock.patch.object(trading_calendar, "CACHE_PATH", self.cache)
        self._patch.start()
        self.addCleanup(self._patch.stop)
        self.addCleanup(self._tmp.cleanup)

    def seed(self, dates: list[date]) -> None:
        payload = {
            "source": "test",
            "cached_at": datetime.now(CN).isoformat(timespec="seconds"),
            "dates": [item.strftime("%Y-%m-%d") for item in dates],
        }
        self.cache.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        trading_calendar._reset_memory()


class LastCompletedTradingDayTest(CalendarTestCase):
    def test_before_close_excludes_today(self):
        self.seed(TRADE_DAYS)
        day, basis = last_completed_trading_day(datetime(2026, 11, 3, 10, 0, tzinfo=CN))
        self.assertEqual((day, basis), ("2026-11-02", "calendar"))

    def test_at_or_after_close_includes_today(self):
        self.seed(TRADE_DAYS)
        for hour, minute in ((15, 0), (18, 30)):
            day, _ = last_completed_trading_day(datetime(2026, 11, 3, hour, minute, tzinfo=CN))
            self.assertEqual(day, "2026-11-03")

    def test_weekend_returns_last_trading_day(self):
        self.seed(TRADE_DAYS)
        day, basis = last_completed_trading_day(datetime(2026, 11, 1, 9, 0, tzinfo=CN))
        self.assertEqual((day, basis), ("2026-10-30", "calendar"))

    def test_missing_calendar_falls_back_to_workday(self):
        trading_calendar._reset_memory()
        day, basis = last_completed_trading_day(datetime(2026, 11, 3, 10, 0, tzinfo=CN))
        self.assertEqual((day, basis), ("2026-11-02", "workday"))

    def test_workday_fallback_steps_back_over_weekend(self):
        trading_calendar._reset_memory()
        day, basis = last_completed_trading_day(datetime(2026, 11, 2, 10, 0, tzinfo=CN))
        self.assertEqual((day, basis), ("2026-10-30", "workday"))

    def test_calendar_refresh_skipped_when_it_covers_the_year(self):
        # 缓存覆盖到当年 12-31 就不再联网，避免每次启动都拉日历
        year_end = date(datetime.now(CN).year, 12, 31)
        self.seed(sorted(set(TRADE_DAYS) | {year_end}))
        trading_calendar._reset_memory()
        with mock.patch("akshare.tool_trade_date_hist_sina") as fetch:
            trading_calendar.refresh_calendar()
        fetch.assert_not_called()


class LagCountTest(CalendarTestCase):
    def test_counts_only_trading_days(self):
        self.seed(TRADE_DAYS)
        self.assertEqual(lag_trading_days("2026-10-30", "2026-11-02"), 1)
        self.assertEqual(lag_trading_days("2026-10-30", "2026-11-06"), 5)

    def test_no_lag_when_current_or_ahead(self):
        self.seed(TRADE_DAYS)
        self.assertEqual(lag_trading_days("2026-11-02", "2026-11-02"), 0)
        self.assertEqual(lag_trading_days("2026-11-03", "2026-11-02"), 0)
        self.assertEqual(lag_trading_days(None, "2026-11-02"), 0)

    def test_workday_fallback_counts_weekdays(self):
        trading_calendar._reset_memory()
        self.assertEqual(lag_trading_days("2026-10-30", "2026-11-06"), 5)


class DataGapTest(unittest.TestCase):
    def test_flags_only_lagging_lines(self):
        local = {"indices": "2026-11-02", "sectors": "2026-10-30", "institutions": "2026-11-02"}
        with mock.patch.object(bootstrap, "latest_local_dates", return_value=local), \
                mock.patch.object(bootstrap, "last_completed_trading_day",
                                  return_value=("2026-11-02", "calendar")), \
                mock.patch.object(bootstrap, "lag_trading_days",
                                  side_effect=lambda earlier, later: 1 if earlier == "2026-10-30" else 0):
            gap = bootstrap.data_gap()
        self.assertEqual(gap["stale"], ["sectors"])
        self.assertEqual(gap["lag"]["sectors"], 1)
        self.assertEqual(gap["lag"]["indices"], 0)

    def test_missing_line_counts_as_stale(self):
        local = {"indices": "2026-11-02", "sectors": None, "institutions": "2026-11-02"}
        with mock.patch.object(bootstrap, "latest_local_dates", return_value=local), \
                mock.patch.object(bootstrap, "last_completed_trading_day",
                                  return_value=("2026-11-02", "calendar")), \
                mock.patch.object(bootstrap, "lag_trading_days", return_value=0):
            gap = bootstrap.data_gap()
        self.assertEqual(gap["stale"], ["sectors"])


class AttemptStateTest(unittest.TestCase):
    def setUp(self):
        # ETF 是并列的第四条数据线：单测统一屏蔽，保证不触网
        patcher = mock.patch.object(bootstrap, "update_etf",
                                    return_value={"status": "ok", "updated": [],
                                                  "skipped": 6, "failed": []})
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_etf_refresh_is_merged_into_refreshed(self):
        gap = make_gap(["sectors"])
        etf = {"status": "ok", "updated": ["510300"], "skipped": 5, "failed": []}
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=False), \
                mock.patch.object(bootstrap, "data_gap", side_effect=[gap, gap]), \
                mock.patch.object(bootstrap, "_load_state", return_value={}), \
                mock.patch.object(bootstrap, "_save_state"), \
                mock.patch.object(bootstrap, "update_sectors", return_value=162), \
                mock.patch.object(bootstrap, "update_etf", return_value=etf):
            result = bootstrap.ensure_fresh_data()
        self.assertEqual(result["refreshed"]["etf"], etf)
        self.assertEqual(result["refreshed"]["sectors"], 162)

    def test_etf_failure_only_warns(self):
        gap = make_gap(["sectors"])
        failed = {"status": "failed", "error": "RuntimeError: 网络断开"}
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=False), \
                mock.patch.object(bootstrap, "data_gap", side_effect=[gap, gap]), \
                mock.patch.object(bootstrap, "_load_state", return_value={}), \
                mock.patch.object(bootstrap, "_save_state"), \
                mock.patch.object(bootstrap, "update_sectors", return_value=162), \
                mock.patch.object(bootstrap, "update_etf", return_value=failed):
            result = bootstrap.ensure_fresh_data()
        self.assertEqual(result["refreshed"]["sectors"], 162)
        self.assertEqual(result["refreshed"]["etf"]["status"], "failed")

    def test_data_gap_keeps_three_lines_only(self):
        """ETF 不得进入 stale 判定：快照与日志仍只描述三条主线。"""
        with mock.patch.object(bootstrap, "latest_local_dates",
                               return_value={"indices": "2026-11-02", "sectors": "2026-11-02",
                                             "institutions": "2026-11-02"}), \
                mock.patch.object(bootstrap, "last_completed_trading_day",
                                  return_value=("2026-11-02", "calendar")):
            real = bootstrap.data_gap()
        self.assertEqual(set(real["local"]), {"indices", "sectors", "institutions"})
        self.assertEqual(real["stale"], [])

    def test_records_keep_same_day_peers(self):
        day = date(2026, 11, 3).strftime("%Y-%m-%d")
        state = {"2026-11-02": {"tries": {"sectors": 3}}}
        bootstrap._record_attempt(state, day, "indices")
        bootstrap._record_attempt(state, day, "sectors")
        self.assertEqual(state, {day: {"tries": {"indices": 1, "sectors": 1}}})

    def test_done_marker_skips_when_expected_unchanged(self):
        day = datetime.now(trading_calendar.CN).strftime("%Y-%m-%d")
        gap = make_gap(["sectors"])
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=False), \
                mock.patch.object(bootstrap, "data_gap", return_value=gap), \
                mock.patch.object(bootstrap, "_load_state",
                                  return_value={day: {"done": {"sectors": "2026-11-02"},
                                                      "tries": {"sectors": 2}}}), \
                mock.patch.object(bootstrap, "update_sectors") as update:
            result = bootstrap.ensure_fresh_data()
        update.assert_not_called()
        self.assertEqual(result["refreshed"], {})
        self.assertEqual(result["skipped"], ["sectors"])

    def test_expected_change_invalidates_done_marker(self):
        # 15:00 后最近交易日推进，当天早先的“已补齐”不得继续阻止重试
        day = datetime.now(trading_calendar.CN).strftime("%Y-%m-%d")
        gap = make_gap(["sectors"], expected="2026-11-03")
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=False), \
                mock.patch.object(bootstrap, "data_gap", side_effect=[gap, gap]), \
                mock.patch.object(bootstrap, "_load_state",
                                  return_value={day: {"done": {"sectors": "2026-11-02"}}}), \
                mock.patch.object(bootstrap, "_save_state"), \
                mock.patch.object(bootstrap, "update_sectors", return_value=162) as update:
            bootstrap.ensure_fresh_data()
        update.assert_called_once()

    def test_failed_backfill_is_retried_on_next_startup(self):
        # 失败不计数： attempts 再多也不能阻止下一次启动重试
        day = datetime.now(trading_calendar.CN).strftime("%Y-%m-%d")
        gap = make_gap(["sectors"])
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=False), \
                mock.patch.object(bootstrap, "data_gap", side_effect=[gap, gap]), \
                mock.patch.object(bootstrap, "_load_state",
                                  return_value={day: {"done": {}, "tries": {"sectors": 99}}}), \
                mock.patch.object(bootstrap, "_save_state") as save, \
                mock.patch.object(bootstrap, "update_sectors", return_value=162) as update:
            result = bootstrap.ensure_fresh_data()
        update.assert_called_once()
        self.assertTrue(result["after"]["stale"])  # 仍落后，不算完成
        save.assert_called_once()
        self.assertEqual(save.call_args[0][0][day].get("done", {}), {})

    def test_success_records_done_marker(self):
        day = datetime.now(trading_calendar.CN).strftime("%Y-%m-%d")
        stale = make_gap(["sectors"])
        fresh = make_gap([], expected="2026-11-02")
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=False), \
                mock.patch.object(bootstrap, "data_gap", side_effect=[stale, fresh]), \
                mock.patch.object(bootstrap, "_load_state", return_value={}), \
                mock.patch.object(bootstrap, "_save_state") as save, \
                mock.patch.object(bootstrap, "update_sectors", return_value=162):
            bootstrap.ensure_fresh_data()
        self.assertEqual(save.call_args[0][0][day]["done"], {"sectors": "2026-11-02"})

    def test_ensure_fresh_data_refreshes_only_lagging_line(self):
        gap = make_gap(["sectors"])
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=False), \
                mock.patch.object(bootstrap, "data_gap", side_effect=[gap, gap]), \
                mock.patch.object(bootstrap, "_load_state", return_value={}), \
                mock.patch.object(bootstrap, "_save_state"), \
                mock.patch.object(bootstrap, "update_sectors", return_value=162) as sectors, \
                mock.patch.object(bootstrap, "update_indices") as indices:
            result = bootstrap.ensure_fresh_data()
        sectors.assert_called_once()
        indices.assert_not_called()
        self.assertEqual(result["refreshed"]["sectors"], 162)

    def test_skips_judgement_right_after_full_bootstrap(self):
        with mock.patch.object(bootstrap, "refresh_calendar"), \
                mock.patch.object(bootstrap, "ensure_raw_data", return_value=True), \
                mock.patch.object(bootstrap, "data_gap", return_value={"expected": "x"}), \
                mock.patch.object(bootstrap, "update_sectors") as sectors:
            result = bootstrap.ensure_fresh_data()
        sectors.assert_not_called()
        self.assertTrue(result["bootstrap"])


class TailDateTest(unittest.TestCase):
    def test_reads_last_row_date_by_column(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "801010.csv"
            path.write_text("代码,date,close\n801010,2026-10-29,1.0\n801010,2026-10-30,1.1\n",
                            encoding="utf-8-sig")
            self.assertEqual(bootstrap._tail_date(path, 1), "2026-10-30")

    def test_truncated_or_missing_file_returns_none(self):
        with tempfile.TemporaryDirectory() as name:
            path = Path(name) / "broken.csv"
            path.write_text("801010,2026", encoding="utf-8")
            self.assertIsNone(bootstrap._tail_date(path, 1))
            self.assertIsNone(bootstrap._tail_date(Path(name) / "missing.csv", 1))


if __name__ == "__main__":
    unittest.main()
