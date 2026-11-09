"""外部数据层测试：manifest 契约、修订检测、对齐填充上限、单位守卫与失败隔离。"""
from __future__ import annotations

import json
import unittest

import numpy as np
import pandas as pd

import external_data


def series_frame(values: list[float], start: str = "2024-01-01") -> pd.DataFrame:
    dates = pd.bdate_range(start=start, periods=len(values))
    return pd.DataFrame({"date": dates, "value": values})


class ManifestTest(unittest.TestCase):
    def test_entry_contains_required_fields(self):
        frame = series_frame([1.0, 2.0, np.nan])
        entry = external_data.build_entry("demo", "fake", "说明", frame)
        for field in external_data.REQUIRED_FIELDS:
            self.assertIn(field, entry)
        self.assertEqual(entry["rows"], 3)
        self.assertAlmostEqual(entry["missing_rate"], 1 / 3, places=6)
        self.assertFalse(entry["revision_detected"])

    def test_missing_field_raises(self):
        entry = external_data.build_entry("demo", "fake", "说明", series_frame([1.0]))
        broken = dict(entry)
        broken.pop("revision_detected")
        with self.assertRaises(ValueError):
            external_data.validate_entry(broken)

    def test_missing_rate_out_of_range_raises(self):
        entry = external_data.build_entry("demo", "fake", "说明", series_frame([1.0]))
        broken = dict(entry, missing_rate=1.5)
        with self.assertRaises(ValueError):
            external_data.validate_entry(broken)

    def test_write_and_load_manifest(self):
        entry = external_data.build_entry("demo", "fake", "说明", series_frame([1.0, 2.0]))
        path = external_data.EXTERNAL.parent / "test-manifest.json"
        try:
            external_data.write_manifest([entry], path)
            payload = json.loads(path.read_text(encoding="utf-8"))
            self.assertEqual(payload["entries"][0]["indicator"], "demo")
        finally:
            path.unlink(missing_ok=True)


class RevisionTest(unittest.TestCase):
    def test_revision_detected_when_history_changes(self):
        previous = series_frame([1.0, 2.0, 3.0])
        changed = series_frame([1.0, 2.5, 3.0])
        self.assertTrue(external_data.detect_revision(changed, previous))

    def test_no_revision_when_identical(self):
        frame = series_frame([1.0, 2.0, 3.0])
        self.assertFalse(external_data.detect_revision(frame.copy(), frame))

    def test_no_previous_means_no_revision(self):
        self.assertFalse(external_data.detect_revision(series_frame([1.0]), None))


class AlignmentTest(unittest.TestCase):
    def test_ffill_limited_to_one_trading_day(self):
        frame = pd.DataFrame({"date": pd.to_datetime(["2024-01-01", "2024-01-05"]), "value": [1.0, 2.0]})
        calendar = pd.bdate_range("2024-01-01", "2024-01-05")
        aligned = external_data.align_to_calendar(frame, calendar, max_ffill=1)
        # 01-02 只隔 1 个交易日，允许填充；01-03、01-04 缺口过长，必须保留 NaN
        self.assertEqual(aligned.iloc[1], 1.0)
        self.assertTrue(np.isnan(aligned.iloc[2]))
        self.assertTrue(np.isnan(aligned.iloc[3]))
        self.assertEqual(aligned.iloc[4], 2.0)


class MarginUnitTest(unittest.TestCase):
    def test_growth_is_not_treated_as_unit_break(self):
        index = pd.to_datetime(pd.bdate_range("2010-03-31", periods=5))
        series = pd.Series([1.0e6, 2.0e7, 4.0e8, 8.0e9, 1.6e10], index=index)
        normalized, breaks = external_data.normalize_margin_units(series)
        self.assertEqual(breaks, [])
        np.testing.assert_allclose(normalized.to_numpy(), series.to_numpy())

    def test_hundredfold_jump_is_normalized(self):
        index = pd.to_datetime(pd.bdate_range("2010-03-31", periods=3))
        series = pd.Series([5.0e6, 6.0e6, 8.0e10], index=index)
        normalized, breaks = external_data.normalize_margin_units(series)
        self.assertEqual(len(breaks), 1)
        self.assertAlmostEqual(normalized.iloc[0], 5.0e10, delta=1.0)

    def test_zero_balance_becomes_missing(self):
        index = pd.to_datetime(pd.bdate_range("2024-08-01", periods=3))
        normalized, _ = external_data.normalize_margin_units(pd.Series([1.0e12, 0.0, 1.1e12], index=index))
        self.assertTrue(np.isnan(normalized.iloc[1]))


class AdapterTest(unittest.TestCase):
    def test_qvix_adapter_shape(self):
        class FakeAk:
            @staticmethod
            def index_option_50etf_qvix():
                return pd.DataFrame({
                    "date": ["2024-01-03", "2024-01-02"],
                    "open": [1, 1], "high": [1, 1], "low": [1, 1], "close": [18.5, 17.5],
                })

        original = external_data._import_akshare
        external_data._import_akshare = lambda: FakeAk
        try:
            frame = external_data.fetch_qvix("50etf")
        finally:
            external_data._import_akshare = original
        self.assertEqual(list(frame.columns), ["date", "value"])
        self.assertTrue(frame["date"].is_monotonic_increasing)
        self.assertEqual(len(frame), 2)

    def test_fetch_all_isolates_failure(self):
        import tempfile
        from pathlib import Path

        with tempfile.TemporaryDirectory() as folder:
            target = Path(folder)
            original_external, original_manifest = external_data.EXTERNAL, external_data.MANIFEST_NAME
            original_adapters = external_data.ADAPTERS
            external_data.EXTERNAL = target
            external_data.ADAPTERS = {
                "ok_indicator": {"interface": "fake", "definition": "正常", "frequency": "daily",
                                 "grade": "P0", "func": lambda: series_frame([1.0, 2.0])},
                "broken_indicator": {"interface": "fake", "definition": "必失败", "frequency": "daily",
                                     "grade": "P0", "func": lambda: (_ for _ in ()).throw(RuntimeError("接口不可用"))},
            }
            try:
                frames = external_data.fetch_all(["ok_indicator", "broken_indicator"])
                manifest = external_data.load_manifest()
            finally:
                external_data.EXTERNAL, external_data.MANIFEST_NAME = original_external, original_manifest
                external_data.ADAPTERS = original_adapters
            self.assertIn("ok_indicator", frames)
            self.assertEqual(manifest["ok_indicator"]["status"], "ok")
            self.assertEqual(manifest["broken_indicator"]["status"], "failed")
            self.assertIn("接口不可用", manifest["broken_indicator"]["error"])


if __name__ == "__main__":
    unittest.main()
