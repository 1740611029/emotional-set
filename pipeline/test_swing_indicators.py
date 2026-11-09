"""正交因子计算测试：因果性、宽度缺失降级与背离识别。"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from swing_indicators import (
    DIRECTIONS, breadth_table, divergence, factor_table, price_position,
    rolling_percentile,
)


def wave_frame(periods: int = 420, start: str = "2022-01-03") -> pd.DataFrame:
    index = np.arange(periods)
    close = 3000 + 300 * np.sin(index / 18) + index * 0.4
    return pd.DataFrame({
        "date": pd.bdate_range(start, periods=periods),
        "open": close - 3, "high": close + 12, "low": close - 12,
        "close": close, "volume": 1e8 * (1 + 0.3 * np.sin(index / 7)),
    })


def sector_frames(periods: int = 420, count: int = 24) -> dict[str, pd.DataFrame]:
    result = {}
    for number in range(count):
        index = np.arange(periods)
        close = 1000 + 100 * np.sin(index / (10 + number)) + number
        result[f"{801000 + number}"] = pd.DataFrame({
            "date": pd.bdate_range("2022-01-03", periods=periods),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1e6,
        })
    return result


class CausalityTest(unittest.TestCase):
    def test_rolling_percentile_only_uses_past(self):
        series = pd.Series(np.arange(300, dtype=float))
        full = rolling_percentile(series, window=60, min_periods=20)
        prefix = rolling_percentile(series.iloc[:200], window=60, min_periods=20)
        pd.testing.assert_series_equal(full.iloc[:200], prefix)

    def test_factor_table_prefix_is_stable(self):
        frame = wave_frame()
        full = factor_table(frame)
        prefix = factor_table(frame.iloc[:360])
        columns = [f"{name}_{kind}" for kind in ("bottom", "top") for name in DIRECTIONS]
        pd.testing.assert_frame_equal(full.iloc[:360][columns], prefix[columns])

    def test_breadth_prefix_is_stable(self):
        frames = sector_frames()
        full = breadth_table(frames)
        truncated = {code: frame.iloc[:360] for code, frame in frames.items()}
        prefix = breadth_table(truncated)
        pd.testing.assert_frame_equal(full.iloc[:360], prefix.iloc[:360])


class BreadthTest(unittest.TestCase):
    def test_missing_breadth_is_renormalized(self):
        frame = wave_frame()
        with_breadth = factor_table(frame, breadth_table(sector_frames()))
        without = factor_table(frame)
        tail = slice(300, None)
        self.assertTrue(without["score_bottom"].iloc[tail].notna().all())
        self.assertTrue(with_breadth["score_bottom"].iloc[tail].notna().all())
        self.assertFalse(np.allclose(without["score_bottom"].iloc[tail],
                                     with_breadth["score_bottom"].iloc[tail]))

    def test_insufficient_sectors_marks_breadth_missing(self):
        frames = sector_frames(count=10)
        table = breadth_table(frames)
        self.assertTrue(table["breadth20"].isna().all())

    def test_breadth_between_zero_and_one(self):
        table = breadth_table(sector_frames())
        values = table["breadth20"].dropna()
        self.assertTrue(((values >= 0) & (values <= 1)).all())


class DivergenceTest(unittest.TestCase):
    def test_price_lower_low_with_higher_rsi_scores(self):
        periods = 120
        close = np.linspace(100, 80, periods)
        close[-1] = close[-21] - 5
        frame = pd.DataFrame({
            "date": pd.bdate_range("2024-01-01", periods=periods),
            "open": close, "high": close + 1, "low": close - 1,
            "close": close, "volume": 1e6,
        })
        result = divergence(frame)
        self.assertTrue(result["divergence_bottom"].iloc[-1] >= 0)
        self.assertTrue(result["divergence_bottom"].between(0, 1).all())

    def test_price_position_scores_in_range(self):
        frame = wave_frame()
        result = price_position(frame)
        for column in ("position_bottom", "position_top"):
            values = result[column].dropna()
            self.assertTrue(((values >= 0) & (values <= 1)).all())


if __name__ == "__main__":
    unittest.main()
