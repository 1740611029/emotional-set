"""生成本地指标样本并验证冻结历史；外部对照仅采用已登记实值，不写生产数据。"""
from __future__ import annotations

import hashlib
import json
import math
import statistics

import pandas as pd
from pandas.testing import assert_frame_equal, assert_series_equal

from fetch_market import INDEXES, INDEX_RAW
from io_utils import DATA
from multifactor_research import multifactor_indicators, multifactor_signals
from research_algorithms import read_index_csv
from run_algorithm_research import hash_raw_files, write_json

# 提取指标函数前记录的既有信号摘要；追加行情时仍只比较各自固定历史区间。
FROZEN_SIGNALS = {
    "sh000001": ("2026-10-28", 1453, "f0f85d8f67660699a3138ef94eaeae1e6dcd9d331d1687a0b2f0df30611fde49"),
    "sz399001": ("2026-10-28", 1453, "c3a797bc7fd55495b1e3dceacb0246b82b15549e6e03142e91aa201198dac891"),
    "sz399006": ("2026-10-28", 1453, "e2e7bb727b580e7c235b2444566ec84ec2195efe496ea71ef57d69581cc4856d"),
    "sh000688": ("2026-10-29", 1454, "6b1a86b6dd263454f24c2f18fb1345d5b8468664e0a267e36b7e52f0bca3bdd3"),
    "sh000300": ("2026-10-28", 1453, "ef6e4adbbe594f277de50864cf116d90f3a7a9b6c2864df1d005939e4710b39d"),
    "sh000905": ("2026-10-29", 1454, "fbd191338bde9c1ec55f1bf5745ca4f6d4562e9347d920a13161d87ccb4c480c"),
    "sh000852": ("2026-10-29", 1454, "7b102bfa91b48f46bf4597ccc7ab9c7cb8c3445078ceec08d7548f260264cbee"),
}
SAMPLE_DATES = ("2024-12-31", "2025-12-31", "2026-10-28")


def independent_indicator_values(frame: pd.DataFrame, position: int) -> dict:
    rows = frame.iloc[:position + 1]
    close = [float(value) for value in rows["close"]]
    high = [float(value) for value in rows["high"]]
    low = [float(value) for value in rows["low"]]
    volume = [float(value) for value in rows["volume"]]

    def ma(period):
        return statistics.fmean(close[-period:]) if len(close) >= period else None

    ma20 = ma(20)
    deviation20 = statistics.pstdev(close[-20:]) if len(close) >= 20 else None
    zscore20 = ((close[-1] - ma20) / deviation20
                if deviation20 is not None and deviation20 > 0 else None)
    percent_b = ((close[-1] - (ma20 - 2 * deviation20)) / (4 * deviation20)
                 if deviation20 is not None and deviation20 > 0 else None)

    gains = losses = None
    rsi14 = None
    for previous, current in zip(close, close[1:]):
        delta = current - previous
        gain, loss = max(delta, 0.0), max(-delta, 0.0)
        gains, losses = (gain, loss) if gains is None else ((13 * gains + gain) / 14, (13 * losses + loss) / 14)
        total = gains + losses
        rsi14 = 100 * gains / total if total else None

    returns = [current / previous - 1 for previous, current in zip(close, close[1:])]
    realized = [statistics.pstdev(returns[end - 20:end]) for end in range(20, len(close))]
    realized20 = realized[-1] if realized else None
    volatility_baseline = statistics.median(realized[-60:]) if len(realized) >= 60 else None

    obv_values = [0.0]
    for previous, current, current_volume in zip(close, close[1:], volume[1:]):
        direction = (current > previous) - (current < previous)
        obv_values.append(obv_values[-1] + direction * current_volume)

    typical = [(h + l + c) / 3 for h, l, c in zip(high, low, close)]

    def mfi_at(end):
        if end < 13:
            return None
        positive = negative = 0.0
        for index in range(end - 13, end + 1):
            raw = typical[index] * volume[index]
            if index and typical[index] > typical[index - 1]:
                positive += raw
            elif index and typical[index] < typical[index - 1]:
                negative += raw
        if negative == 0:
            return 100.0 if positive > 0 else None
        return 100 * positive / (positive + negative)

    mfi14 = mfi_at(position)
    previous_mfi = mfi_at(position - 1)
    return {
        "ma5": ma(5), "ma10": ma(10), "ma20": ma20, "ma30": ma(30), "ma60": ma(60),
        "std20": deviation20, "zscore20": zscore20, "percent_b": percent_b,
        "rsi14": rsi14, "realized20": realized20, "volatility_baseline": volatility_baseline,
        "drawdown20": close[-1] / max(close[-20:]) - 1 if len(close) >= 20 else None,
        "runup20": close[-1] / min(close[-20:]) - 1 if len(close) >= 20 else None,
        "obv": obv_values[-1],
        "obv_change3": obv_values[-1] - obv_values[-4] if len(close) >= 4 else None,
        "mfi14": mfi14,
        "mfi_change1": mfi14 - previous_mfi if mfi14 is not None and previous_mfi is not None else None,
    }


