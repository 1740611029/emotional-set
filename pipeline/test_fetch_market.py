"""同花顺行业指数采集的回归测试：动态清单校验、增量日期段与列名统一。"""

from __future__ import annotations

import contextlib
import io
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import fetch_market as fm

# 测试模板：90 个合规同花顺细分行业（881101~881190），名称唯一且无首尾空白。
THS_TEST_UNIVERSE: list[tuple[str, str]] = [
    ("881101", "种植业与林业"),
    ("881102", "养殖业"),
    ("881103", "农产品加工"),
    ("881104", "渔业"),
    ("881105", "农业综合"),
    ("881106", "饲料"),
    ("881107", "煤炭开采加工"),
    ("881108", "石油加工贸易"),
    ("881109", "油气开采及服务"),
    ("881110", "化学原料"),
    ("881111", "化学制品"),
    ("881112", "化学纤维"),
    ("881113", "塑料制品"),
    ("881114", "橡胶制品"),
    ("881115", "非金属材料"),
    ("881116", "钢铁"),
    ("881117", "小金属"),
    ("881118", "工业金属"),
    ("881119", "贵金属"),
    ("881120", "金属新材料"),
    ("881121", "半导体"),
    ("881122", "光学光电子"),
    ("881123", "消费电子"),
    ("881124", "元件"),
    ("881125", "其他电子"),
    ("881126", "电子化学品"),
    ("881127", "通信设备"),
    ("881128", "通信服务"),
    ("881129", "计算机设备"),
    ("881130", "IT服务"),
    ("881131", "软件开发"),
    ("881132", "游戏"),
    ("881133", "互联网电商"),
    ("881134", "文化传媒"),
    ("881135", "影视院线"),
    ("881136", "造纸"),
    ("881137", "包装印刷"),
    ("881138", "家居用品"),
    ("881139", "纺织制造"),
    ("881140", "服装家纺"),
    ("881141", "美容护理"),
    ("881142", "厨卫电器"),
    ("881143", "白色家电"),
    ("881144", "黑色家电"),
    ("881145", "小家电"),
    ("881146", "电力设备"),
    ("881147", "电池"),
    ("881148", "光伏设备"),
    ("881149", "风电设备"),
    ("881150", "电机"),
    ("881151", "电网设备"),
    ("881152", "其他电源设备"),
    ("881153", "汽车整车"),
    ("881154", "汽车零部件"),
    ("881155", "汽车服务及其他"),
    ("881156", "公路铁路运输"),
    ("881157", "港口航运"),
    ("881158", "机场航运"),
    ("881159", "物流"),
    ("881160", "贸易"),
    ("881161", "零售"),
    ("881162", "旅游及酒店"),
    ("881163", "教育"),
    ("881164", "其他社会服务"),
    ("881165", "饮料制造"),
    ("881166", "食品加工制造"),
    ("881167", "白酒"),
    ("881168", "医药商业"),
    ("881169", "化学制药"),
    ("881170", "中药"),
    ("881171", "生物制品"),
    ("881172", "医疗器械"),
    ("881173", "医疗服务"),
    ("881174", "建筑材料"),
    ("881175", "建筑装饰"),
    ("881176", "工程机械"),
    ("881177", "轨交设备"),
    ("881178", "通用设备"),
    ("881179", "自动化设备"),
    ("881180", "专用设备"),
    ("881181", "环保设备"),
    ("881182", "环境治理"),
    ("881183", "美容"),
    ("881184", "证券"),
    ("881185", "保险"),
    ("881186", "多元金融"),
    ("881187", "银行"),
    ("881188", "房地产"),
    ("881189", "燃气"),
    ("881190", "综合"),
]


def ths_spot(**overrides) -> dict[str, str]:
    """构造一份同花顺接口清单（90 个细分行业），可按需破坏。"""
    spot = dict(THS_TEST_UNIVERSE)
    for code, value in overrides.items():
        if value is None:
            spot.pop(code, None)
        else:
            spot[code] = value
    return spot


