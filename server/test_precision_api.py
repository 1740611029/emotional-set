"""多持有期矩阵 API 契约（任务 5.3 / 7.1 端到端消费 /api/snapshot）。"""
from __future__ import annotations

import json
import unittest

from fastapi.testclient import TestClient

from . import paths
from .app import STORE, app, index_history, snapshot
from .snapshot import build_close_snapshot

HORIZONS = ("5", "10", "20", "30")
KINDS = ("bottom", "top")
SEGMENTS = ("train", "validation")


class PrecisionApiTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        payload = json.loads(paths.INDEX_SIGNALS.read_text(encoding="utf-8"))
        cls.payload = payload
        cls.snapshot = build_close_snapshot(payload, None)

    def setUp(self):
        STORE.set(self.snapshot)

    def test_snapshot_carries_precision_block(self):
        data = snapshot()
        precision = data["precision"]
        self.assertIsNotNone(precision)
        self.assertEqual(precision["universe_size"], 7)
        self.assertEqual(len(precision["covered_universe"]), 7)
        self.assertEqual(precision["covered_universe"],
                         [item["code"] for item in data["indices"]])
        self.assertIn(precision["decision"], ("更换建议", "观察", "保留现行"))
        self.assertIn("方向正确率", precision["criteria_note"])
        self.assertIn("历史基线", precision["legacy_rate_note"])
        self.assertIn("不代表交易收益率", precision["criteria_note"])

    def test_every_index_has_complete_matrix(self):
        data = snapshot()
        self.assertEqual(len(data["indices"]), 7)
        for item in data["indices"]:
            matrix = item["precision_matrix"]
            for segment in SEGMENTS:
                for horizon in HORIZONS:
                    for kind in KINDS:
                        cell = matrix[segment]["horizons"][horizon][kind]
                        self.assertEqual(cell["segment"], segment)
                        self.assertEqual(cell["horizon_days"], int(horizon))
                        self.assertEqual(cell["direction"], kind)
                        self.assertEqual(cell["resolved"] + cell["pending"],
                                         cell["triggers"])
                        self.assertEqual(cell["small_sample"], cell["resolved"] < 30)
                        self.assertIn("start", cell["statistical_interval"])
                        self.assertIn("direction_correct_wilson95", cell)
                        self.assertIn("avg_max_adverse_excursion", cell["drawdown"])

    def test_history_carries_matrix_anchors_and_sample_flags(self):
        data = index_history("sh000001")
        self.assertEqual(data["code"], "sh000001")
        self.assertEqual(len(data["precision_matrix"]["train"]["horizons"]), 4)
        self.assertIsInstance(data["pivot_anchors"], list)
        self.assertIsNotNone(data["criteria_note"])
        self.assertIn("历史基线", data["legacy_rate_note"])
        flags = data["sample_flags"]
        self.assertEqual(len(flags), 2 * 4 * 2)
        for label, flag in flags.items():
            self.assertIn("small_sample", flag)
            self.assertEqual(flag["single_digit_resolved"], flag["resolved"] < 10)
            self.assertEqual(flag["small_sample"], flag["resolved"] < 30, label)

    def test_pivot_anchors_carry_three_dates_and_layer_log(self):
        precision = snapshot()["precision"]
        anchors = [record for records in precision["pivot_anchors"].values()
                   for record in records]
        if not anchors:
            self.skipTest("新算法本次无已确认信号，锚点为空")
        for record in anchors:
            for key in ("pivot_date", "pivot_price", "natural_confirm_date",
                        "natural_confirm_offset", "signal_confirm_offset_from_pivot",
                        "signal_confirm_offset_from_natural", "confirmation"):
                self.assertIn(key, record)
            self.assertGreaterEqual(record["signal_confirm_offset_from_natural"], 0)
            self.assertGreater(len(record["confirmation"]), 0)
            for entry in record["confirmation"]:
                for layer in ("price", "momentum", "volume"):
                    self.assertIn("passed", entry[layer])
                    self.assertIn("rule", entry[layer])

    def test_legacy_rate_still_present(self):
        for item in snapshot()["indices"]:
            self.assertIn("rate", item["accuracy"]["bottom"])
            self.assertIn("rate", item["accuracy"]["top"])

    def test_snapshot_does_not_carry_trading_language(self):
        precision = snapshot()["precision"]
        text = precision["criteria_note"] + precision["legacy_rate_note"]
        for word in ("买入", "卖出", "可交易", "胜率极高", "预期收益"):
            self.assertNotIn(word, text)


class PrecisionEndToEndTest(unittest.TestCase):
    """任务 7.1：真实 ASGI 端点消费重建后的生产产物。"""

    @classmethod
    def setUpClass(cls):
        payload = json.loads(paths.INDEX_SIGNALS.read_text(encoding="utf-8"))
        sectors = json.loads(paths.SECTOR_TRENDS.read_text(encoding="utf-8"))
        STORE.set(build_close_snapshot(payload, sectors))
        cls.client = TestClient(app)

    def test_http_snapshot_has_all_matrix_cells(self):
        response = self.client.get("/api/snapshot")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIsNotNone(body["precision"])
        cell_count = 0
        for item in body["indices"]:
            for segment in ("train", "validation"):
                for horizon in ("5", "10", "20", "30"):
                    for kind in ("bottom", "top"):
                        cell = item["precision_matrix"][segment]["horizons"][horizon][kind]
                        self.assertIn("triggers", cell)
                        self.assertIn("direction_correct_wilson95", cell)
                        cell_count += 1
        self.assertEqual(cell_count, 7 * 2 * 4 * 2)

    def test_http_history_has_anchors_and_flags(self):
        response = self.client.get("/api/indices/sh000001/history")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertIn("precision_matrix", body)
        self.assertIn("pivot_anchors", body)
        self.assertEqual(len(body["sample_flags"]), 16)

    def test_static_index_page_is_served(self):
        response = self.client.get("/")
        self.assertEqual(response.status_code, 200)
        self.assertIn("text/html", response.headers["content-type"])


if __name__ == "__main__":
    unittest.main()