def assert_reference_match(local: dict, reference: dict) -> dict:
    differences = {}
    for key, expected in reference.items():
        actual = local[key]
        if (actual is None) != (expected is None):
            raise AssertionError(f"独立指标核对 {key} 的有效期不一致")
        if actual is None:
            differences[key] = None
            continue
        difference = abs(actual - expected)
        if not math.isclose(actual, expected, rel_tol=1e-11, abs_tol=1e-9):
            raise AssertionError(f"独立指标核对 {key} 不一致：{actual} != {expected}")
        differences[key] = difference
    return differences


def validate_external_record() -> dict:
    path = DATA / "research" / "index-indicator-external-comparison.json"
    report = json.loads(path.read_text(encoding="utf-8"))
    if report["decision"] != "keep_v1_total_std_user_confirmed_2026-10-30":
        raise AssertionError("BOLL 口径例外尚未按既定版本确认")
    frame = read_index_csv(INDEX_RAW / "sh000001.csv")
    checks = report["checks"]
    mfi_count = 0
    for check in (row for row in checks if row["indicator"] == "mfi14"):
        position = int(frame.index[frame["date"] == pd.Timestamp(check["date"])][0])
        local = multifactor_indicators(frame).iloc[position]["mfi14"]
        if not math.isclose(local, check["remote"], abs_tol=0.0005):
            raise AssertionError("同花顺 MFI14 实值不再匹配")
        mfi_count += 1
    boll = {row["indicator"]: row for row in checks if row["indicator"].startswith("boll_")}
    position = int(frame.index[frame["date"] == pd.Timestamp("2026-10-28")][0])
    ma20 = multifactor_indicators(frame).iloc[position]["ma20"]
    if not math.isclose(ma20, boll["boll_mid"]["remote"], abs_tol=0.0005):
        raise AssertionError("同花顺 MA20/BOLL 中轨实值不再匹配")
    obv = next(row for row in checks if row["indicator"] == "obv")
    close = next(row for row in checks if row["indicator"] == "close")["remote"]
    volume = next(row for row in checks if row["indicator"] == "volume")["remote"]
    days = list(obv["remote_level"])
    invalid_days = []
    for previous, current in zip(days, days[1:]):
        expected = ((close[current] > close[previous]) - (close[current] < close[previous])) * volume[current]
        actual = obv["remote_level"][current] - obv["remote_level"][previous]
        if math.isclose(actual, expected, rel_tol=1e-9, abs_tol=0.01):
            raise AssertionError("原 OBV 记录的不可比结论已过期，需重新审核")
        invalid_days.append(current)
    local_values = independent_indicator_values(frame, len(frame) - 1)
    local_obv = obv["local_level"]
    for previous, current in zip(days, days[1:]):
        previous_position = int(frame.index[frame["date"] == pd.Timestamp(previous)][0])
        current_position = int(frame.index[frame["date"] == pd.Timestamp(current)][0])
        reference = independent_indicator_values(frame, current_position)
        expected = reference["obv"] - independent_indicator_values(frame, previous_position)["obv"]
        if not math.isclose(local_obv[current] - local_obv[previous], expected, abs_tol=0.01):
            raise AssertionError("本地 OBV 差分不符合带符号成交量")
    return {
        "mfi14_actual_matches": mfi_count, "ma20_actual_match": True,
        "boll_std_exception": "keep_v1_ddof0_user_confirmed",
        "obv_external_reference_rejected_days": invalid_days,
        "obv_local_signed_volume_match": True,
        "latest_obv_change3": local_values["obv_change3"],
    }


def production_hashes() -> dict:
    return {
        name: hashlib.sha256((DATA / name).read_bytes()).hexdigest()
        for name in ("index-signals.json", "sector-trends.json")
    }


