"""板块增量补齐的回归测试：收盘定稿、已覆盖不请求、清单校验失败不写盘。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import bootstrap as bs
import fetch_market as fm


def frame(days: list[str]) -> pd.DataFrame:
    return pd.DataFrame({
        "date": days,
        "open": [100.0 + i for i in range(len(days))],
        "close": [101.0 + i for i in range(len(days))],
        "high": [102.0 + i for i in range(len(days))],
        "low": [99.0 + i for i in range(len(days))],
        "volume": [1000.0] * len(days),
        "amount": [1e6] * len(days),
    })


class SectorUpdateTestCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.directory = Path(self.temp.name)
        self.raw = self.directory / "sectors-em-industry"
        self.raw.mkdir(parents=True)
        self.manifest_path = self.directory / "sectors-em-industry-manifest.json"
        self.boards = [{"code": "BK0433", "name": "农林牧渔"}, {"code": "BK0479", "name": "钢铁"}]
        self.manifest_path.write_text(json.dumps({
            "source": fm.SECTOR_SOURCE,
            "universe": list(self.boards),
            "universe_snapshot": {"接口板块总数": 496, "生产清单板块数": 31, "未入列板块数": 465},
            "universe_changes": [],
            "boards": [dict(board, rows=100, start="2026-10-01", end="2026-11-02",
                            insufficient_history=False) for board in self.boards],
        }, ensure_ascii=False), encoding="utf-8")
        for board in self.boards:
            frame(["2026-10-30"]).to_csv(self.raw / f"{board['code']}.csv", index=False, encoding="utf-8-sig")
        self.patches = [
            mock.patch.object(bs, "EM_SECTOR_RAW", self.raw),
            mock.patch.object(bs, "EM_SECTOR_MANIFEST", self.manifest_path),
        ]
        for patch in self.patches:
            patch.start()
            self.addCleanup(patch.stop)
        self.addCleanup(self.temp.cleanup)

    def patch_calendar(self, expected: str):
        return mock.patch.object(bs, "last_completed_trading_day",
                                 return_value=(expected, "calendar"))

    def patch_spot(self, extra: int = 1):
        """返回包含全 31 个硬编码代码的接口清单，另加几个未入列的细分板块。"""
        spot = dict(fm.EM_SECTOR_UNIVERSE)
        for index in range(extra):
            spot[f"BK9{index:03d}"] = f"测试细分{index}"
        return mock.patch.object(bs, "sector_spot", return_value=spot)


class CoveredBoardTest(SectorUpdateTestCase):
    def test_no_request_when_local_tail_covers_expected_day(self):
        with self.patch_calendar("2026-10-30"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em") as fetch:
            changed = bs.update_sectors()
        self.assertEqual(changed, 0)
        fetch.assert_not_called()

    def test_stale_board_beyond_threshold_is_skipped(self):
        """落后超过停更阈值的板块不再请求，避免拖死整轮补齐。"""
        with self.patch_calendar("2026-11-03"), self.patch_spot(), \
                mock.patch.object(bs, "lag_trading_days", return_value=bs.STALE_SECTOR_LAG_DAYS + 1), \
                mock.patch.object(bs, "fetch_sector_hist_em") as fetch:
            changed = bs.update_sectors()
        self.assertEqual(changed, 0)
        fetch.assert_not_called()


class ClosingBarTest(SectorUpdateTestCase):
    def test_request_window_restarts_at_local_tail(self):
        """起点含尾日：每轮重取最后一根 bar，为未定稿数值留出自愈入口。"""
        with self.patch_calendar("2026-11-02"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em",
                                  return_value=frame(["2026-10-30", "2026-11-02"])) as fetch:
            changed = bs.update_sectors()
        self.assertEqual(changed, 2)
        self.assertEqual(fetch.call_args.args[1], "20261030")  # 起点：本地尾日（含）
        self.assertEqual(fetch.call_args.args[2], "20261102")  # 终点：最近已完成交易日

    def test_intraday_bar_is_never_written(self):
        """15:00 前 expected 仍为上一交易日：当日盘中 bar 不进入请求区间也不落盘。"""
        with self.patch_calendar("2026-11-02"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em",
                                  return_value=frame(["2026-10-31", "2026-11-02"])):
            bs.update_sectors()
        saved = pd.read_csv(self.raw / "BK0433.csv", dtype={"date": str})
        self.assertEqual(saved["date"].iat[-1], "2026-11-02")
        self.assertNotIn("2026-11-03", saved["date"].tolist())
        self.assertEqual(bs._tail_date(self.raw / "BK0433.csv", 0), "2026-11-02")

    def test_closed_day_is_persisted_and_manifest_updated(self):
        with self.patch_calendar("2026-11-03"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em",
                                  return_value=frame(["2026-11-03"])):
            changed = bs.update_sectors()
        self.assertEqual(changed, 2)
        self.assertEqual(bs._tail_date(self.raw / "BK0433.csv", 0), "2026-11-03")
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual({row["end"] for row in manifest["boards"]}, {"2026-11-03"})


class MergeAndGuardTest(SectorUpdateTestCase):
    def test_merge_dedupes_and_keeps_latest(self):
        with self.patch_calendar("2026-11-03"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em",
                                  return_value=frame(["2026-10-30", "2026-11-03"])):
            bs.update_sectors()
        saved = pd.read_csv(self.raw / "BK0433.csv", dtype={"date": str})
        self.assertEqual(saved["date"].tolist(), ["2026-10-30", "2026-11-03"])
        self.assertEqual(len(saved.drop_duplicates("date")), len(saved))

    def test_universe_check_failure_blocks_fetch_and_write(self):
        """清单校验不通过时报错，不请求也不改产物，保留上一版数据。"""
        manifest_before = self.manifest_path.read_text(encoding="utf-8")
        broken = dict(fm.EM_SECTOR_UNIVERSE)
        broken["BK0433"] = "农林牧渔Ⅱ"
        with self.patch_calendar("2026-11-03"), \
                mock.patch.object(bs, "sector_spot", return_value=broken), \
                mock.patch.object(bs, "fetch_sector_hist_em") as fetch:
            with self.assertRaises(RuntimeError):
                bs.update_sectors()
        fetch.assert_not_called()
        self.assertEqual(self.manifest_path.read_text(encoding="utf-8"), manifest_before)
        self.assertEqual(bs._tail_date(self.raw / "BK0433.csv", 0), "2026-10-30")

    def test_single_board_failure_does_not_block_others(self):
        with self.patch_calendar("2026-11-03"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em",
                                  side_effect=[RuntimeError("接口超时"), frame(["2026-11-03"])]) as fetch:
            changed = bs.update_sectors()
        self.assertEqual(changed, 1)
        self.assertEqual(fetch.call_count, 2)

    def test_universe_diff_is_recorded_without_joining_list(self):
        """接口板块总数变化只留痕，生产清单不自动扩充。"""
        with self.patch_calendar("2026-11-03"), self.patch_spot(extra=2), \
                mock.patch.object(bs, "fetch_sector_hist_em", return_value=frame(["2026-11-03"])):
            bs.update_sectors()
        manifest = json.loads(self.manifest_path.read_text(encoding="utf-8"))
        self.assertEqual(manifest["universe_snapshot"]["接口板块总数"], len(fm.EM_SECTOR_UNIVERSE) + 2)
        self.assertEqual(len(manifest["universe_changes"]), 1)
        self.assertEqual(len(manifest["boards"]), 2)


class TailRevisionTest(SectorUpdateTestCase):
    """起点含尾日后的写盘判定：只看数值真有没有变，不能只看行数与尾日。"""

    def test_revised_tail_bar_is_rewritten(self):
        """尾日 bar 在下一轮拿到修正后的量额时必须覆盖落盘（自愈）。"""
        revised = frame(["2026-10-30"])
        revised.loc[0, "volume"] = 12345.0
        with self.patch_calendar("2026-11-03"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em", return_value=revised):
            changed = bs.update_sectors()
        self.assertEqual(changed, 2)
        saved = pd.read_csv(self.raw / "BK0433.csv", dtype={"date": str})
        self.assertEqual(saved["date"].tolist(), ["2026-10-30"])
        self.assertEqual(float(saved["volume"].iat[0]), 12345.0)

    def test_identical_repull_is_not_rewritten(self):
        """逐值一致时不写盘也不计为变化，避免每轮无谓重建。"""
        stamp = (self.raw / "BK0433.csv").stat().st_mtime_ns
        with self.patch_calendar("2026-11-03"), self.patch_spot(), \
                mock.patch.object(bs, "fetch_sector_hist_em", return_value=frame(["2026-10-30"])):
            changed = bs.update_sectors()
        self.assertEqual(changed, 0)
        self.assertEqual((self.raw / "BK0433.csv").stat().st_mtime_ns, stamp)


if __name__ == "__main__":
    unittest.main()
