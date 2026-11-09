"""东财行业板块采集的回归测试：固定清单校验、增量日期段与列名统一。"""
from __future__ import annotations

import contextlib
import io
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import fetch_market as fm


def spot_payload(**overrides) -> dict[str, str]:
    """构造一份与硬编码清单一致的东财接口清单，可按需破坏。"""
    spot = {code: name for code, name in fm.EM_SECTOR_UNIVERSE}
    for code, value in overrides.items():
        if value is None:
            spot.pop(code, None)
        else:
            spot[code] = value
    return spot


class UniverseTest(unittest.TestCase):
    def test_universe_has_31_unique_codes_and_names(self):
        codes = [code for code, _ in fm.EM_SECTOR_UNIVERSE]
        names = [name for _, name in fm.EM_SECTOR_UNIVERSE]
        self.assertEqual(len(codes), 31)
        self.assertEqual(len(set(codes)), 31)
        self.assertEqual(len(set(names)), 31)
        self.assertTrue(all(re.fullmatch(r"BK\d{4}", code) for code in codes))

    def test_universe_excludes_subdivision_boards(self):
        """生产清单只允许单层行业，带 Ⅱ/Ⅲ 后缀的细分板块不得混入。"""
        self.assertFalse(any("Ⅱ" in name or "Ⅲ" in name for _, name in fm.EM_SECTOR_UNIVERSE))


class CheckUniverseTest(unittest.TestCase):
    def test_ok_and_records_extra_boards_without_joining_list(self):
        spot = spot_payload()
        spot["BK9999"] = "涂料"  # 东财三级细分：只留痕，不入列
        boards, snapshot = fm.check_sector_universe(spot)
        self.assertEqual(len(boards), 31)
        self.assertEqual(snapshot["接口板块总数"], 32)
        self.assertEqual(snapshot["生产清单板块数"], 31)
        self.assertEqual(snapshot["未入列板块数"], 1)

    def test_renamed_board_raises_instead_of_remapping(self):
        spot = spot_payload(**{"BK0433": "农林牧渔Ⅱ"})
        with self.assertRaises(ValueError) as ctx:
            fm.check_sector_universe(spot)
        self.assertIn("BK0433", str(ctx.exception))

    def test_missing_board_raises(self):
        spot = spot_payload(**{"BK1283": None})
        with self.assertRaises(ValueError) as ctx:
            fm.check_sector_universe(spot)
        self.assertIn("已查不到", str(ctx.exception))


class RequestWindowTest(unittest.TestCase):
    def test_no_cache_falls_back_to_full_history(self):
        self.assertEqual(fm.sector_request_start(None), fm.FIRST_FETCH_START)

    def test_start_includes_local_tail_for_self_healing(self):
        """起点含尾日：尾日那根 bar 每轮都被重取，收盘瞬间未定稿的量额能在下一轮被覆盖。"""
        self.assertEqual(fm.sector_request_start("2026-11-03"), "20261103")

    def test_tail_on_expected_day_gives_single_day_window(self):
        """尾日已到终点时区间退化为一天，不产生反向区间。"""
        start = fm.sector_request_start("2026-11-03")
        self.assertEqual(start, "2026-11-03".replace("-", ""))