class CheckUniverseTest(unittest.TestCase):
    def test_ok_with_full_dynamic_universe(self):
        boards, snapshot = fm.check_sector_universe(ths_spot())
        self.assertEqual(len(boards), 90)
        self.assertEqual(snapshot["接口板块总数"], 90)
        self.assertEqual(snapshot["生产清单板块数"], 90)
        self.assertEqual(snapshot["未入列板块数"], 0)

    def test_empty_spot_raises(self):
        with self.assertRaises(ValueError) as ctx:
            fm.check_sector_universe({})
        self.assertIn("为空", str(ctx.exception))

    def test_too_few_industries_raises(self):
        spot = dict(ths_spot())
        for code in list(spot)[:31]:  # 只留 59 个，低于 THS_MIN_UNIVERSE
            spot.pop(code, None)
        self.assertEqual(len(spot), 59)
        with self.assertRaises(ValueError) as ctx:
            fm.check_sector_universe(spot)
        self.assertIn("少于", str(ctx.exception))

    def test_invalid_code_raises(self):
        with self.assertRaises(ValueError) as ctx:
            fm.check_sector_universe(ths_spot(**{"881x01": "测试行业"}))
        self.assertIn("6 位数字", str(ctx.exception))

    def test_blank_or_padded_name_raises(self):
        with self.assertRaises(ValueError) as ctx:
            fm.check_sector_universe(ths_spot(**{"882000": " 测试行业"}))
        self.assertIn("首尾空白", str(ctx.exception))

    def test_duplicate_name_raises(self):
        with self.assertRaises(ValueError) as ctx:
            fm.check_sector_universe(ths_spot(**{"882001": "半导体"}))
        self.assertIn("重复", str(ctx.exception))


class RequestWindowTest(unittest.TestCase):
    def test_no_cache_falls_back_to_full_history(self):
        self.assertEqual(fm.sector_request_start(None), fm.FIRST_FETCH_START)
        self.assertEqual(fm.FIRST_FETCH_START, "20201030")

    def test_start_includes_local_tail_for_self_healing(self):
        """起点含尾日：尾日那根 bar 每轮都被重取，收盘瞬间未定稿的量额能在下一轮被覆盖。"""
        self.assertEqual(fm.sector_request_start("2026-11-13"), "20261113")


class FetchSectorsEndpointTest(unittest.TestCase):
    """全量路径的收盘定稿与不降级：终点必须来自交易日历，校验失败不落盘。"""

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.raw = Path(self.temp.name) / "sectors-ths-industry"
        self.manifest = Path(self.temp.name) / "sectors-ths-industry-manifest.json"
        self.raw_patch = mock.patch.object(fm, "THS_SECTOR_RAW", self.raw)
        self.manifest_patch = mock.patch.object(
            fm, "THS_SECTOR_MANIFEST", self.manifest
        )
        # manifest 记录相对 ROOT 的路径，临时目录必须整体挂在同一个根下
        self.root_patch = mock.patch.object(fm, "ROOT", Path(self.temp.name))
        for patch in (self.raw_patch, self.manifest_patch, self.root_patch):
            patch.start()
            self.addCleanup(patch.stop)

    def frame(self, day: str = "2026-11-13") -> pd.DataFrame:
        return pd.DataFrame(
            {
                column: ([day] if column == "date" else [1.0])
                for column in fm.SECTOR_COLUMNS
            }
        )

    def run_fetch(
        self,
        hist,
        calendar_day: str = "2026-11-13",
        end_date: str | None = None,
        **overrides,
    ) -> list[tuple[str, str]]:
        captured: list[tuple[str, str]] = []

        def record(symbol: str, start_date: str, stop_date: str) -> pd.DataFrame:
            captured.append((start_date, stop_date))
            return hist

        spot = ths_spot(**overrides)
        with (
            mock.patch.object(fm, "sector_spot", return_value=spot),
            mock.patch.object(fm, "fetch_sector_hist_ths", side_effect=record),
            mock.patch.object(
                fm,
                "last_completed_trading_day",
                return_value=(calendar_day, "calendar"),
            ),
            mock.patch.object(fm.time, "sleep"),
        ):  # 分批节奏限流控制，测试不等真实间隔
            with contextlib.redirect_stdout(io.StringIO()):
                fm.fetch_sectors(end_date)
        return captured

    def test_default_end_is_last_completed_trading_day(self):
        """盘中初始化也不能把当日未完成 bar 当收盘数据写入缓存。"""
        captured = self.run_fetch(self.frame(), calendar_day="2026-11-13")
        self.assertEqual(len(captured), 90)
        self.assertEqual({end for _, end in captured}, {"20261113"})
        self.assertEqual({start for start, _ in captured}, {fm.FIRST_FETCH_START})
        self.assertEqual(self.manifest.exists(), True)
        self.assertEqual(len(list(self.raw.glob("*.csv"))), 90)
        manifest = __import__("json").loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(manifest["universe"]), 90)
        self.assertEqual(len(manifest["boards"]), 90)

    def test_explicit_end_date_is_honoured_for_backfill(self):
        """回填演练可显式指定终点，此时不采用日历口径。"""
        captured = self.run_fetch(self.frame("2020-12-31"), end_date="20201231")
        self.assertEqual({stop for _, stop in captured}, {"20201231"})

    def test_universe_check_failure_writes_nothing(self):
        """清单校验不过时既不请求也不落盘，保留上一版数据。"""
        with self.assertRaises(ValueError):
            self.run_fetch(self.frame(), **{"881001": " 空格行业"})
        self.assertFalse(self.raw.exists() and any(self.raw.glob("*.csv")))
        self.assertFalse(self.manifest.exists())


