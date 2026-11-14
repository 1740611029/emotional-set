"""ETF 日线数据线测试：字段口径、清单校验、增量合并与失败隔离（全部离线）。

2026-11-14 换源后：日线为新浪全量返回（volume 单位股，落盘 /100 转手），
清单校验为腾讯实时接口。本文件按新接口口径重写。
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import fetch_etf

FROZEN_NAMES = {item["code"]: item["name"] for item in fetch_etf.ETF_UNIVERSE}


def _source_frame(rows: list[dict]) -> pd.DataFrame:
    """构造新浪原始列（含额外的 postVol/postAmt 列，验证字段筛选与 volume 换算）。"""
    return pd.DataFrame({
        "date": [row["date"] for row in rows],
        "open": [row.get("open", 1.0) for row in rows],
        "high": [row.get("high", 1.2) for row in rows],
        "low": [row.get("low", 0.9) for row in rows],
        "close": [row.get("close", 1.1) for row in rows],
        "volume": [row.get("volume_shares", 100000) for row in rows],  # 股
        "amount": [row.get("amount", 1e10) for row in rows],
        "postVol": [0.0 for _ in rows],
        "postAmt": [0.0 for _ in rows],
    })


def _series(days: list[str], *, amount: float = 1e10) -> pd.DataFrame:
    return pd.DataFrame({
        "date": days,
        "open": [1.0] * len(days), "close": [1.1] * len(days),
        "high": [1.2] * len(days), "low": [0.9] * len(days),
        "volume": [1000.0] * len(days), "amount": [amount] * len(days),
    })


class UniverseTest(unittest.TestCase):
    def test_universe_is_frozen_six(self):
        self.assertEqual(len(fetch_etf.ETF_UNIVERSE), 6)
        self.assertEqual({item["code"] for item in fetch_etf.ETF_UNIVERSE},
                         {"588000", "159915", "510300", "510050", "512100", "510500"})
        for item in fetch_etf.ETF_UNIVERSE:
            self.assertTrue(item["name"])
            self.assertTrue(item["index"])
        self.assertEqual(fetch_etf.ETF_AMOUNT_COLUMNS,
                         ["date", "open", "close", "high", "low", "volume", "amount"])

    def test_check_passes_when_names_match(self):
        entries = fetch_etf.check_etf_universe(dict(FROZEN_NAMES))
        self.assertEqual([entry["code"] for entry in entries],
                         [item["code"] for item in fetch_etf.ETF_UNIVERSE])

    def test_missing_code_raises(self):
        spot = dict(FROZEN_NAMES)
        spot.pop("510300")
        with self.assertRaises(ValueError) as caught:
            fetch_etf.check_etf_universe(spot)
        self.assertIn("510300", str(caught.exception))

    def test_name_mismatch_raises_instead_of_substituting(self):
        spot = dict(FROZEN_NAMES, **{"159915": "创业板ETF天弘"})
        with self.assertRaises(ValueError) as caught:
            fetch_etf.check_etf_universe(spot)
        self.assertIn("创业板ETF天弘", str(caught.exception))


class HistFetchTest(unittest.TestCase):
    def test_columns_filtered_sorted_deduplicated_and_volume_converted_to_hand(self):
        """新浪列筛选 + 日期去重保尾 + volume 股→手换算（/100）。"""
        source = _source_frame([
            {"date": "2026-11-03", "amount": 2e10, "volume_shares": 123456700},
            {"date": "2026-11-02", "amount": 1e10, "volume_shares": 111111100},
            {"date": "2026-11-03", "amount": 3e10, "volume_shares": 222222200},
        ])
        with mock.patch.object(fetch_etf, "call_with_retry", return_value=source):
            frame = fetch_etf.fetch_etf_hist_sina("510300")
        self.assertEqual(frame.columns.tolist(), fetch_etf.ETF_AMOUNT_COLUMNS)
        self.assertEqual(frame["date"].tolist(), ["2026-11-02", "2026-11-03"])
        self.assertEqual(float(frame["amount"].iat[-1]), 3e10)  # 同日保留最后一次
        self.assertEqual(float(frame["volume"].iat[-1]), 2222222.0)  # 股 /100 → 手
        self.assertEqual(str(frame["amount"].dtype), "float64")

    def test_missing_field_raises(self):
        source = _source_frame([{"date": "2026-11-03"}]).drop(columns=["amount"])
        with mock.patch.object(fetch_etf, "call_with_retry", return_value=source):
            with self.assertRaises(ValueError) as caught:
                fetch_etf.fetch_etf_hist_sina("510300")
        self.assertIn("amount", str(caught.exception))

    def test_non_numeric_amount_raises(self):
        source = _source_frame([{"date": "2026-11-03"}])
        source["amount"] = source["amount"].astype(object)
        source.loc[0, "amount"] = "—"
        with mock.patch.object(fetch_etf, "call_with_retry", return_value=source):
            with self.assertRaises(ValueError):
                fetch_etf.fetch_etf_hist_sina("510300")


class IncrementalTest(unittest.TestCase):
    EXPECTED = "2026-11-03"

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        raw = root / "etf-daily"
        manifest = root / "etf-daily-manifest.json"
        for patch in (
            mock.patch.object(fetch_etf, "ETF_DAILY_RAW", raw),
            mock.patch.object(fetch_etf, "ETF_DAILY_MANIFEST", manifest),
            mock.patch.object(fetch_etf, "etf_spot", return_value=dict(FROZEN_NAMES)),
            mock.patch.object(fetch_etf, "last_completed_trading_day",
                              return_value=(self.EXPECTED, "calendar")),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        raw.mkdir(parents=True)
        manifest.write_text(json.dumps({"source": "x", "universe": [], "boards": []},
                                       ensure_ascii=False), encoding="utf-8")
        self.raw = raw
        self.manifest = manifest

    def _seed(self, code: str, days: list[str], *, amount: float = 1e10) -> Path:
        path = self.raw / f"{code}.csv"
        _series(days, amount=amount).to_csv(path, index=False, encoding="utf-8-sig")
        return path

    def _seed_all(self, days: list[str], *, amount: float = 1e10) -> None:
        for code in FROZEN_NAMES:
            self._seed(code, days, amount=amount)

    def test_incomplete_intraday_bar_is_not_written(self):
        path = self._seed("510300", ["2026-11-02"])
        self._seed_all(["2026-11-02"])
        source = _series(["2026-11-02", "2026-11-03", "2026-11-04"])
        with mock.patch.object(fetch_etf, "fetch_etf_hist_sina", return_value=source) as fetch:
            result = fetch_etf.update_etf_daily()
        # 新浪全量返回：单参数调用，盘中 11-04 由 _merge_and_save 裁掉
        requested = [call.args for call in fetch.call_args_list]
        self.assertIn(("510300",), requested)
        written = pd.read_csv(path, dtype={"date": str})
        self.assertEqual(written["date"].tolist(), ["2026-11-02", "2026-11-03"])
        self.assertEqual(written["date"].is_unique, True)
        self.assertIn("510300", result["updated"])

    def test_corrected_tail_amount_overwrites_without_duplicate_rows(self):
        # 本地尾日 11-02 的量额未定稿，下一轮全量重取时必须被覆写且不产生重复日期行
        self._seed_all(["2026-11-01", "2026-11-02"], amount=1e10)
        path = self.raw / "510300.csv"
        source = _series(["2026-11-02", "2026-11-03"], amount=3e10)
        with mock.patch.object(fetch_etf, "fetch_etf_hist_sina", return_value=source):
            result = fetch_etf.update_etf_daily()
        written = pd.read_csv(path, dtype={"date": str})
        self.assertEqual(written["date"].tolist(), ["2026-11-01", "2026-11-02", "2026-11-03"])
        self.assertEqual(written["date"].is_unique, True)
        self.assertAlmostEqual(float(written.loc[written["date"] == "2026-11-02", "amount"].iat[0]), 3e10)
        self.assertIn("510300", result["updated"])

    def test_short_history_is_flagged_in_manifest(self):
        self._seed("510300", ["2026-11-02"])
        self._seed_all(["2026-11-02"])
        source = _series(["2026-11-02", "2026-11-03"])
        with mock.patch.object(fetch_etf, "fetch_etf_hist_sina", return_value=source):
            fetch_etf.update_etf_daily()
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        rows = {row["code"]: row for row in payload["boards"]}
        self.assertTrue(rows["510300"]["insufficient_history"])
        self.assertEqual(rows["510300"]["end"], self.EXPECTED)

    def test_uninitialized_manifest_skips_without_network(self):
        self.manifest.unlink()
        with mock.patch.object(fetch_etf, "fetch_etf_hist_sina") as fetch:
            result = fetch_etf.update_etf_daily()
        self.assertEqual(result["status"], "uninitialized")
        fetch.assert_not_called()

    def test_up_to_date_codes_are_skipped_without_request(self):
        self._seed_all(["2026-11-02", self.EXPECTED])
        with mock.patch.object(fetch_etf, "fetch_etf_hist_sina") as fetch:
            result = fetch_etf.update_etf_daily()
        self.assertEqual(result["status"], "skipped")
        self.assertEqual(result["skipped"], 6)
        fetch.assert_not_called()


class FullFetchTest(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        root = Path(self._tmp.name)
        for patch in (
            mock.patch.object(fetch_etf, "ETF_DAILY_RAW", root / "etf-daily"),
            mock.patch.object(fetch_etf, "ETF_DAILY_MANIFEST", root / "manifest.json"),
            mock.patch.object(fetch_etf, "etf_spot", return_value=dict(FROZEN_NAMES)),
            mock.patch.object(fetch_etf, "last_completed_trading_day",
                              return_value=("2026-11-03", "calendar")),
            mock.patch.object(fetch_etf.time, "sleep"),
        ):
            patch.start()
            self.addCleanup(patch.stop)
        self.raw = root / "etf-daily"
        self.manifest = root / "manifest.json"

    def test_single_failure_does_not_block_others(self):
        frame = _series(["2026-11-02", "2026-11-03"])

        def fake_fetch(symbol):
            if symbol == "588000":
                raise RuntimeError("接口超时")
            return frame

        with mock.patch.object(fetch_etf, "fetch_etf_hist_sina", side_effect=fake_fetch):
            result = fetch_etf.fetch_etf_daily()
        self.assertEqual(result["failed"], ["588000"])
        self.assertEqual(len(result["rows"]), 5)
        self.assertEqual(len(list(self.raw.glob("*.csv"))), 5)
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        self.assertEqual(len(payload["boards"]), 5)
        self.assertEqual(payload["source"], fetch_etf.ETF_SOURCE)

    def test_manifest_lists_rows_and_range(self):
        frame = _series(["2026-11-02", "2026-11-03"])
        with mock.patch.object(fetch_etf, "fetch_etf_hist_sina", return_value=frame):
            fetch_etf.fetch_etf_daily()
        payload = json.loads(self.manifest.read_text(encoding="utf-8"))
        first = payload["boards"][0]
        for key in ("code", "name", "index", "rows", "start", "end", "path"):
            self.assertIn(key, first)
        self.assertEqual((first["rows"], first["start"], first["end"]), (2, "2026-11-02", "2026-11-03"))


if __name__ == "__main__":
    unittest.main()
