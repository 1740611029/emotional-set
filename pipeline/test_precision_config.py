"""标的清单、三段划分、28 条幅度线与冻结参数集的配置测试（任务 1.1 / 1.3 / 2.4）。"""

from __future__ import annotations

import unittest

import precision_config as cfg


class TestUniverseManifest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = cfg.load_frames()
        cls.manifest = cfg.build_manifest(cls.frames)

    def test_seven_fixed_indices(self):
        self.assertEqual(self.manifest["universe_size"], 7)
        self.assertEqual(
            [item["code"] for item in self.manifest["entries"]], list(cfg.INDEXES)
        )
        self.assertEqual(self.manifest["source"], "新浪公开指数日线")
        self.assertEqual(self.manifest["adjust"], "不复权")

    def test_manifest_matches_actual_csv(self):
        required = (
            "code",
            "name",
            "source",
            "adjust",
            "rows",
            "start",
            "end",
            "relative_path",
            "history_sufficient",
        )
        for item in self.manifest["entries"]:
            for key in required:
                self.assertIn(key, item)
            frame = self.frames[item["code"]]
            self.assertEqual(item["rows"], len(frame))
            self.assertEqual(item["start"], frame["date"].min().strftime("%Y-%m-%d"))
            self.assertEqual(item["end"], frame["date"].max().strftime("%Y-%m-%d"))
            self.assertTrue((cfg.ROOT / item["relative_path"]).exists())

    def test_insufficient_history_is_marked_not_dropped(self):
        # 全部 7 个标的均满足 1250 日；不足时 MUST 显式标记而非静默丢弃。
        for item in self.manifest["entries"]:
            self.assertIsInstance(item["history_sufficient"], bool)
            if not item["history_sufficient"]:
                self.assertIsNotNone(item["insufficient_history_note"])
        self.assertEqual(len(self.manifest["entries"]), 7)


class TestSegments(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.segments = cfg.segment_definition(cfg.read_index_csv("sh000001"))

    def test_train_end_is_fixed(self):
        self.assertEqual(self.segments["train"]["end"], "2024-12-31")
        self.assertEqual(self.segments["train_nominal_end"], "2024-12-31")

    def test_embargo_at_least_30_trading_days(self):
        self.assertGreaterEqual(self.segments["embargo"]["trading_days"], 30)
        self.assertGreaterEqual(
            self.segments["embargo_trading_days"], self.segments["embargo_min_required"]
        )
        self.assertTrue(self.segments["embargo"]["excluded_from_tuning"])
        self.assertTrue(self.segments["embargo"]["excluded_from_train_accuracy"])

    def test_no_overlap_between_train_and_validation(self):
        self.assertTrue(self.segments["train_validation_no_overlap"])
        self.assertLess(
            self.segments["train"]["end"], self.segments["validation"]["start"]
        )

    def test_effective_train_end_excludes_embargo(self):
        self.assertLess(
            self.segments["train"]["effective_end"], self.segments["embargo"]["start"]
        )


class TestAmplitudeLines(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.frames = cfg.load_frames()
        cls.lines = cfg.freeze_amplitude_lines(cls.frames)

    def test_28_lines(self):
        self.assertEqual(self.lines["line_count"], 28)
        self.assertEqual(len(self.lines["lines"]), 7)
        for code in cfg.INDEXES:
            self.assertEqual(
                set(self.lines["lines"][code]), {str(h) for h in cfg.HOLD_PERIODS}
            )

    def test_lines_reflect_index_volatility(self):
        # 创业板波动高于上证，各持有期线 MUST 相应更高。
        for horizon in cfg.HOLD_PERIODS:
            key = str(horizon)
            self.assertGreater(
                self.lines["lines"]["sz399006"][key],
                self.lines["lines"]["sh000001"][key],
            )

    def test_lines_increase_with_horizon(self):
        for code in cfg.INDEXES:
            values = [self.lines["lines"][code][str(h)] for h in cfg.HOLD_PERIODS]
            self.assertEqual(values, sorted(values), code)

    def test_reproducible_and_quantile_basis(self):
        again = cfg.freeze_amplitude_lines(cfg.load_frames())
        self.assertEqual(self.lines["lines"], again["lines"])
        self.assertEqual(self.lines["quantile"], 0.75)
        self.assertTrue(self.lines["validation_must_not_adjust"])
        for evidence in self.lines["evidence"].values():
            self.assertEqual(evidence["train_end"], "2024-12-31")
            self.assertGreater(evidence["train_rows"], 0)

    def test_amplitude_line_missing_raises(self):
        with self.assertRaises(KeyError):
            cfg.amplitude_line(self.lines, "not-a-code", 20)
        with self.assertRaises(KeyError):
            cfg.amplitude_line(self.lines, "sh000001", 99)


class TestFrozenParams(unittest.TestCase):
    def test_frozen_set_has_required_fields(self):
        frozen = cfg.frozen_params(cfg.load_frames())
        for key in (
            "pivot_amplitude",
            "pivot_min_gap",
            "six_factor_gate",
            "mae_limit",
            "mae_window",
            "effective_amplitude",
            "input_hash",
            "random_seed",
            "baseline_seed",
            "algorithm_version",
        ):
            self.assertIn(key, frozen)
        self.assertEqual(frozen["mae_limit"], 0.05)
        self.assertEqual(frozen["pivot_amplitude"], 0.05)
        self.assertEqual(frozen["pivot_min_gap"], 8)
        self.assertTrue(frozen["validation_must_not_adjust"])

    def test_input_hash_is_stable(self):
        self.assertEqual(cfg.input_hash(), cfg.input_hash())
        self.assertEqual(len(cfg.input_hash()), 64)

    def test_same_input_reproduces_same_lines(self):
        first = cfg.frozen_params(cfg.load_frames())
        second = cfg.frozen_params(cfg.load_frames())
        self.assertEqual(
            first["effective_amplitude"]["lines"],
            second["effective_amplitude"]["lines"],
        )
        self.assertEqual(first["input_hash"], second["input_hash"])


if __name__ == "__main__":
    unittest.main()