class FetchSectorsEndpointTest(unittest.TestCase):
    """全量路径的收盘定稿与不降级：终点必须来自交易日历，校验失败不落盘。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name) / "sectors-em-industry"
        self.manifest = Path(self.temp.name) / "sectors-em-industry-manifest.json"
        self.raw_patch = mock.patch.object(fm, "EM_SECTOR_RAW", self.raw)
        self.manifest_patch = mock.patch.object(fm, "EM_SECTOR_MANIFEST", self.manifest)
        # manifest 记录相对 ROOT 的路径，临时目录必须整体挂在同一个根下
        self.root_patch = mock.patch.object(fm, "ROOT", Path(self.temp.name))
        for patch in (self.raw_patch, self.manifest_patch, self.root_patch):
            patch.start()
            self.addCleanup(patch.stop)

    def frame(self, day: str = "2026-11-03") -> pd.DataFrame:
        return pd.DataFrame({column: ([day] if column == "date" else [1.0])
                             for column in fm.SECTOR_COLUMNS})

    def run_fetch(self, hist, calendar_day: str = "2026-11-03", end_date: str | None = None,
                  **overrides) -> list[tuple[str, str]]:
        captured: list[tuple[str, str]] = []

        def record(symbol: str, start_date: str, stop_date: str) -> pd.DataFrame:
            captured.append((start_date, stop_date))
            return hist

        spot = {code: name for code, name in fm.EM_SECTOR_UNIVERSE} | overrides
        with mock.patch.object(fm, "sector_spot", return_value=spot), \
                mock.patch.object(fm, "fetch_sector_hist_em", side_effect=record), \
                mock.patch.object(fm, "last_completed_trading_day",
                                  return_value=(calendar_day, "calendar")):
            with contextlib.redirect_stdout(io.StringIO()):
                fm.fetch_sectors(end_date)
        return captured

    def test_default_end_is_last_completed_trading_day(self):
        """盘中初始化也不能把当日未完成 bar 当收盘数据写入缓存。"""
        captured = self.run_fetch(self.frame(), calendar_day="2026-11-03")
        self.assertEqual(len(captured), 31)
        self.assertEqual({end for _, end in captured}, {"20261103"})
        self.assertEqual({start for start, _ in captured}, {fm.FIRST_FETCH_START})
        self.assertEqual(self.manifest.exists(), True)
        self.assertEqual(len(list(self.raw.glob("*.csv"))), 31)

    def test_explicit_end_date_is_honoured_for_backfill(self):
        """回填演练可显式指定终点，此时不采用日历口径。"""
        captured = self.run_fetch(self.frame("2020-12-31"), end_date="20201231")
        self.assertEqual({stop for _, stop in captured}, {"20201231"})

    def test_universe_check_failure_writes_nothing(self):
        """清单校验不过时既不请求也不落盘，保留上一版数据。"""
        with self.assertRaises(ValueError):
            self.run_fetch(self.frame(), BK0433="农林牧渔Ⅱ")
        self.assertFalse(self.raw.exists() and any(self.raw.glob("*.csv")))
        self.assertFalse(self.manifest.exists())


class FetchSectorHistEmTest(unittest.TestCase):
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame({
            "日期": ["2026-11-03", "2026-11-02", "2026-11-02"],
            "开盘": [10.0, 9.0, 9.5], "收盘": [11.0, 9.2, 9.4],
            "最高": [12.0, 9.6, 9.6], "最低": [9.8, 8.8, 8.8],
            "涨跌幅": [1.0, 0.5, 0.6], "涨跌额": [0.1, 0.05, 0.06],
            "成交量": [1000, 900, 950], "成交额": [1e6, 9e5, 9.5e5],
            "振幅": [1.0, 0.8, 0.9], "换手率": [0.5, 0.4, 0.45],
        })

    def test_columns_renamed_sorted_and_deduplicated(self):
        with mock.patch.object(fm.ak, "stock_board_industry_hist_em", return_value=self.frame()):
            frame = fm.fetch_sector_hist_em("BK0433", "20261101", "20261103")
        self.assertEqual(frame.columns.tolist(), fm.SECTOR_COLUMNS)
        self.assertEqual(frame["date"].tolist(), ["2026-11-02", "2026-11-03"])
        self.assertEqual(list(frame["open"]), [9.5, 10.0])  # 重复日期保留后一条
        self.assertTrue(frame["close"].notna().all())

    def test_all_missing_prices_leave_no_rows(self):
        broken = self.frame().assign(收盘=[float("nan")] * 3)
        with mock.patch.object(fm.ak, "stock_board_industry_hist_em", return_value=broken):
            with self.assertRaises(ValueError) as ctx:
                fm.fetch_sector_hist_em("BK0433", "20261101", "20261103")
        self.assertIn("无日线", str(ctx.exception))

    def test_missing_field_raises(self):
        broken = self.frame().drop(columns=["成交量"])
        with mock.patch.object(fm.ak, "stock_board_industry_hist_em", return_value=broken):
            with self.assertRaises(ValueError) as ctx:
                fm.fetch_sector_hist_em("BK0433", "20261101", "20261103")
        self.assertIn("volume", str(ctx.exception))

    def test_request_uses_board_code_and_no_adjust(self):
        """口径固定：不复权，且按 BK 代码请求，不按名称查表。"""
        with mock.patch.object(fm.ak, "stock_board_industry_hist_em",
                               return_value=self.frame()) as patched:
            fm.fetch_sector_hist_em("BK0433", "20261101", "20261103")
        kwargs = patched.call_args.kwargs
        self.assertEqual(kwargs["symbol"], "BK0433")
        self.assertEqual(kwargs["adjust"], "")
        self.assertEqual(kwargs["period"], "日k")


if __name__ == "__main__":
    unittest.main()
