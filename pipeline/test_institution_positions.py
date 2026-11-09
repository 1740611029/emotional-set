"""机构持仓流水线的口径与契约测试（不依赖网络）。"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import institution_positions as ip
from schemas import validate_payload

DAY_A, DAY_B, DAY_C = "2026-01-05", "2026-01-06", "2026-01-07"


def make_table(rows_by_variety: dict[str, list[tuple[str, int, int]]]) -> dict[str, pd.DataFrame]:
    """构造 akshare 形态的返回：每品种一个合约 DataFrame + rank=999 合计行。

    rows 格式: (long_party_name, long_oi, short_oi)
    当未指定 short_party_name 时，默认与 long_party_name 相同（简化测试）。
    如需测试多空分离，使用 make_table_split。
    """
    table: dict[str, pd.DataFrame] = {}
    for variety, rows in rows_by_variety.items():
        records = [
            {"rank": index, "long_party_name": name, "long_open_interest": long_oi,
             "short_party_name": name, "short_open_interest": short_oi, "variety": variety}
            for index, (name, long_oi, short_oi) in enumerate(rows, start=1)
        ]
        records.append({
            "rank": 999, "long_party_name": None, "short_party_name": None,
            "long_open_interest": sum(row[1] for row in rows),
            "short_open_interest": sum(row[2] for row in rows), "variety": variety,
        })
        table[f"{variety}2601"] = pd.DataFrame(records)
    return table


def make_table_split(long_rows: list[tuple[str, int]], short_rows: list[tuple[str, int]],
                     variety: str = "IF") -> dict[str, pd.DataFrame]:
    """构造多空分离的 akshare 返回：多头排名和空头排名对应不同会员。"""
    n = max(len(long_rows), len(short_rows))
    records = []
    for i in range(n):
        rec = {"rank": i + 1, "variety": variety}
        if i < len(long_rows):
            rec["long_party_name"] = long_rows[i][0]
            rec["long_open_interest"] = long_rows[i][1]
        if i < len(short_rows):
            rec["short_party_name"] = short_rows[i][0]
            rec["short_open_interest"] = short_rows[i][1]
        records.append(rec)
    records.append({
        "rank": 999, "long_party_name": None, "short_party_name": None,
        "long_open_interest": sum(r[1] for r in long_rows),
        "short_open_interest": sum(r[1] for r in short_rows), "variety": variety,
    })
    return {f"{variety}2601": pd.DataFrame(records)}


def sample_rows(guotai=(100, 140), citic=(200, 180), others=(300, 320)) -> list[tuple[str, int, int]]:
    return [("国泰君安(代客)", *guotai), ("中信期货(代客)", *citic), ("中信建投(代客)", *others)]


def synthetic_benchmark(days: list[str], closes: list[float]) -> pd.DataFrame:
    return pd.DataFrame({"date": days, "close": closes})


SH_INDEX = synthetic_benchmark([DAY_A, DAY_B, DAY_C], [3200.0, 3210.0, 3205.0])


class MemberGroupingTest(unittest.TestCase):
    def test_book_suffix_and_exact_match(self):
        self.assertEqual(ip.base_name("国泰君安(代客)"), "国泰君安")
        self.assertEqual(ip.group_of("国泰君安(代客)"), "guotai")
        self.assertEqual(ip.group_of("国泰君安"), "guotai")
        self.assertEqual(ip.group_of("中信期货(代客)"), "citic")
        self.assertEqual(ip.group_of("中信建投(代客)"), "others")
        self.assertEqual(ip.group_of("国泰君安期货"), "guotai")

    def test_broker_suffix_falls_into_correct_groups(self):
        # 2024-02-26 ~ 2024-04-26 源数据会员名带"(经纪)"后缀
        self.assertEqual(ip.base_name("中信期货(经纪)"), "中信期货")
        self.assertEqual(ip.group_of("中信期货(经纪)"), "citic")
        self.assertEqual(ip.group_of("国泰君安(经纪)"), "guotai")
        self.assertEqual(ip.group_of("中信建投(经纪)"), "others")

    def test_no_member_is_counted_twice(self):
        frame = ip.normalize_table(DAY_A, make_table({"IF": sample_rows()}))
        aggregated = ip.aggregate_day(frame)
        groups = aggregated["varieties"]["IF"]["groups"]
        self.assertEqual(groups["guotai"], {"long": 100, "short": 140})
        self.assertEqual(groups["citic"], {"long": 200, "short": 180})
        self.assertEqual(groups["others"], {"long": 300, "short": 320})

    def test_long_short_split_correctly_attributed(self):
        """多头排名第1是国泰，空头排名第1是中信——各自归集不得串位。"""
        table = make_table_split(
            long_rows=[("国泰君安(代客)", 100), ("中信期货(代客)", 80)],
            short_rows=[("中信期货(代客)", 120), ("国泰君安(代客)", 60)],
        )
        frame = ip.normalize_table(DAY_A, table)
        aggregated = ip.aggregate_day(frame)
        groups = aggregated["varieties"]["IF"]["groups"]
        # 国泰: long=100 (来自多头排名), short=60 (来自空头排名)
        self.assertEqual(groups["guotai"], {"long": 100, "short": 60})
        # 中信: long=80 (来自多头排名), short=120 (来自空头排名)
        self.assertEqual(groups["citic"], {"long": 80, "short": 120})

    def test_unmatched_members_are_reported_without_suffix(self):
        aggregated = ip.aggregate_day(ip.normalize_table(DAY_A, make_table({"IF": sample_rows()})))
        self.assertIn("中信建投", aggregated["unmapped"])
        self.assertNotIn("国泰君安", aggregated["unmapped"])

    def test_legacy_cache_triggers_refetch(self):
        """旧格式缓存（无 long_party_name 列）应返回 None 触发重抓。"""
        import tempfile
        from pathlib import Path
        old_csv = "date,variety,contract,rank,party_name,long_oi,short_oi\n2026-01-05,IF,IF2601,1,国泰君安(代客),100,140\n"
        with tempfile.TemporaryDirectory() as temp:
            p = Path(temp) / f"{DAY_A}.csv"
            p.write_text(old_csv, encoding="utf-8")
            with mock.patch.object(ip, "RAW_CFFEX", Path(temp)):
                result = ip.read_cached_day(DAY_A)
        self.assertIsNone(result)


class AggregationTest(unittest.TestCase):
    def test_member_positions_are_summed_across_contracts(self):
        table = {
            "IF2601": pd.DataFrame([
                {"rank": 1, "long_party_name": "国泰君安(代客)", "long_open_interest": 100,
                 "short_party_name": "国泰君安(代客)", "short_open_interest": 10, "variety": "IF"},
                {"rank": 999, "long_party_name": None, "short_party_name": None,
                 "long_open_interest": 100,
                 "short_open_interest": 10, "variety": "IF"},
            ]),
            "IF2603": pd.DataFrame([
                {"rank": 1, "long_party_name": "国泰君安(代客)", "long_open_interest": 50,
                 "short_party_name": "国泰君安(代客)", "short_open_interest": 5, "variety": "IF"},
                {"rank": 999, "long_party_name": None, "short_party_name": None,
                 "long_open_interest": 50,
                 "short_open_interest": 5, "variety": "IF"},
            ]),
        }
        aggregated = ip.aggregate_day(ip.normalize_table(DAY_A, table))
        self.assertEqual(aggregated["varieties"]["IF"]["groups"]["guotai"], {"long": 150, "short": 15})

    def test_conservation_mismatch_raises(self):
        table = make_table({"IF": sample_rows()})
        table["IF2601"].loc[table["IF2601"]["rank"] == 999, "long_open_interest"] = 1
        with self.assertRaises(ValueError) as context:
            ip.aggregate_day(ip.normalize_table(DAY_A, table))
        self.assertIn("守恒", str(context.exception))

    def test_all_view_equals_variety_sum(self):
        table = make_table({
            "IF": sample_rows(guotai=(100, 140)),
            "IC": sample_rows(guotai=(60, 80), citic=(10, 20), others=(30, 40)),
        })
        aggregated = ip.aggregate_day(ip.normalize_table(DAY_A, table))
        views = ip.day_views(aggregated)
        for gid, _ in ip.GROUPS:
            self.assertEqual(views["all"][gid]["long"], views["IF"][gid]["long"] + views["IC"][gid]["long"])
            self.assertEqual(views["all"][gid]["short"], views["IF"][gid]["short"] + views["IC"][gid]["short"])


class SeriesTest(unittest.TestCase):
    def build(self, dates, day_map, closes):
        return ip.build_series(dates, day_map, closes)

    def test_change_is_relative_to_previous_trading_day(self):
        day_map = {
            DAY_A: {view: {"guotai": {"long": 100, "short": 50}} for view in ip.VIEWS},
            DAY_B: {view: {"guotai": {"long": 130, "short": 50}} for view in ip.VIEWS},
        }
        series = self.build([DAY_A, DAY_B], day_map, [100.0, 101.0])
        self.assertIsNone(series["guotai"]["all"]["chg"][0])
        self.assertEqual(series["guotai"]["all"]["chg"][1], 30)

    def test_missing_day_breaks_change_and_marks_no_data(self):
        day_map = {
            DAY_A: {view: {"guotai": {"long": 100, "short": 50}} for view in ip.VIEWS},
            DAY_C: {view: {"guotai": {"long": 130, "short": 50}} for view in ip.VIEWS},
        }
        series = self.build([DAY_A, DAY_B, DAY_C], day_map, [100.0, 101.0, 102.0])
        self.assertEqual(series["guotai"]["all"]["relation"][1], "N")
        self.assertIsNone(series["guotai"]["all"]["chg"][2])

    def test_next_return_uses_next_trading_row(self):
        dates = ["2026-01-09", "2026-01-12"]  # 周五 → 下周一
        day_map = {day: {view: {"guotai": {"long": 100, "short": 50}} for view in ip.VIEWS} for day in dates}
        series = self.build(dates, day_map, [100.0, 110.0])
        self.assertAlmostEqual(series["guotai"]["all"]["next_ret"][0], 0.1)
        self.assertIsNone(series["guotai"]["all"]["next_ret"][1])

    def test_classify_covers_all_four_states(self):
        self.assertEqual(ip.classify(10, 0.01), "S")
        self.assertEqual(ip.classify(-10, -0.01), "S")
        self.assertEqual(ip.classify(10, -0.01), "H")
        self.assertEqual(ip.classify(-10, 0.01), "H")
        self.assertEqual(ip.classify(0, 0.01), "P")
        self.assertEqual(ip.classify(None, 0.01), "P")
        self.assertEqual(ip.classify(10, None), "P")

    def test_stats_use_decided_denominator_and_report_bounds(self):
        dates = [f"2026-01-0{day}" for day in range(5, 9)]
        day_map = {}
        nets = [0, 10, 20, 5]
        for day, net in zip(dates, nets):
            day_map[day] = {view: {"guotai": {"long": 100 + net, "short": 100}} for view in ip.VIEWS}
        series = self.build(dates, day_map, [100.0, 101.0, 99.0, 100.0])
        stats = ip.summarize(dates, series)["guotai"]["all"]
        self.assertEqual(stats["decided"], 2)
        self.assertEqual((stats["same"], stats["hedge"]), (1, 1))
        self.assertAlmostEqual(stats["rate"], 0.5)
        self.assertEqual((stats["start"], stats["end"]), (dates[1], dates[2]))
        self.assertTrue(stats["small_sample"])


class PayloadTest(unittest.TestCase):
    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = mock.patch.object(ip, "RAW_CFFEX", Path(self._temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def build_payload(self, *, closes=None, fail_days=()):
        dates = [DAY_A, DAY_B, DAY_C]
        benchmark = synthetic_benchmark(dates, closes or [100.0, 101.0, 100.5])

        def fetcher(day):
            if day in fail_days:
                raise RuntimeError("网络不可用")
            return make_table({"IF": sample_rows(), "IC": sample_rows()})

        return ip.build_payload(benchmark=benchmark, fetcher=fetcher,
                                market_index=SH_INDEX), benchmark

    def test_payload_matches_schema(self):
        payload, _ = self.build_payload()
        validate_payload("institution-positions.json", payload)
        self.assertEqual(payload["as_of"], DAY_C)
        self.assertEqual(len(payload["dates"]), 3)
        self.assertTrue(payload["aggregation_note"])

    def test_failed_day_is_reported_not_raised(self):
        payload, _ = self.build_payload(fail_days=(DAY_B,))
        self.assertEqual(payload["quality"]["missing_dates"], [DAY_B])
        self.assertEqual(payload["series"]["guotai"]["all"]["relation"][1], "N")
        validate_payload("institution-positions.json", payload)

    def test_schema_rejects_drifted_relation(self):
        payload, _ = self.build_payload()
        payload["series"]["guotai"]["all"]["relation"][0] = "S"
        with self.assertRaises(ValueError):
            validate_payload("institution-positions.json", payload)

    def test_schema_rejects_wrong_rate(self):
        payload, _ = self.build_payload()
        payload["stats"]["guotai"]["all"]["rate"] = 0.99
        with self.assertRaises(ValueError):
            validate_payload("institution-positions.json", payload)

    def test_schema_rejects_missing_field(self):
        payload, _ = self.build_payload()
        del payload["quality"]
        with self.assertRaises(Exception):
            validate_payload("institution-positions.json", payload)

    def test_schema_rejects_misaligned_arrays(self):
        payload, _ = self.build_payload()
        payload["series"]["citic"]["IF"]["net"].pop()
        with self.assertRaises(ValueError):
            validate_payload("institution-positions.json", payload)


class MarketLinesTest(unittest.TestCase):
    """展示用大盘对照序列：上证仅叠加展示，统计口径必须一字不变。"""

    def setUp(self):
        self._temp = tempfile.TemporaryDirectory()
        self.addCleanup(self._temp.cleanup)
        patcher = mock.patch.object(ip, "RAW_CFFEX", Path(self._temp.name))
        patcher.start()
        self.addCleanup(patcher.stop)

    def build(self, market_index):
        dates = [DAY_A, DAY_B, DAY_C]
        benchmark = synthetic_benchmark(dates, [100.0, 101.0, 100.5])

        def fetcher(day):
            return make_table({"IF": sample_rows(), "IC": sample_rows()})

        return ip.build_payload(benchmark=benchmark, fetcher=fetcher, market_index=market_index)

    def test_two_lines_aligned_with_dates(self):
        payload = self.build(SH_INDEX)
        validate_payload("institution-positions.json", payload)
        lines = payload["market_lines"]
        self.assertEqual([line["code"] for line in lines], ["sh000001", "sh000300"])
        self.assertEqual([line["name"] for line in lines], ["上证指数", "沪深300"])
        for line in lines:
            self.assertEqual(len(line["close"]), len(payload["dates"]))
        self.assertEqual(lines[1]["close"], payload["benchmark"]["close"])

    def test_missing_index_day_breaks_line(self):
        payload = self.build(synthetic_benchmark([DAY_A, DAY_C], [3200.0, 3210.0]))
        index_close = payload["market_lines"][0]["close"]
        self.assertEqual(index_close[:1] + index_close[2:], [3200.0, 3210.0])
        self.assertIsNone(index_close[1])

    def test_absent_index_file_keeps_benchmark_line_only(self):
        payload = self.build(pd.DataFrame(columns=["date", "close"]))
        self.assertEqual([line["code"] for line in payload["market_lines"]], ["sh000300"])
        validate_payload("institution-positions.json", payload)

    def test_display_series_does_not_change_statistics(self):
        with_index = self.build(SH_INDEX)
        without = self.build(pd.DataFrame(columns=["date", "close"]))
        for key in ("as_of", "window", "benchmark", "dates", "series", "stats", "quality"):
            self.assertEqual(without[key], with_index[key], f"{key} 被展示序列影响")


class CacheTest(unittest.TestCase):
    def test_second_call_reuses_cache_and_skips_network(self):
        calls: list[str] = []

        def fetcher(day):
            calls.append(day)
            return make_table({"IF": sample_rows()})

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(ip, "RAW_CFFEX", Path(temp)):
            first = ip.ensure_day(DAY_A, fetcher=fetcher)
            second = ip.ensure_day(DAY_A, fetcher=fetcher)
            self.assertEqual(len(calls), 1)
            self.assertEqual(len(first), len(second))
            self.assertTrue((Path(temp) / f"{DAY_A}.csv").exists())

    def test_non_trading_day_is_not_cached(self):
        with tempfile.TemporaryDirectory() as temp, mock.patch.object(ip, "RAW_CFFEX", Path(temp)):
            result = ip.ensure_day("2026-01-10", fetcher=lambda day: {})
            self.assertIsNone(result)
            self.assertFalse((Path(temp) / "2026-01-10.csv").exists())

    def test_retry_then_failure_returns_none(self):
        attempts: list[int] = []

        def fetcher(day):
            attempts.append(1)
            raise RuntimeError("boom")

        with tempfile.TemporaryDirectory() as temp, mock.patch.object(ip, "RAW_CFFEX", Path(temp)):
            result = ip.ensure_day(DAY_A, fetcher=fetcher, force=True)
        self.assertIsNone(result)
        self.assertEqual(len(attempts), ip.RETRY_ATTEMPTS)


class BootstrapHookTest(unittest.TestCase):
    """机构持仓更新为独立产物：失败不得阻断指数与板块刷新。"""

    def test_failure_does_not_break_refresh(self):
        import bootstrap

        def boom():
            raise RuntimeError("中金所接口不可用")

        with mock.patch.object(ip, "build_payload", boom), \
                mock.patch.object(bootstrap, "ensure_raw_data", lambda progress: None), \
                mock.patch.object(bootstrap, "update_indices", lambda progress: ["sh000300"]), \
                mock.patch.object(bootstrap, "update_sectors", lambda progress: 3):
            result = bootstrap.refresh_daily(bootstrap.Progress())

        self.assertEqual(result["indices"], ["sh000300"])
        self.assertEqual(result["sectors"], 3)
        self.assertEqual(result["institutions"]["status"], "failed")
        self.assertIn("中金所接口不可用", result["institutions"]["error"])

    def test_success_reports_as_of(self):
        import bootstrap

        payload = {"as_of": DAY_C, "quality": {"missing_dates": []}}
        with mock.patch.object(ip, "build_payload", lambda: payload), \
                mock.patch("io_utils.protected_write", lambda *args, **kwargs: None):
            result = bootstrap.update_institutions(bootstrap.Progress())
        self.assertEqual(result, {"status": "ok", "as_of": DAY_C, "missing": 0})


if __name__ == "__main__":
    unittest.main()
