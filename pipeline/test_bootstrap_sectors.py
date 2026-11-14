"""板块增量补齐的回归测试：收盘定稿、已覆盖不请求、清单校验失败不写盘。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import bootstrap as bs


def frame(days: list[str]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "date": days,
            "open": [100.0 + i for i in range(len(days))],
            "close": [101.0 + i for i in range(len(days))],
            "high": [102.0 + i for i in range(len(days))],
            "low": [99.0 + i for i in range(len(days))],
            "volume": [1000.0] * len(days),
            "amount": [1e6] * len(days),
        }
    )


# 生产清单占用的 code：模板生成时跳过，避免接口清单与生产清单叠加后总数虚增
THS_RESERVED_CODES = {"881121", "881273"}


def ths_template(count: int = 90) -> dict[str, str]:
    """构造与同花顺接口一致的合规细分行业清单（881100 起，跳过生产清单占用的 code，名称唯一）。"""
    template: dict[str, str] = {}
    i = 0
    while len(template) < count:
        code = f"88{1100 + i}"
        if code not in THS_RESERVED_CODES:
            template[code] = f"测试行业{len(template):03d}"
        i += 1
    return template


class SectorUpdateTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.raw = self.directory / "sectors-ths-industry"
        self.raw.mkdir(parents=True)
        self.manifest_path = self.directory / "sectors-ths-industry-manifest.json"
        self.boards = [
            {"code": "881121", "name": "半导体"},
            {"code": "881273", "name": "白酒"},
        ]
        self.manifest_path.write_text(
            json.dumps(
                {
                    "source": "同花顺行业指数（不复权）",
                    "universe": list(self.boards),
                    "universe_snapshot": {
                        "接口板块总数": 90,
                        "生产清单板块数": 90,
                        "未入列板块数": 0,
                    },
                    "universe_changes": [],
                    "boards": [
                        dict(
                            board,
                            rows=100,
                            start="2026-10-01",
                            end="2026-11-02",
                            insufficient_history=False,
                        )
                        for board in self.boards
                    ],
                },
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        for board in self.boards:
            frame(["2026-10-30"]).to_csv(
                self.raw / f"{board['code']}.csv", index=False, encoding="utf-8-sig"
            )
        self.patches = [
            mock.patch.object(bs, "THS_SECTOR_RAW", self.raw),
            mock.patch.object(bs, "THS_SECTOR_MANIFEST", self.manifest_path),
            # 分批节奏限流控制由 fetch_market 侧生效，这里打掉 sleep 让回归测试秒级完成
            mock.patch.object(bs.time, "sleep"),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temp.cleanup)

    def patch_calendar(self, expected: str):
        return mock.patch.object(
            bs, "last_completed_trading_day", return_value=(expected, "calendar")
        )

    def patch_spot(self, total: int = 90):
        """返回同花顺动态清单（默认 90 个全量，可通过 total 制造总数变化）。"""
        spot = ths_template(total - len(self.boards))
        # 保证生产清单中的两只都在接口清单内（改名/下架场景由 fetch_market 侧测试覆盖）
        spot.update({board["code"]: board["name"] for board in self.boards})
        return mock.patch.object(bs, "sector_spot", return_value=spot)


class CoveredBoardTest(SectorUpdateTestCase):
    def test_no_request_when_local_tail_covers_expected_day(self):
        with (
            self.patch_calendar("2026-10-30"),
            self.patch_spot(),
            mock.patch.object(bs, "fetch_sector_hist_ths") as fetch,
        ):
            changed = bs.update_sectors()
        self.assertEqual(changed, 0)
        fetch.assert_not_called()

    def test_stale_board_beyond_threshold_is_skipped(self):
        """落后超过停更阈值的板块不再请求，避免拖死整轮补齐。"""
        with (
            self.patch_calendar("2026-11-03"),
            self.patch_spot(),
            mock.patch.object(
                bs, "lag_trading_days", return_value=bs.STALE_SECTOR_LAG_DAYS + 1
            ),
            mock.patch.object(bs, "fetch_sector_hist_ths") as fetch,
        ):
            changed = bs.update_sectors()
        self.assertEqual(changed, 0)
        fetch.assert_not_called()


class ClosingBarTest(SectorUpdateTestCase):
    def test_request_window_restarts_at_local_tail(self):
        """起点含尾日：每轮重取最后一根 bar，为未定稿数值留出自愈入口；请求按名称而非代码。"""
        with (
            self.patch_calendar("2026-11-02"),
            self.patch_spot(),
            mock.patch.object(
                bs,
                "fetch_sector_hist_ths",
                return_value=frame(["2026-10-30", "2026-11-02"]),
            ) as fetch,
        ):
            changed = bs.update_sectors()
        self.assertEqual(changed, 2)
        self.assertEqual(
            fetch.call_args_list[0].args[0], "半导体"
        )  # 首参为行业名称（按 boards 顺序首次即 881121）
        self.assertEqual(
            fetch.call_args_list[0].args[1], "20261030"
        )  # 起点：本地尾日（含）
        self.assertEqual(
            fetch.call_args_list[0].args[2], "20261102"
        )  # 终点：最近已完成交易日

    def test_intraday_bar_is_never_written(self):
        """15:00 前 expected 仍为上一交易日：当日盘中 bar 不进入请求区间也不落盘。"""
        with (
            self.patch_calendar("2026-11-02"),
            self.patch_spot(),
            mock.patch.object(
                bs,
                "fetch_sector_hist_ths",
                return_value=frame(["2026-10-31", "2026-11-02"]),
            ),
        ):
            bs.update_sectors()
        saved = pd.read_csv(self.raw / "881121.csv", dtype={"date": str})
        self.assertEqual(saved["date"].iat[-1], "2026-11-02")
        self.assertNotIn("2026-11-03", saved["date"].tolist())
        self.assertEqual(bs._tail_date(self.raw / "881121.csv", 0), "2026-11-02")

    def test_closed_day_is_persisted_and_manifest_updated(self):
        with (
            self.patch_calendar("2026-11-03"),
            self.patch_spot(),
            mock.patch.object(
                bs, "fetch_sector_hist_ths", return_value=frame(["2026-11-03"])
            ),
        ):
            changed = bs.update_sectors()
        self.assertEqual(changed, 2)
        self.assertEqual(bs._tail_date(self.raw / "881121.csv", 0), "2026-11-03")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual({row["end"] for row in manifest["boards"]}, {"2026-11-03"})


class MergeAndGuardTest(SectorUpdateTestCase):
    def test_merge_dedupes_and_keeps_latest(self):
        with (
            self.patch_calendar("2026-11-03"),
            self.patch_spot(),
            mock.patch.object(
                bs,
                "fetch_sector_hist_ths",
                return_value=frame(["2026-10-30", "2026-11-03"]),
            ),
        ):
            bs.update_sectors()
        saved = pd.read_csv(self.raw / "881121.csv", dtype={"date": str})
        self.assertEqual(saved["date"].tolist(), ["2026-10-30", "2026-11-03"])
        self.assertEqual(len(saved.drop_duplicates("date")), len(saved))

    def test_universe_check_failure_blocks_fetch_and_write(self):
        """清单校验不通过时报错，不请求也不改产物，保留上一版数据。"""
        manifest_before = self.manifest_path.read_text(encoding="utf-8")
        broken = ths_template(90)
        broken["881121"] = " 半导体"  # 首尾空白 → check_sector_universe 报错
        with (
            self.patch_calendar("2026-11-03"),
            mock.patch.object(bs, "sector_spot", return_value=broken),
            mock.patch.object(bs, "fetch_sector_hist_ths") as fetch,
        ):
            with self.assertRaises(RuntimeError):
                bs.update_sectors()
        fetch.assert_not_called()
        self.assertEqual(
            self.manifest_path.read_text(encoding="utf-8"), manifest_before
        )
        self.assertEqual(bs._tail_date(self.raw / "881121.csv", 0), "2026-10-30")

    def test_single_board_failure_does_not_block_others(self):
        with (
            self.patch_calendar("2026-11-03"),
            self.patch_spot(),
            mock.patch.object(
                bs,
                "fetch_sector_hist_ths",
                side_effect=[RuntimeError("接口超时"), frame(["2026-11-03"])],
            ) as fetch,
        ):
            changed = bs.update_sectors()
        self.assertEqual(changed, 1)
        self.assertEqual(fetch.call_count, 2)

    def test_universe_total_change_is_recorded_without_joining_list(self):
        """接口行业总数变化只留痕，生产清单不自动扩充。"""
        with (
            self.patch_calendar("2026-11-03"),
            self.patch_spot(total=92),
            mock.patch.object(
                bs, "fetch_sector_hist_ths", return_value=frame(["2026-11-03"])
            ),
        ):
            bs.update_sectors()
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["universe_snapshot"]["接口板块总数"], 92)
        self.assertEqual(len(manifest["universe_changes"]), 1)
        self.assertEqual(len(manifest["boards"]), 2)


class TailRevisionTest(SectorUpdateTestCase):
    """起点含尾日后的写盘判定：只看数值真有没有变，不能只看行数与尾日。"""

    def test_revised_tail_bar_is_rewritten(self):
        """尾日 bar 在下一轮拿到修正后的量额时必须覆盖落盘（自愈）。"""
        revised = frame(["2026-10-30"])
        revised.loc[0, "volume"] = 12345.0
        with (
            self.patch_calendar("2026-11-03"),
            self.patch_spot(),
            mock.patch.object(bs, "fetch_sector_hist_ths", return_value=revised),
        ):
            changed = bs.update_sectors()
        self.assertEqual(changed, 2)
        saved = pd.read_csv(self.raw / "881121.csv", dtype={"date": str})
        self.assertEqual(saved["date"].tolist(), ["2026-10-30"])
        self.assertEqual(float(saved["volume"].iat[0]), 12345.0)

    def test_identical_repull_is_not_rewritten(self):
        """逐值一致时不写盘也不计为变化，避免每轮无谓重建。"""
        stamp = (self.raw / "881121.csv").stat().st_mtime_ns
        with (
            self.patch_calendar("2026-11-03"),
            self.patch_spot(),
            mock.patch.object(
                bs, "fetch_sector_hist_ths", return_value=frame(["2026-10-30"])
            ),
        ):
            changed = bs.update_sectors()
        self.assertEqual(changed, 0)
        self.assertEqual((self.raw / "881121.csv").stat().st_mtime_ns, stamp)


class EtfFreshnessGapTest(unittest.TestCase):
    """ETF 数据线接入 data_gap（D4）：stale 逐线列出，不牵连其余三条线。"""

    def test_etf_only_lag_marks_etf_stale_alone(self):
        local = {
            "indices": "2026-11-03",
            "sectors": "2026-11-03",
            "institutions": "2026-11-03",
            "etf": "2026-10-30",
        }
        with (
            mock.patch.object(bs, "latest_local_dates", return_value=local),
            mock.patch.object(
                bs,
                "last_completed_trading_day",
                return_value=("2026-11-03", "calendar"),
            ),
        ):
            gap = bs.data_gap()
        self.assertEqual(gap["stale"], ["etf"])
        self.assertEqual(
            gap["lag"]["etf"], 2
        )  # 10-30 → 11-03 之间有 11-02、11-03 两个交易日
        self.assertEqual(gap["lag"]["indices"], 0)
        self.assertEqual(gap["lag"]["sectors"], 0)
        self.assertEqual(gap["lag"]["institutions"], 0)

    def test_etf_min_ready_date_is_used(self):
        """ETF 取 6 只尾日的最小齐备日：一只落后即整体齐备日落后。"""
        tails = {
            "510300": "2026-11-03",
            "510050": "2026-10-30",
            "588000": "2026-11-03",
            "159915": "2026-11-03",
            "512100": "2026-11-03",
            "510500": "2026-11-03",
        }
        codes = list(tails)
        with (
            mock.patch.object(
                bs, "_tail_date", side_effect=lambda path, col: tails[path.stem]
            ),
            mock.patch.object(bs, "ETF_UNIVERSE", [{"code": code} for code in codes]),
        ):
            ready = bs._etf_ready_date()
        self.assertEqual(ready, "2026-10-30")

    def test_missing_etf_file_reports_none(self):
        with mock.patch.object(bs, "_tail_date", return_value=None):
            self.assertIsNone(bs._etf_ready_date())


if __name__ == "__main__":
    unittest.main()