class FetchSectorHistThsTest(unittest.TestCase):
    def frame(self) -> pd.DataFrame:
        return pd.DataFrame(
            {
                "日期": ["2026-11-13", "2026-11-12", "2026-11-12"],
                "开盘价": [10.0, 9.0, 9.5],
                "收盘价": [11.0, 9.2, 9.4],
                "最高价": [12.0, 9.6, 9.6],
                "最低价": [9.8, 8.8, 8.8],
                "成交量": [1000, 900, 950],
                "成交额": [1e6, 9e5, 9.5e5],
            }
        )

    def test_columns_renamed_sorted_and_deduplicated(self):
        with mock.patch.object(
            fm.ak, "stock_board_industry_index_ths", return_value=self.frame()
        ):
            frame = fm.fetch_sector_hist_ths("半导体", "20220201", "20261113")
        self.assertEqual(frame.columns.tolist(), fm.SECTOR_COLUMNS)
        self.assertEqual(frame["date"].tolist(), ["2026-11-12", "2026-11-13"])
        self.assertEqual(list(frame["open"]), [9.5, 10.0])  # 重复日期保留后一条
        self.assertTrue(frame["close"].notna().all())

    def test_request_uses_name_and_year_end(self):
        """口径固定：symbol 传行业名称，end_date 换算为当年年末（避免 akshare 切片截断）。"""
        with mock.patch.object(
            fm.ak, "stock_board_industry_index_ths", return_value=self.frame()
        ) as patched:
            fm.fetch_sector_hist_ths("半导体", "20220201", "20261113")
        kwargs = patched.call_args.kwargs
        self.assertEqual(kwargs["symbol"], "半导体")
        self.assertEqual(kwargs["start_date"], "20220201")
        self.assertEqual(kwargs["end_date"], "20261231")

    def test_out_of_range_dates_are_filtered(self):
        """年末取值后只保留业务终点（end_date）之前的数据。"""
        frame = self.frame()
        with mock.patch.object(
            fm.ak, "stock_board_industry_index_ths", return_value=frame
        ):
            got = fm.fetch_sector_hist_ths("半导体", "20220201", "20261112")
        self.assertEqual(got["date"].tolist(), ["2026-11-12"])

    def test_all_missing_prices_leave_no_rows(self):
        broken = self.frame().assign(收盘价=[float("nan")] * 3)
        with mock.patch.object(
            fm.ak, "stock_board_industry_index_ths", return_value=broken
        ):
            with self.assertRaises(ValueError) as ctx:
                fm.fetch_sector_hist_ths("半导体", "20220201", "20261113")
        self.assertIn("无日线", str(ctx.exception))

    def test_missing_field_raises(self):
        broken = self.frame().drop(columns=["成交量"])
        with mock.patch.object(
            fm.ak, "stock_board_industry_index_ths", return_value=broken
        ):
            with self.assertRaises(ValueError) as ctx:
                fm.fetch_sector_hist_ths("半导体", "20220201", "20261113")
        self.assertIn("volume", str(ctx.exception))


if __name__ == "__main__":
    unittest.main()
