"""测试确认型信号算法"""
import pandas as pd
import numpy as np
from multifactor_research import confirmed_multifactor_signals, multifactor_signals
from research_algorithms import read_index_csv
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_confirmed_signals():
    # 读取上证指数数据（原始数据不入库，缺失时跳过）
    path = ROOT / "data" / "raw" / "indices-ths" / "sh000001.csv"
    if not path.exists():
        import pytest

        pytest.skip("缺少 data/raw/indices-ths/sh000001.csv，先运行 pipeline/bootstrap.py")
    frame = read_index_csv(path)
    print(f"数据行数: {len(frame)}")
    print(f"数据日期范围: {frame['date'].min()} ~ {frame['date'].max()}")

    # 生成候选信号
    candidates = multifactor_signals(frame, 4, trigger_mode='original', participation_mode='and')
    candidate_bottoms = (candidates == 'bottom').sum()
    candidate_tops = (candidates == 'top').sum()
    print(f"候选底部信号数: {candidate_bottoms}")
    print(f"候选顶部信号数: {candidate_tops}")

    # 生成确认信号
    confirmed = confirmed_multifactor_signals(frame, 4, trigger_mode='original', participation_mode='and')
    confirmed_bottoms = (confirmed == 'bottom').sum()
    confirmed_tops = (confirmed == 'top').sum()
    print(f"确认底部信号数: {confirmed_bottoms}")
    print(f"确认顶部信号数: {confirmed_tops}")

    # 验证信号数量减少
    assert confirmed_bottoms <= candidate_bottoms, "确认信号不应多于候选信号"
    assert confirmed_tops <= candidate_tops, "确认信号不应多于候选信号"

    print("测试通过！")


if __name__ == "__main__":
    test_confirmed_signals()
