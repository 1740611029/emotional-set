"""schema 与失败保护的回归测试。"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from io_utils import protected_write
from jsonschema import Draft202012Validator
from schemas import SECTOR_EVALUATION, validate_payload
from build_dashboard import build_indices, current_signal
from fetch_etf import ETF_UNIVERSE
from fetch_market import INDEXES

ROOT = Path(__file__).resolve().parents[1]


def convert_sector_payload_to_signed(payload: dict) -> dict:
    """将测试中读取的旧板块产物临时转换为有向强度，便于逐任务回归。"""
    def convert(item: dict) -> None:
        parts = item.get("strength_parts")
        strength = item.get("strength")
        basis = item.get("strength_basis")
        if parts is None or strength is None:
            return
        average = (parts["range_position"] + parts["ma_score"]) / 2
        if basis == "neutral":
            item["strength"] = 0.0
        elif basis in {"directional", "inverted"}:
            item["strength"] = average - 50
            item["strength_basis"] = "directional"

    for rows in payload["groups"].values():
        for item in rows:
            convert(item)
    return payload


class ProtectedWriteTest(unittest.TestCase):
    def test_contract_examples(self):
        for name in ["index-signals.json", "sector-trends.json"]:
            payload = json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
            if name == "sector-trends.json":
                payload = convert_sector_payload_to_signed(payload)
            validate_payload(name, payload)

    def test_missing_required_field_preserves_previous_json(self):
        name = "index-signals.json"
        payload = json.loads((ROOT / "data" / name).read_text(encoding="utf-8"))
        del payload["indices"][0]["history"]
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            previous = directory / name
            previous.write_text('{"version":"previous"}', encoding="utf-8")
            before = previous.read_bytes()
            with self.assertRaises(Exception):
                protected_write(name, payload, directory=directory)
            self.assertEqual(previous.read_bytes(), before)


class CurrentSignalTest(unittest.TestCase):
    def test_only_latest_signal_within_ten_trading_days_is_active(self):
        history = [{"date": f"2026-10-{day:02d}", "close": 100.0} for day in range(1, 13)]
        signals = [
            {"date": "2026-10-01", "type": "bottom", "price": 99.0},
            {"date": "2026-10-02", "type": "top", "price": 101.0},
            {"date": "2026-10-03", "type": "bottom", "price": 98.0},
            {"date": "2026-10-04", "type": "top", "price": 102.0},
        ]
        self.assertEqual(current_signal(signals, history), {"type": "top", "date": "2026-10-04"})
        self.assertEqual(current_signal(signals[:2], history), {"type": "none", "date": None})
        self.assertEqual(current_signal(signals[:3], history), {"type": "bottom", "date": "2026-10-03"})


class EvaluationContractTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = build_indices()

    def test_real_seven_index_evaluation_is_valid(self):
        self.assertEqual(len(self.payload["indices"]), len(INDEXES))
        self.assertTrue(any(item["signals"] for item in self.payload["indices"]))
        self.assertTrue(all("evaluation" in item for item in self.payload["indices"]))
        validate_payload("index-signals.json", self.payload)

    def test_current_and_recent_signals_cannot_drift_from_facts(self):
        for action in ("current", "recent", "change"):
            payload = copy.deepcopy(self.payload)
            if action == "current":
                payload["indices"][0]["current_signal"] = {"type": "top", "date": "2026-10-01"}
            elif action == "recent":
                payload["recent_signals"].reverse()
            else:
                payload["recent_signals"][0]["change_since"] += 1
            with self.subTest(action=action), self.assertRaises(ValueError):
                validate_payload("index-signals.json", payload)

    def test_evaluation_remains_optional_for_legacy_payloads(self):
        payload = copy.deepcopy(self.payload)
        for item in payload["indices"]:
            del item["evaluation"]
        validate_payload("index-signals.json", payload)

    def test_wrong_counts_rates_and_small_sample_flags_are_rejected(self):
        for field, value in (("total", 999), ("resolved", 999), ("rate", 0.5),
                             ("rate", float("nan")), ("small_sample", False), ("note", None),
                             ("wilson95", [0.8, 0.1])):
            with self.subTest(field=field, value=value):
                payload = copy.deepcopy(self.payload)
                payload["indices"][0]["evaluation"]["segments"][0]["summary"]["bottom"][field] = value
                with self.assertRaises(ValueError):
                    validate_payload("index-signals.json", payload)

    def test_pending_cannot_have_a_confirmation_or_disappear(self):
        for action in ("confirm", "remove"):
            payload = copy.deepcopy(self.payload)
            part = payload["indices"][0]["evaluation"]["segments"][1]
            pending = [event for event in part["events"] if event["status"] == "pending"]
            if not pending:
                self.skipTest("当前行情末端没有未决事件，本用例依赖数据状态")
            event = pending[0]
            if action == "confirm":
                event["confirmation_date"] = part["end"]
            else:
                part["events"].remove(event)
            with self.assertRaises(ValueError):
                validate_payload("index-signals.json", payload)

    def test_duplicate_segments_events_and_independence_claims_are_rejected(self):
        for action in ("segment", "event", "independent"):
            payload = copy.deepcopy(self.payload)
            parts = payload["indices"][0]["evaluation"]["segments"]
            if action == "segment":
                parts[1] = copy.deepcopy(parts[0])
            elif action == "event":
                parts[0]["events"].append(copy.deepcopy(parts[0]["events"][0]))
            else:
                parts[1]["independent"] = True
            with self.assertRaises(Exception):
                validate_payload("index-signals.json", payload)

    def test_signal_price_confirmation_date_and_trading_days_are_checked(self):
        for key, value in (("price", 1), ("confirmation_date", "2025-01-02"),
                           ("confirmation_date", "2024-02-31"), ("confirmation_days", 999),
                           ("observed_trading_days", 999), ("reason", "opposite_signal")):
            payload = copy.deepcopy(self.payload)
            event = payload["indices"][0]["evaluation"]["segments"][0]["events"][0]
            event[key] = value
            with self.subTest(key=key), self.assertRaises(ValueError):
                validate_payload("index-signals.json", payload)

    def test_legacy_accuracy_cannot_drift_from_evaluation(self):
        for key, value in (("triggers", 999), ("successes", 999), ("rate", 1.0)):
            payload = copy.deepcopy(self.payload)
            payload["indices"][0]["accuracy"]["bottom"][key] = value
            with self.assertRaises(ValueError):
                validate_payload("index-signals.json", payload)

    def test_invalid_evaluation_preserves_previous_file(self):
        payload = copy.deepcopy(self.payload)
        payload["indices"][0]["evaluation"]["segments"][0]["summary"]["bottom"]["total"] += 1
        with tempfile.TemporaryDirectory() as temp:
            directory = Path(temp)
            target = directory / "index-signals.json"
            target.write_text('{"previous":true}', encoding="utf-8")
            before = target.read_bytes()
            with self.assertRaises(ValueError):
                protected_write("index-signals.json", payload, directory=directory)
            self.assertEqual(target.read_bytes(), before)


SECTOR_STATES = ("bull", "warn", "bear", "rise")


def _cell(samples: int = 100) -> dict:
    return {"entries": samples, "samples": samples, "successes": samples // 2, "pending": 0,
            "rate": 0.5, "wilson95": [0.4, 0.6], "small_sample": samples < 30, "note": None}


def _evaluation_payload() -> dict:
    """内存构造的四态评估载荷：不依赖生产产物，供契约拒收/放行用例使用。"""
    return {
        "algorithm_version": "sector-donchian20-ma60-v3.0-ths-source",
        "research_status": "post_hoc_exploratory_not_independent",
        "rate_unit": "fraction",
        "boundary_policy": "strict_segment_end_inclusive",
        "note": "板块趋势为描述性分类，方向胜率不代表交易收益。",
        "segments": [
            {"segment": segment, "independent": False,
             "states": {state: {"10": _cell(), "20": _cell()} for state in SECTOR_STATES}}
            for segment in ("research", "exploratory_validation")
        ],
    }


class SectorEvaluationSchemaTest(unittest.TestCase):
    """板块评估四态契约：四态齐备、无聚合 horizons、允许无样本空值（内存构造，不读产物）。"""

    def test_four_state_payload_is_valid(self):
        Draft202012Validator(SECTOR_EVALUATION).validate(_evaluation_payload())

    def test_state_without_samples_is_valid(self):
        payload = _evaluation_payload()
        payload["segments"][0]["states"]["rise"] = {"10": None, "20": None}
        Draft202012Validator(SECTOR_EVALUATION).validate(payload)

    def test_legacy_aggregate_horizons_is_rejected(self):
        payload = _evaluation_payload()
        for segment in payload["segments"]:
            segment.pop("states")
            segment["horizons"] = {"10": _cell(), "20": _cell()}
        with self.assertRaises(Exception):
            Draft202012Validator(SECTOR_EVALUATION).validate(payload)

    def test_missing_state_is_rejected(self):
        payload = _evaluation_payload()
        del payload["segments"][0]["states"]["bear"]
        with self.assertRaises(Exception):
            Draft202012Validator(SECTOR_EVALUATION).validate(payload)


class SectorContractTest(unittest.TestCase):
    """板块产物契约：单层 90 个行业板块、共享日期、序列末项与强度构成自洽。"""

    @classmethod
    def setUpClass(cls):
        payload = json.loads((ROOT / "data" / "sector-trends.json").read_text(encoding="utf-8"))
        cls.payload = convert_sector_payload_to_signed(payload)

    def _first(self, payload):
        return payload["groups"]["bull"][0]

    def test_real_payload_is_valid(self):
        validate_payload("sector-trends.json", self.payload)
        self.assertEqual(sum(len(rows) for rows in self.payload["groups"].values()), 90)

    def test_real_evaluation_is_split_by_four_states(self):
        """生产产物按四态拆分：四态齐备、每态 10/20 两档，且不再有聚合 horizons。"""
        for segment in self.payload["evaluation"]["segments"]:
            self.assertNotIn("horizons", segment)
            self.assertEqual(set(segment["states"]), set(SECTOR_STATES))
            for horizons in segment["states"].values():
                self.assertEqual(set(horizons), {"10", "20"})

    def test_payload_declares_em_single_level_source(self):
        self.assertEqual(self.payload["data_source"], "同花顺行业指数（不复权）")
        self.assertEqual(self.payload["algorithm_version"], "sector-donchian20-ma60-v3.0-ths-source")
        items = [item for rows in self.payload["groups"].values() for item in rows]
        ths_manifest = json.loads(
            (ROOT / "data" / "raw" / "sectors-ths-industry-manifest.json").read_text(encoding="utf-8"))
        self.assertEqual({item["code"] for item in items},
                         {item["code"] for item in ths_manifest["universe"]})

    def test_legacy_hierarchy_fields_are_rejected(self):
        for key, value in (("subsectors", []), ("level", "first"), ("parent", None)):
            payload = copy.deepcopy(self.payload)
            self._first(payload)[key] = value
            with self.subTest(key=key), self.assertRaises(Exception):
                validate_payload("sector-trends.json", payload)

    def test_legacy_top_level_keys_are_rejected(self):
        """旧两层产物的顶层键不得混入单层产物，否则等于两套结构共存。"""
        for key, value in (("subsector_evaluation", {}), ("subsectors", []),
                           ("boards", []), ("level", "first")):
            payload = copy.deepcopy(self.payload)
            payload[key] = value
            with self.subTest(key=key), self.assertRaises(Exception):
                validate_payload("sector-trends.json", payload)
        validate_payload("sector-trends.json", self.payload)  # 原载荷仍合法

    def test_series_length_must_match_shared_dates(self):
        payload = copy.deepcopy(self.payload)
        self._first(payload)["series"]["close"].pop()
        with self.assertRaises(ValueError):
            validate_payload("sector-trends.json", payload)

    def test_strength_must_match_parts(self):
        payload = copy.deepcopy(self.payload)
        item = self._first(payload)
        item["strength_parts"] = {"range_position": 80.0, "ma_score": 80.0}
        item["strength"] = 31.0
        with self.assertRaises(ValueError):
            validate_payload("sector-trends.json", payload)

    def test_signed_strength_is_valid(self):
        payload = copy.deepcopy(self.payload)
        item = self._first(payload)
        item["strength_parts"] = {"range_position": 80.0, "ma_score": 80.0}
        item["strength"] = 30.0
        item["strength_basis"] = "directional"
        validate_payload("sector-trends.json", payload)

    def test_negative_strength_is_valid(self):
        payload = copy.deepcopy(self.payload)
        item = self._first(payload)
        item["strength_parts"] = {"range_position": 20.0, "ma_score": 20.0}
        item["strength"] = -30.0
        item["strength_basis"] = "directional"
        validate_payload("sector-trends.json", payload)

    def test_old_inverted_strength_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        item = self._first(payload)
        item["strength_parts"] = {"range_position": 20.0, "ma_score": 20.0}
        item["strength"] = 80.0
        item["strength_basis"] = "inverted"
        with self.assertRaises(Exception):
            validate_payload("sector-trends.json", payload)

    def test_old_neutral_strength_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        item = self._first(payload)
        item["strength"] = 50.0
        item["strength_basis"] = "neutral"
        with self.assertRaises(ValueError):
            validate_payload("sector-trends.json", payload)

    def test_last_state_must_match_item_state(self):
        payload = copy.deepcopy(self.payload)
        item = self._first(payload)
        item["series"]["state"][-1] = "R" if item["series"]["state"][-1] != "R" else "B"
        with self.assertRaises(ValueError):
            validate_payload("sector-trends.json", payload)

    def test_duplicate_code_is_rejected(self):
        payload = copy.deepcopy(self.payload)
        duplicate = copy.deepcopy(self._first(payload))
        payload["groups"]["bear"].append(duplicate)
        with self.assertRaises(ValueError):
            validate_payload("sector-trends.json", payload)

    def test_sectors_are_independently_classified(self):
        items = [item for rows in self.payload["groups"].values() for item in rows]
        states = {item["state"] for item in items if item["state"] is not None}
        self.assertGreater(len(states), 1, "板块应各自独立判定四态，不只出一两种状态")
        for item in items:
            if item["state"] is None:
                continue
            self.assertEqual(item["strength_parts"] is not None, True)
            self.assertLessEqual(abs(item["strength"]), 50)


class EtfVolumePayloadTest(unittest.TestCase):
    """ETF 放量事件产物：合法载荷通过，四类坏数据必须抛错。"""

    @staticmethod
    def _payload() -> dict:
        pool = [{"code": item["code"], "name": item["name"], "index": item["index"]}
                for item in ETF_UNIVERSE]
        return {
            "as_of": "2026-11-05", "generated_at": "2026-11-05T20:00:00",
            "algorithm_version": "etf-volume-spike-v1.0", "threshold_amount": 1.0e10,
            "cluster_gap_days": 2, "mode_basis_index": "sh000300", "pool": pool,
            "days": [
                {"date": "2026-11-02", "level": "L1", "etf_codes": [pool[0]["code"]],
                 "total_amount": 1.5e10, "state": "中性过渡区"},
                {"date": "2026-11-03", "level": "L3",
                 "etf_codes": [item["code"] for item in pool],
                 "total_amount": 9.0e10, "state": "下跌底部区"},
            ],
            "events": [{"start_date": "2026-11-02", "end_date": "2026-11-03", "days": 2,
                        "level": "L3", "state": "下跌底部区", "peak_amount": 9.0e10}],
        }

    def test_valid_payload_passes(self):
        validate_payload("etf-volume-events.json", self._payload())

    def test_duplicate_day_is_rejected(self):
        payload = self._payload()
        payload["days"].append(copy.deepcopy(payload["days"][0]))
        with self.assertRaises(ValueError):
            validate_payload("etf-volume-events.json", payload)

    def test_unsorted_days_are_rejected(self):
        payload = self._payload()
        payload["days"].reverse()
        with self.assertRaises(ValueError):
            validate_payload("etf-volume-events.json", payload)

    def test_illegal_level_is_rejected(self):
        payload = self._payload()
        payload["days"][0]["level"] = "L4"
        with self.assertRaises(Exception):
            validate_payload("etf-volume-events.json", payload)

    def test_event_start_outside_days_is_rejected(self):
        payload = self._payload()
        payload["events"][0]["start_date"] = "2026-11-04"
        with self.assertRaises(ValueError):
            validate_payload("etf-volume-events.json", payload)

    def test_code_outside_pool_is_rejected(self):
        payload = self._payload()
        payload["days"][0]["etf_codes"] = ["000000"]
        with self.assertRaises(ValueError):
            validate_payload("etf-volume-events.json", payload)


class CompactWriterTest(unittest.TestCase):
    def test_roundtrip_and_inline_arrays(self):
        from io_utils import _dumps  # noqa: PLC0415

        payload = {"a": [1.5, 2, None], "b": {"c": [{"d": 1}]}, "e": []}
        text = _dumps(payload)
        self.assertEqual(json.loads(text), payload)
        self.assertIn('"a": [1.5, 2, null]', text)
        self.assertIn('"e": []', text)

    def test_compact_write_is_valid_json(self):
        payload = json.loads((ROOT / "data" / "sector-trends.json").read_text(encoding="utf-8"))
        payload = convert_sector_payload_to_signed(payload)
        with tempfile.TemporaryDirectory() as temp:
            target = protected_write("sector-trends.json", payload,
                                     directory=Path(temp), compact_arrays=True)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8")), payload)


if __name__ == "__main__":
    unittest.main()
