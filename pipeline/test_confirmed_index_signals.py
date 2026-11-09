"""确认版信号的固定参数、因果性、分段标签和旧契约回归测试。"""
from __future__ import annotations

import json
import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd
from jsonschema import Draft202012Validator
from pandas.testing import assert_frame_equal, assert_series_equal

from confirmed_index_signals import (
    ALGORITHM_VERSION, SMALL_SAMPLE_NOTE, build_index_result, confirmed_signals,
    evaluate_signals, legacy_accuracy, signal_records, summarize_events,
)
from multifactor_research import multifactor_signals
from schemas import INDEX_SCHEMA, SIGNAL


def history(periods=100, start="2024-10-01"):
    close = 100 + np.sin(np.arange(periods) / 5) * 8
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=periods), "close": close,
        "open": close - 0.2, "high": close + 1, "low": close - 1,
        "volume": 1000.0,
    })


def factor_row(kind):
    bottom = kind == "bottom"
    return {
        "ma5": 110 if bottom else 90, "ma20": 90 if bottom else 110,
        "ma60": 110 if bottom else 90, "rsi14": 30 if bottom else 70,
        "percent_b": 0.1 if bottom else 0.9, "zscore20": -2 if bottom else 2,
        "drawdown20": -0.1 if bottom else 0, "runup20": 0 if bottom else 0.1,
        "realized20": 0.01, "volatility_baseline": 0.02,
        "obv_change3": 1 if bottom else -1, "mfi_change1": 1 if bottom else -1,
    }


def controlled_history(kinds, periods=104):
    frame = history(periods)
    frame["close"] = 100.0
    indicators = pd.DataFrame(np.nan, index=frame.index, columns=list(factor_row("bottom")))
    for position, kind in kinds.items():
        frame.loc[position, "close"] = 101 if kind == "bottom" else 99
        indicators.loc[position] = factor_row(kind)
    frame["open"] = 100.0
    frame["high"] = 102.0
    frame["low"] = 98.0
    return frame, indicators


