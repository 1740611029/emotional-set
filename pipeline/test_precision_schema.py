"""矩阵字段结构校验、失败不半写、保留上一版（任务 5.1）。"""
from __future__ import annotations

import copy
import json
import tempfile
import unittest
from pathlib import Path

from jsonschema import Draft202012Validator, ValidationError

from io_utils import protected_write
from precision_config import INDEXES
from schemas import PRECISION_CELL, PRECISION_MATRIX, validate_payload, validate_precision

ROOT = Path(__file__).resolve().parents[1]
RESEARCH = ROOT / "data" / "research"


def sample_payload() -> dict:
    research = json.loads((RESEARCH / "precision-matrix.json").read_text(encoding="utf-8"))
    indices = [
        {"code": code,
         "precision_matrix": research["legacy_algorithm_re_evaluated"][code]}
        for code in INDEXES
    ]
    precision = {
        "algorithm_version": "index-precision-pivot-v1.0",
        "signal_algorithm_version": "index-swing6-v2.0",
        "research_status": "post_hoc_exploratory_not_independent",
        "covered_universe": list(INDEXES),
        "universe_size": 7,
        "segments": research["segments"],
        "horizons": [5, 10, 20, 30],
        "criteria_note": "固定持有期方向正确 + 20 日盘中浮亏不超过 5%",
        "legacy_rate_note": "历史基线：旧聚合 rate 与矩阵口径不可直接比较",
        "effective_amplitude_lines": {"sh000001": {"5": 0.02}},
        "decision": "观察",
        "pivot_anchors": {},
        "comparison": {},
    }
    return {"indices": indices, "precision": precision}


class TestPrecisionCellSchema(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.payload = sample_payload()
        cls.cell = cls.payload["indices"][0]["precision_matrix"]["train"]["horizons"]["20"]["bottom"]

    def test_valid_cell_passes(self):
        Draft202012Validator(PRECISION_CELL).validate(self.cell)

    def test_missing_any_required_field_fails(self):
        required = PRECISION_CELL["required"]
        self.assertGreaterEqual(len(required), 19)
        for key in required:
            broken = copy.deepcopy(self.cell)
            broken.pop(key, None)
            with self.assertRaises(ValidationError, msg=f"缺字段 {key} 居然通过"):
                Draft202012Validator(PRECISION_CELL).validate(broken)

    def test_missing_matrix_horizon_fails(self):
        matrix = copy.deepcopy(self.payload["indices"][0]["precision_matrix"])
        matrix["train"]["horizons"].pop("30")
        with self.assertRaises(ValidationError):
            Draft202012Validator(PRECISION_MATRIX).validate(matrix)

    def test_missing_direction_fails(self):
        matrix = copy.deepcopy(self.payload["indices"][0]["precision_matrix"])
        matrix["validation"]["horizons"]["10"].pop("top")
        with self.assertRaises(ValidationError):
            Draft202012Validator(PRECISION_MATRIX).validate(matrix)


class TestCrossFieldConsistency(unittest.TestCase):
    def test_valid_payload_passes(self):
        validate_precision(sample_payload())

    def test_trigger_conservation_violation_fails(self):
        payload = sample_payload()
        payload["indices"][0]["precision_matrix"]["train"]["horizons"]["5"]["bottom"]["triggers"] += 1
        with self.assertRaises(ValueError):
            validate_precision(payload)

    def test_rate_mismatch_fails(self):
        payload = sample_payload()
        cell = payload["indices"][0]["precision_matrix"]["train"]["horizons"]["5"]["bottom"]
        if cell["direction_correct_rate"] is not None:
            cell["direction_correct_rate"] = min(1.0, cell["direction_correct_rate"] + 0.1)
            with self.assertRaises(ValueError):
                validate_precision(payload)

    def test_universe_mismatch_fails(self):
        payload = sample_payload()
        payload["precision"]["covered_universe"] = list(reversed(list(INDEXES)))
        with self.assertRaises(ValueError):
            validate_precision(payload)

    def test_legacy_baseline_label_required(self):
        payload = sample_payload()
        payload["precision"]["legacy_rate_note"] = "无标注"
        with self.assertRaises(ValueError):
            validate_precision(payload)


class TestRollbackCompatibility(unittest.TestCase):
    """任务 7.4：恢复上一版产物后仍须通过结构校验。"""

    def test_previous_version_without_precision_passes(self):
        previous = json.loads(
            (ROOT / "data" / "research" / "precision-backup" / "index-signals.json")
            .read_text(encoding="utf-8"))
        self.assertNotIn("precision", previous)
        validate_payload("index-signals.json", previous)

    def test_partial_matrix_is_rejected(self):
        current = json.loads((ROOT / "data" / "index-signals.json").read_text(encoding="utf-8"))
        current["indices"][0].pop("precision_matrix", None)
        with self.assertRaises(ValueError):
            validate_payload("index-signals.json", current)

    def test_current_version_still_passes(self):
        current = json.loads((ROOT / "data" / "index-signals.json").read_text(encoding="utf-8"))
        validate_payload("index-signals.json", current)


class TestProtectedWrite(unittest.TestCase):
    def test_failed_validation_keeps_previous_version_and_no_partial_file(self):
        with tempfile.TemporaryDirectory() as folder:
            directory = Path(folder)
            target = directory / "index-signals.json"
            target.write_text("PREVIOUS_VERSION", encoding="utf-8")
            with self.assertRaises(Exception):
                protected_write("index-signals.json", {}, directory=directory)
            self.assertEqual(target.read_text(encoding="utf-8"), "PREVIOUS_VERSION")
            self.assertEqual(list(directory.glob("*.tmp")), [])


if __name__ == "__main__":
    unittest.main()
