"""对照入口测试：合成小数据端到端跑通、产物落研究目录、生产指纹不变、三态结论齐备。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import run_pivot_comparison as rpc


def synth_frame(length: int = 160, seed_price: float = 100.0) -> pd.DataFrame:
    """构造 ~35% 锯齿涨跌的合成日线，保证 ZigZag/分形能识别多个拐点。"""
    dates = pd.bdate_range("2021-01-04", periods=length)
    close, price = [], seed_price
    for i in range(length):
        phase = (i // 15) % 2
        price *= 1.02 if phase == 0 else 0.98
        close.append(price)
    return pd.DataFrame({
        "date": dates, "open": close, "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close], "close": close, "volume": [1e6] * length,
    })


def synthetic_frames() -> dict[str, pd.DataFrame]:
    return {f"sz{i:06d}": synth_frame() for i in (1, 2, 3)}


def fake_metric(*, rate, median=None, samples=40, signals=40):
    return {"pivot_samples": samples, "signal_count": signals,
            "capture": {"rate": rate, "hits": 0, "total": samples},
            "main_hit": {"rate": rate, "hits": 0, "total": samples},
            "precision": {"rate": rate, "hits": 0, "total": signals},
            "lag": {"median": median, "p90": median},
            "price_deviation": {"median": median, "p90": median}}


def fake_aggregate(validation: dict) -> dict:
    return {"train": dict(validation), "validation": validation}


class DecideTest(unittest.TestCase):
    def test_small_sample_forces_observe(self):
        cand = fake_aggregate(fake_metric(rate=0.9, median=1, samples=10))
        inc = fake_aggregate(fake_metric(rate=0.1, median=1, samples=10))
        result = rpc.decide(cand, inc)
        self.assertEqual(result["verdict"], "观察")
        self.assertIn("统计力不足", result["reason"])

    def test_all_not_worse_yields_replace(self):
        cand = fake_aggregate(fake_metric(rate=0.5, median=3, samples=40, signals=40))
        inc = fake_aggregate(fake_metric(rate=0.2, median=8, samples=40, signals=40))
        result = rpc.decide(cand, inc)
        self.assertEqual(result["verdict"], "更换建议")

    def test_lower_main_hit_yields_keep_current(self):
        cand = fake_aggregate(fake_metric(rate=0.1, median=20, samples=40))
        inc = fake_aggregate(fake_metric(rate=0.5, median=5, samples=40))
        result = rpc.decide(cand, inc)
        self.assertEqual(result["verdict"], "保留现行")

    def test_recall_inflation_flag_surfaced(self):
        cand = fake_aggregate(fake_metric(rate=0.6, median=1, samples=40, signals=500))
        inc = fake_aggregate(fake_metric(rate=0.2, median=5, samples=40, signals=40))
        result = rpc.decide(cand, inc)
        self.assertTrue(result["recall_inflation_flag"])
        self.assertIn("过量触发", result["reason"])

    def test_precision_veto_downgrades_to_observe(self):
        # 高召回（捕捉/主达标均远优于现行）但精确度低于现行 → 精确度硬门槛降级为观察
        cand_val = {"pivot_samples": 40, "signal_count": 647,
                    "capture": {"rate": 0.971}, "main_hit": {"rate": 0.971},
                    "precision": {"rate": 0.066},
                    "lag": {"median": 2.0}, "price_deviation": {"median": 0.022}}
        inc_val = {"pivot_samples": 40, "signal_count": 22,
                   "capture": {"rate": 0.118}, "main_hit": {"rate": 0.118},
                   "precision": {"rate": 0.182},
                   "lag": {"median": 2.0}, "price_deviation": {"median": 0.029}}
        result = rpc.decide({"train": {}, "validation": cand_val},
                            {"train": {}, "validation": inc_val})
        self.assertEqual(result["verdict"], "观察")
        self.assertIn("精确度硬门槛", result["reason"])


class BuildComparisonTest(unittest.TestCase):
    def setUp(self):
        self.frames = synthetic_frames()
        self.report = rpc.build_comparison(self.frames, breadth=None)

    def test_all_candidates_and_incumbent_present(self):
        expected = set(rpc.alg.PREREGISTERED["algorithm_params"]) | {rpc.INCUMBENT}
        self.assertEqual(set(self.report["candidates"]), expected)

    def test_every_candidate_has_main_metrics_both_segments(self):
        for name, entry in self.report["candidates"].items():
            for segment in rpc.SEGMENTS:
                metrics = entry["aggregate"][segment]
                for key in ("capture", "main_hit", "precision", "lag", "price_deviation"):
                    self.assertIn(key, metrics, f"{name}/{segment} 缺 {key}")

    def test_verdicts_valid_three_state(self):
        self.assertEqual(self.report["candidates"][rpc.INCUMBENT]["verdict"], "现行基线")
        for name, entry in self.report["candidates"].items():
            if name == rpc.INCUMBENT:
                continue
            self.assertIn(entry["verdict"], rpc.VERDICTS, name)
            self.assertTrue(entry["verdict_detail"]["reason"])

    def test_sensitivity_and_frozen_fields_present(self):
        self.assertIn("frozen", self.report)
        self.assertIn("validation_pivot_samples", self.report)
        self.assertIn("input_hash", self.report)
        for entry in self.report["candidates"].values():
            self.assertEqual(set(entry["sensitivity"]), {"3%", "5%", "7%"})


class ProductsAndFingerprintTest(unittest.TestCase):
    def test_default_products_target_research_dir(self):
        self.assertEqual(rpc.COMPARISON_JSON.parent.name, "research")
        self.assertEqual(rpc.COMPARISON_MD.parent.name, "research")
        self.assertNotIn("index-signals", str(rpc.COMPARISON_JSON))

    def test_write_products_produces_json_and_markdown_with_verdicts(self):
        report = rpc.build_comparison(synthetic_frames(), breadth=None)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            json_path, md_path = rpc.write_products(
                report, json_path=folder / "index-pivot-comparison.json",
                md_path=folder / "index-pivot-comparison.md")
            payload = json.loads(json_path.read_text(encoding="utf-8"))
            self.assertEqual(set(payload["candidates"]),
                             set(rpc.alg.PREREGISTERED["algorithm_params"]) | {rpc.INCUMBENT})
            markdown = md_path.read_text(encoding="utf-8")
            for name in payload["candidates"]:
                self.assertIn(name, markdown)
            self.assertTrue(any(verdict in markdown for verdict in rpc.VERDICTS))

    def test_research_write_does_not_change_production_fingerprints(self):
        before = rpc.fingerprints()
        report = rpc.build_comparison(synthetic_frames(), breadth=None)
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            rpc.write_products(report, json_path=folder / "a.json", md_path=folder / "a.md")
        self.assertEqual(before, rpc.fingerprints(), "研究写盘不得改动生产文件指纹")


if __name__ == "__main__":
    unittest.main()