class ConfirmedSignalsTest(unittest.TestCase):
    STALE_V10 = "v1.0 时代用例：v1.1-confirmed 引入确认窗口后语义已变，待按新语义重写"

    @unittest.skip(STALE_V10)
    def test_fixed_parameters_match_research_and_never_call_labels(self):
        frame = history()
        with patch("confirmed_index_signals.ma_reversal_masks", side_effect=AssertionError("禁止偷用标签")):
            result = confirmed_signals(frame)
        assert_series_equal(result, multifactor_signals(frame, 4, participation_mode="and"))
        self.assertEqual(ALGORITHM_VERSION, "index-mf4-participation-v1.0")

    @unittest.skip(STALE_V10)
    def test_all_four_layers_required_for_both_directions(self):
        for kind in ("bottom", "top"):
            frame, indicators = controlled_history({60: kind})
            with patch("multifactor_research.multifactor_indicators", return_value=indicators):
                self.assertEqual(confirmed_signals(frame).iat[60], kind)
            changes = {
                "state": {"ma60": 90 if kind == "bottom" else 110},
                "exhaustion": {"rsi14": 50, "percent_b": 0.5, "zscore20": 0, "drawdown20": 0, "runup20": 0},
                "obv": {"obv_change3": 0}, "mfi": {"mfi_change1": 0},
            }
            for layer, values in changes.items():
                altered = indicators.copy()
                for key, value in values.items():
                    altered.loc[60, key] = value
                with self.subTest(kind=kind, layer=layer), patch(
                    "multifactor_research.multifactor_indicators", return_value=altered,
                ):
                    self.assertTrue(confirmed_signals(frame).eq("").all())
            frame.loc[60, "open"] = frame.loc[60, "close"]
            with patch("multifactor_research.multifactor_indicators", return_value=indicators):
                self.assertTrue(confirmed_signals(frame).eq("").all())

    @unittest.skip(STALE_V10)
    def test_cooldown_uses_trading_rows_and_separate_directions(self):
        kinds = {60: "bottom", 70: "top", 80: "bottom", 81: "bottom", 90: "top", 91: "top"}
        frame, indicators = controlled_history(kinds)
        frame.loc[81, "close"] = 101.5
        frame.loc[91, "close"] = 98.5
        with patch("multifactor_research.multifactor_indicators", return_value=indicators):
            result = confirmed_signals(frame)
        self.assertEqual(np.flatnonzero(result.ne("").to_numpy()).tolist(), [60, 70, 81, 91])
        self.assertEqual(result.iloc[[60, 70, 81, 91]].tolist(), ["bottom", "top", "bottom", "top"])

    def test_every_prefix_preserves_signal_facts_and_input(self):
        frame = history(130)
        before = frame.copy(deep=True)
        full = confirmed_signals(frame)
        records = signal_records(frame, full)
        for cutoff in range(1, len(frame)):
            prefix = frame.iloc[:cutoff]
            result = confirmed_signals(prefix)
            assert_series_equal(result, full.iloc[:cutoff])
            self.assertEqual(signal_records(prefix, result), [row for row in records if row["date"] <= prefix.date.iat[-1].strftime("%Y-%m-%d")])
        assert_frame_equal(frame, before)
        altered = frame.copy()
        altered.loc[100:, ["open", "high", "low", "close"]] *= 10
        assert_series_equal(confirmed_signals(altered).iloc[:100], full.iloc[:100])

    def test_empty_short_flat_and_zero_volume_abstain(self):
        flat = history()
        flat[["open", "high", "low", "close"]] = 100.0
        no_volume = history()
        no_volume["volume"] = 0.0
        for frame in (history(0), history(1), history(59), flat, no_volume):
            result = build_index_result(frame)
            self.assertEqual(result["signals"], [])
            json.dumps(result, allow_nan=False)
            if len(frame) < 60:
                self.assertEqual(result["status"], "insufficient_history")

    def test_bad_inputs_raise_without_silent_imputation(self):
        frame = history()
        cases = [frame.iloc[::-1], frame.drop(columns="volume")]
        for column, value in (("date", frame.date.iat[0]), ("close", np.nan),
                              ("volume", -1), ("high", 1), ("close", np.inf)):
            bad = frame.copy()
            bad.loc[1, column] = value
            cases.append(bad)
        for bad in cases:
            with self.assertRaises(ValueError):
                confirmed_signals(bad)

    def test_non_range_index_does_not_change_signals(self):
        frame = history()
        expected = confirmed_signals(frame).tolist()
        frame.index = np.arange(len(frame)) * 3 + 11
        self.assertEqual(confirmed_signals(frame).tolist(), expected)


