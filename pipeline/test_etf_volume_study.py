"""ETF 研究报告回归：合成数据、临时输出，禁止触网和写生产产物。"""

from __future__ import annotations

import json
import re
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import pandas as pd

import etf_volume_events as ev
import run_etf_volume_study as study


class EtfStudyReportTest(unittest.TestCase):
    def _payload(self):
        dates = pd.bdate_range("2025-01-02", periods=80).strftime("%Y-%m-%d").tolist()
        core = {
            item["code"]: pd.DataFrame(
                {
                    "date": dates,
                    "amount": [
                        1.1e10 if index % 8 == 0 else 6e9 for index in range(80)
                    ],
                }
            )
            for item in study.ETF_UNIVERSE
        }
        history = pd.DataFrame(
            {"date": dates, "close": [100 + index for index in range(80)]}
        )
        indices = {code: history for code in study.INDEXES}
        with (
            mock.patch.object(
                study, "load_inputs", return_value=(core, indices, dates[-1])
            ),
            mock.patch.object(study, "_sha256", return_value="synthetic-input"),
            mock.patch.object(
                study, "broad_candidates", side_effect=AssertionError("测试不得联网")
            ),
        ):
            return study.study(dates[0], dates[-1], with_broad=False)

    def test_every_displayed_rate_carries_sample_count_and_its_range(self):
        payload = self._payload()
        text = study.render_markdown(payload)
        for line in text.splitlines():
            # 胜率后必须紧跟自身样本和区间，不能借用其他分层的起止日期。
            for match in re.finditer(r"\d+\.\d+% \[[^\]]+\]", line):
                self.assertRegex(
                    line[match.end() :],
                    r"^（n=\d+，区间 \d{4}-\d{2}-\d{2} ~ \d{4}-\d{2}-\d{2}）",
                )
        layer = next(row for row in payload["stats"]["layers"] if row["key"] == "L3")
        cell = layer["horizons"]["20"]
        self.assertLess(cell["end"], layer["end"])
        self.assertIn(study._rate_text(cell), text)
        self.assertIn(study._baseline_text(cell, "baseline"), text)
        self.assertIn(study._baseline_text(cell, "normal_baseline"), text)
        self.assertNotEqual(cell["end"], cell["baseline_end"])
        self.assertIn("首日级别", text)
        self.assertIn("不参与首日收益分层", text)

    def test_questions_use_effective_horizon_range_and_three_verdicts(self):
        payload = self._payload()
        layers = {row["key"]: row for row in payload["stats"]["layers"]}
        question = next(
            row for row in payload["questions"] if row["id"] == "l3-beats-same-state"
        )
        cell = layers["L3"]["horizons"]["5"]
        self.assertEqual(question["range"], [cell["start"], cell["end"]])
        self.assertEqual(question["samples"], cell["samples"])
        self.assertTrue(
            all(row["verdict"] in study.VERDICTS for row in payload["questions"])
        )
        cell["verdict"] = ev.VERDICT_NO_BASELINE
        cell["baseline_samples"] = 0
        question = next(
            row
            for row in study.build_questions(payload["lists"], payload["stats"])
            if row["id"] == "l3-beats-same-state"
        )
        self.assertEqual(question["verdict"], ev.VERDICT_UNKNOWN)
        self.assertIn(ev.VERDICT_NO_BASELINE, question["evidence"])

    def test_load_inputs_retains_warmup_but_excludes_future_rows(self):
        dates = pd.bdate_range("2023-01-02", periods=500).strftime("%Y-%m-%d").tolist()
        frame = pd.DataFrame(
            {"date": dates, "close": range(100, 600), "amount": [1e9] * 500}
        )
        boundary = dates[-10]
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            for item in study.ETF_UNIVERSE:
                (root / f"{item['code']}.csv").touch()
            with (
                mock.patch.object(study, "ETF_DAILY_RAW", root),
                mock.patch.object(study, "read_etf_csv", return_value=frame),
                mock.patch.object(study, "read_index_csv", return_value=frame),
            ):
                core, indices, end = study.load_inputs(boundary)
        self.assertEqual(end, boundary)
        for source in [*core.values(), *indices.values()]:
            self.assertEqual(source["date"].iat[0], dates[0])
            self.assertEqual(source["date"].iat[-1], boundary)
            self.assertLess(source["date"].iat[0], study.START)

    def test_warmup_matches_full_history_without_expanding_sample_range(self):
        dates = pd.bdate_range("2023-01-02", periods=320).strftime("%Y-%m-%d").tolist()
        start, end = dates[260], dates[-1]
        history = pd.DataFrame(
            {"date": dates, "close": [100 + index for index in range(320)]}
        )
        core = {
            item["code"]: pd.DataFrame(
                {
                    "date": dates,
                    "amount": [
                        1.1e10 if index in (250, 259, 260, 275, 300) else 6e9
                        for index in range(320)
                    ],
                }
            )
            for item in study.ETF_UNIVERSE
        }
        with (
            mock.patch.object(
                study,
                "load_inputs",
                return_value=(
                    core,
                    {code: history for code in study.INDEXES},
                    end,
                ),
            ),
            mock.patch.object(study, "_sha256", return_value="synthetic-input"),
        ):
            payload = study.study(start, end, with_broad=False)
        self.assertEqual(payload["event_dates"], [dates[275], dates[300]])
        self.assertNotIn(
            start, payload["event_dates"], "跨研究起点的簇不能另造一个首日"
        )
        for event in payload["events"]:
            self.assertEqual(event["state"], ev.STATE_UPTREND)
            self.assertEqual(
                event["state"], ev.market_state(history, event["start_date"])
            )
            self.assertEqual(
                ev.market_state(study._clip(history, start, end), event["start_date"]),
                ev.STATE_NEUTRAL,
            )
        self.assertTrue(
            all(row["first_date"] >= start for row in payload["lists"]["core"]["rows"])
        )
        baselines = payload["stats"]["baselines"]
        for stat in [*baselines["same_state"].values(), baselines["normal_volume"]]:
            if stat["n"]:
                self.assertGreaterEqual(stat["start"], start)
                self.assertLessEqual(stat["end"], end)
        self.assertEqual(payload["stats"]["segments"]["cutoff"], dates[290])

    def test_broad_scan_honors_selected_start_date(self):
        frame = pd.DataFrame(
            {"date": ["2025-01-02", "2025-02-03"], "amount": [2e10, 3e10]}
        )
        with mock.patch.object(
            study, "fetch_etf_hist_sina", return_value=frame
        ) as fetch:
            result = study.broad_break_list(
                [{"code": "510300", "name": "合成测试"}], "2025-02-28", "2025-02-01"
            )
        fetch.assert_called_once_with("510300")
        self.assertEqual(result["rows"][0]["first_date"], "2025-02-03")
        self.assertEqual(result["rows"][0]["days"], 1)

    def test_previous_report_comparison_preserves_original_files(self):
        for old_hash in ("synthetic-input", "older-input"):
            with self.subTest(old_hash=old_hash), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                payload = self._payload()
                previous = root / "old.json"
                previous.write_text(
                    json.dumps({"input_hash": old_hash}), encoding="utf-8"
                )
                before = (previous.read_bytes(), previous.stat().st_mtime_ns)

                def write_local(name, data):
                    (root / name).write_text(
                        json.dumps(data, ensure_ascii=False), encoding="utf-8"
                    )

                with (
                    mock.patch.object(study, "RESEARCH", root),
                    mock.patch.object(study, "study", return_value=payload),
                    mock.patch.object(study, "write_json", side_effect=write_local),
                    mock.patch(
                        "sys.argv",
                        ["study", "--out", "v1.2.json", "--previous", "old.json"],
                    ),
                ):
                    study.main()
                self.assertEqual(
                    (previous.read_bytes(), previous.stat().st_mtime_ns), before
                )
                result = json.loads((root / "v1.2.json").read_text(encoding="utf-8"))
                changed = old_hash != payload["input_hash"]
                self.assertEqual(
                    result["comparison"],
                    {
                        "report": "old.json",
                        "input_hash": old_hash,
                        "input_changed": changed,
                    },
                )
                text = (root / "v1.2.md").read_text(encoding="utf-8")
                self.assertIn("输入已变更" if changed else "输入哈希相同", text)
                self.assertIn("保留原件", text)

    def test_versioned_output_does_not_overwrite_original_report(self):
        payload = self._payload()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            old_json, old_md = root / study.STUDY_JSON, root / study.STUDY_MD
            old_json.write_text("旧 JSON", encoding="utf-8")
            old_md.write_text("旧报告", encoding="utf-8")

            def write_local(name, data):
                (root / name).write_text(
                    json.dumps(data, ensure_ascii=False), encoding="utf-8"
                )

            with (
                mock.patch.object(study, "RESEARCH", root),
                mock.patch.object(study, "study", return_value=payload),
                mock.patch.object(study, "write_json", side_effect=write_local),
                mock.patch(
                    "sys.argv", ["study", "--skip-broad", "--out", "revision-v1.1.json"]
                ),
            ):
                study.main()
            self.assertEqual(old_json.read_text(encoding="utf-8"), "旧 JSON")
            self.assertEqual(old_md.read_text(encoding="utf-8"), "旧报告")
            self.assertEqual(
                json.loads((root / "revision-v1.1.json").read_text(encoding="utf-8")),
                payload,
            )
            self.assertEqual(
                (root / "revision-v1.1.md").read_text(encoding="utf-8"),
                study.render_markdown(payload),
            )


if __name__ == "__main__":
    unittest.main()
