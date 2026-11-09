"""真实拐点基准集测试：人工小序列逐值验证拐点日期/类型/价格，以及阈值/间隔抑制。"""
from __future__ import annotations

import unittest

import pandas as pd

from pivot_benchmark import annotate_pivots


def make_frame(close: list[float]) -> pd.DataFrame:
    dates = pd.bdate_range("2021-01-04", periods=len(close))
    return pd.DataFrame({
        "date": dates,
        "open": close, "high": [c * 1.01 for c in close],
        "low": [c * 0.99 for c in close], "close": close,
        "volume": [1e6] * len(close),
    })


def by_index(pivots: list[dict]) -> dict[int, dict]:
    return {row["index"]: row for row in pivots}


class AnnotateTest(unittest.TestCase):
    def test_full_cycle_marks_bottom_top_bottom_with_exact_prices(self):
        close = [100, 105, 115, 125, 140, 150, 140, 130, 120, 110, 100, 95, 110, 120, 130]
        frame = make_frame(close)
        pivots = annotate_pivots(frame, amplitude=0.05, min_gap=3)
        got = {row["index"]: (row["type"], row["price"]) for row in pivots}
        # 起点为上涨段低点（底部），idx5=150 为峰顶，idx11=95 为随后谷值。
        self.assertEqual(got, {0: ("bottom", 100.0), 5: ("top", 150.0), 11: ("bottom", 95.0)})
        for row in pivots:
            self.assertEqual(row["date"], frame["date"].iat[row["index"]].strftime("%Y-%m-%d"))
            self.assertGreater(row["confirm_index"], row["index"])  # 事后确认
            self.assertEqual(row["confirm_date"], frame["date"].iat[row["confirm_index"]].strftime("%Y-%m-%d"))

    def test_types_strictly_alternate(self):
        close = [100, 105, 115, 125, 140, 150, 140, 130, 120, 110, 100, 95, 110, 125, 140, 155, 140, 120]
        pivots = annotate_pivots(make_frame(close), amplitude=0.05, min_gap=3)
        types = [row["type"] for row in pivots]
        self.assertTrue(all(a != b for a, b in zip(types, types[1:])), types)

    def test_monotone_decline_marks_starting_high_only_no_bottom(self):
        close = [120, 118, 116, 115, 114, 113, 112, 111, 110]
        pivots = annotate_pivots(make_frame(close), amplitude=0.05, min_gap=3)
        self.assertEqual([(row["index"], row["type"]) for row in pivots], [(0, "top")])

    def test_reversal_below_threshold_is_not_annotated(self):
        # 峰谷间仅 ~4% 波动，未达 5% 反向阈值 → 不产生任何拐点
        close = [100, 101, 102, 103, 104, 103, 102, 101, 100]
        pivots = annotate_pivots(make_frame(close), amplitude=0.05, min_gap=3)
        self.assertEqual(pivots, [])

    def test_min_gap_suppresses_too_close_pivot(self):
        # idx0=100 为底，峰在 idx3=140（距底仅 3 间隔但 min_gap=5 抑制），随后一路阴跌不再创新高。
        close = [100, 110, 120, 140, 130, 120, 110, 100, 95, 90]
        pivots = annotate_pivots(make_frame(close), amplitude=0.05, min_gap=5)
        indices = [row["index"] for row in pivots]
        self.assertNotIn(3, indices, "间隔不足的峰 MUST NOT 被标注")
        # 只保留底部起点（峰因间隔不足被抑制，之后无更高等待确认）
        self.assertEqual([(row["index"], row["type"]) for row in pivots], [(0, "bottom")])

    def test_no_annotation_when_history_too_short(self):
        self.assertEqual(annotate_pivots(make_frame([100, 101]), amplitude=0.05, min_gap=8), [])
        self.assertEqual(annotate_pivots(make_frame([100]), amplitude=0.05, min_gap=8), [])


if __name__ == "__main__":
    unittest.main()