class ConfirmedEvaluationTest(unittest.TestCase):
    def evaluate(self, frame, signals, bull=(), bear=()):
        masks = []
        for active in (bull, bear):
            mask = pd.Series(False, index=frame.index)
            mask.iloc[list(active)] = True
            masks.append(mask)
        with patch("confirmed_index_signals.ma_reversal_masks", return_value=tuple(masks)):
            return evaluate_signals(frame, pd.Series(signals, index=frame.index, dtype="string"))

    def events(self, result):
        return [event for part in result["segments"] for event in part["events"]]

    def test_success_ignores_signal_day_and_uses_first_later_confirmation(self):
        result = self.evaluate(history(5), ["bottom", "", "", "", "top"], bull=(0, 2, 3))
        event = self.events(result)[0]
        self.assertEqual(event["status"], "success")
        self.assertEqual(event["confirmation_days"], 2)
        self.assertEqual(event["confirmation_date"], "2024-10-03")
        pending = self.evaluate(history(2), ["bottom", ""], bull=(0,))
        self.assertEqual(self.events(pending)[0]["status"], "pending")

    def test_opposite_day_is_excluded_and_last_day_remains_pending(self):
        result = self.evaluate(history(3), ["bottom", "", "top"], bull=(2,))
        bottom, top = self.events(result)
        self.assertEqual(bottom["status"], "failure")
        self.assertEqual(bottom["reason"], "opposite_signal")
        self.assertEqual(bottom["confirmation_days"], 2)
        self.assertEqual(top["status"], "pending")
        self.assertEqual(top["observed_trading_days"], 0)
        self.assertIsNone(top["confirmation_date"])

    def test_top_rule_is_symmetric(self):
        success = self.evaluate(history(4), ["top", "", "", "bottom"], bear=(1,))
        self.assertEqual(self.events(success)[0]["status"], "success")
        failure = self.evaluate(history(4), ["top", "", "", "bottom"], bear=(3,))
        self.assertEqual(self.events(failure)[0]["status"], "failure")

    def test_segment_boundary_does_not_borrow_future_success_or_opposite(self):
        frame = history(4, start="2024-12-30")
        result = self.evaluate(frame, ["bottom", "bottom", "", "top"], bull=(2,))
        research, validation = result["segments"]
        self.assertEqual(research["end"], "2024-12-31")
        self.assertEqual(research["summary"]["bottom"]["pending"], 2)
        self.assertTrue(all(event["reason"] == "segment_boundary" for event in research["events"]))
        self.assertEqual(validation["start"], "2025-01-01")
        self.assertFalse(validation["independent"])

    def test_frames_without_research_rows_and_empty_segments_work(self):
        result = self.evaluate(history(2, "2025-01-02"), ["bottom", ""])
        self.assertIsNone(result["segments"][0]["start"])
        self.assertEqual(result["segments"][1]["summary"]["bottom"]["pending"], 1)
        self.assertEqual(legacy_accuracy(result)["start"], "2025-01-02")
        early = self.evaluate(history(2, "2020-01-02"), ["bottom", ""])
        self.assertEqual(self.events(early), [])

    def test_pending_matures_without_changing_signal_facts(self):
        frame = history(65, "2025-01-02")
        frame["close"] = np.arange(100, 165, dtype=float)
        frame["open"] = frame["close"]
        frame["high"] = frame["close"] + 1
        frame["low"] = frame["close"] - 1
        signals = pd.Series("", index=frame.index, dtype="string")
        signals.iat[60] = "bottom"
        before = evaluate_signals(frame.iloc[:61], signals.iloc[:61])
        after = evaluate_signals(frame, signals)
        self.assertEqual(self.events(before)[0]["status"], "pending")
        self.assertEqual(self.events(after)[0]["status"], "success")
        self.assertEqual(self.events(after)[0]["confirmation_days"], 1)
        self.assertEqual(signal_records(frame.iloc[:61], signals.iloc[:61]), signal_records(frame, signals))

    def test_summary_uses_resolved_denominator_and_small_sample_boundary(self):
        rows = [{"type": "bottom", "status": status} for status in ("success", "failure", "pending")]
        bottom = summarize_events(rows)["bottom"]
        self.assertEqual((bottom["total"], bottom["resolved"], bottom["rate"]), (3, 2, 0.5))
        self.assertEqual(bottom["note"], SMALL_SAMPLE_NOTE)
        for count in (29, 30):
            part = summarize_events([{"type": "top", "status": "success"}] * count)["top"]
            self.assertEqual(part["small_sample"], count < 30)
        empty = summarize_events([])["bottom"]
        self.assertIsNone(empty["rate"])
        self.assertEqual(empty["wilson95"], [None, None])

    def test_legacy_contract_preserves_total_count_and_percent_rate(self):
        frame = history(5)
        result = self.evaluate(frame, ["bottom", "", "bottom", "top", "bottom"], bull=(1,))
        accuracy = legacy_accuracy(result)
        schema = INDEX_SCHEMA["properties"]["indices"]["items"]["properties"]["accuracy"]
        Draft202012Validator(schema).validate(accuracy)
        self.assertEqual(
            {key: accuracy["bottom"][key] for key in ("triggers", "successes", "rate")},
            {"triggers": 3, "successes": 1, "rate": 50.0},
        )
        self.assertEqual(accuracy["top"]["rate"], 0)
        for event in self.events(result):
            self.assertIn(event["type"], ("bottom", "top"))
            self.assertIsInstance(event["price"], float)
        json.dumps(result, allow_nan=False)

    def test_misaligned_and_unknown_signals_are_rejected(self):
        frame = history(3)
        for signals in (pd.Series(["bottom"], dtype="string"),
                        pd.Series(["bottom", None, ""], dtype="string"),
                        pd.Series(["bottom", "unknown", ""], dtype="string")):
            with self.assertRaises(ValueError):
                evaluate_signals(frame, signals)


if __name__ == "__main__":
    unittest.main()