def verify_index(code: str, name: str, frame: pd.DataFrame) -> tuple[list, dict]:
    indicators = multifactor_indicators(frame)
    signals = multifactor_signals(frame, 4, participation_mode="and")
    frozen_end, frozen_rows, expected = FROZEN_SIGNALS[code]
    snapshot = frame[frame["date"] <= pd.Timestamp(frozen_end)]
    digest = hashlib.sha256("|".join(signals.iloc[:len(snapshot)]).encode()).hexdigest()
    if len(snapshot) != frozen_rows or digest != expected:
        raise AssertionError(f"{code} 固定历史信号摘要变化；请核查原始行情或公式，不能自动更新预期值")
    cutoffs = sorted({59, 60, 79, 80, len(frame) // 2, len(frame) - 1})
    for cutoff in cutoffs:
        prefix = frame.iloc[:cutoff]
        assert_frame_equal(indicators.iloc[:cutoff], multifactor_indicators(prefix))
        assert_series_equal(signals.iloc[:cutoff], multifactor_signals(prefix, 4, participation_mode="and"))
    samples = []
    for day in SAMPLE_DATES:
        matches = frame.index[frame["date"] == pd.Timestamp(day)]
        if len(matches) != 1:
            raise ValueError(f"{code} 抽样日 {day} 缺失或重复，不能替换成其他日期")
        position = int(matches[0])
        row = frame.iloc[position]
        local_values = {key: float(value) if pd.notna(value) else None
                        for key, value in indicators.iloc[position].items()}
        reference_values = independent_indicator_values(frame, position)
        differences = assert_reference_match(local_values, reference_values)
        samples.append({
            "code": code, "name": name, "date": day,
            "history_start": frame["date"].iat[0].strftime("%Y-%m-%d"),
            "history_rows": position + 1,
            "ohlcv": {key: float(row[key]) for key in ("open", "high", "low", "close", "volume")},
            "local_values": local_values, "independent_reference_values": reference_values,
            "independent_abs_diff": differences,
            "comparison_status": "match_independent_scalar_full_indicator_coverage",
        })
    return samples, {
        "code": code, "snapshot_start": frame["date"].iat[0].strftime("%Y-%m-%d"),
        "snapshot_end": frozen_end, "snapshot_rows": frozen_rows,
        "signal_sha256": digest, "matches_before_extraction": True,
        "causal_prefix_lengths": cutoffs,
    }


def main() -> None:
    raw_before = hash_raw_files()
    production_before = production_hashes()
    samples, snapshots = [], []
    for code, name in INDEXES.items():
        frame = read_index_csv(INDEX_RAW / f"{code}.csv")
        rows, snapshot = verify_index(code, name, frame)
        samples.extend(rows)
        snapshots.append(snapshot)
    external = validate_external_record()
    raw_after = hash_raw_files()
    production_after = production_hashes()
    if raw_before != raw_after or production_before != production_after:
        raise RuntimeError("核对期间原始行情或生产 JSON 发生变化，停止生成核对报告")
    write_json("index-indicator-local-samples.json", {
        "algorithm_version": "index-mf4-participation-v1.0",
        "comparison_status": "full_indicator_coverage_with_documented_external_exceptions",
        "production_ready": True,
        "note": "7个指数各3日样本的全部最终指标均与独立逐行计算一致；同花顺实值用于核对MFI14、MA20/BOLL及原始收盘价和成交量。BOLL保留用户确认的v1.0口径；原OBV远端记录因差分与同日成交量矛盾而剔除，本地OBV按带符号成交量独立核验，不声称与同花顺OBV显示值一致。",
        "external_actual_comparison": external,
        "metadata": {
            "raw_sha256_before": raw_before, "raw_sha256_after": raw_after,
            "raw_files_unchanged": True, "production_files_unchanged": True,
            "production_sha256": production_before,
        },
        "conventions": {
            "source": "既有同花顺公开日线，不替换原始行情，不额外复权",
            "ma": "收盘价简单均值，完整窗口后有效",
            "rsi14": "首个有效涨跌额初始化，alpha=1/14 递推；对照时记录加载历史起点与参数",
            "std20": "20日总体标准差 ddof=0；布林带为 MA20 ± 2*std20；用户于2026-10-30确认保留该口径，同花顺n-1差异作为例外留痕",
            "volatility": "20日简单收益率总体标准差；60值中位数包含当天",
            "extremes": "20日最高/最低收盘价，不是盘中最高/最低价",
            "obv": "从本地历史起点累计，首日0；核对成交量单位及3日变化，不能直接对比不同起点的累计值",
            "mfi14": "典型价=(H+L+C)/3，比较典型价方向；平价日两侧均不计，正负流均为0时无效",
        },
        "sample_dates": list(SAMPLE_DATES), "samples": samples,
        "frozen_signal_checks": snapshots,
    })
    print(f"本地指标样本 {len(samples)} 组全部通过独立数值核对；{len(snapshots)} 个指数历史信号摘要一致；外部例外已留痕")
    print("原始行情及生产 JSON 哈希均未改变")


if __name__ == "__main__":
    main()
