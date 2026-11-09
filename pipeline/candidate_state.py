"""候选信号与三层确认状态的只读导出（波段引擎）。

候选仅用于界面展示"待确认候选 / 确认层数"，不参与准确率统计，
也不写入历史信号列表；已确认的信号不会重复出现在候选列表中。
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from confirmed_index_signals import validate_history
from swing_signal_engine import (
    KINDS, LAYER_KEYS, _swing_gate, layer_state, prepare, swing_details,
)


def candidate_records(frame: pd.DataFrame, breadth: pd.DataFrame | None = None,
                      params: dict | None = None) -> list[dict]:
    """返回最近确认窗口内尚未确认的候选及其三层状态。"""
    validate_history(frame)
    state = prepare(frame, breadth, params)
    config = state["params"]
    details = swing_details(frame, breadth, params)
    confirmed_dates = {row["date"] for row in details}
    lookup = {day.strftime("%Y-%m-%d"): position for position, day in enumerate(frame["date"])}
    last = len(frame) - 1
    last_kind = details[-1]["type"] if details else None
    last_position = lookup[details[-1]["date"]] if details else None

    records: list[dict] = []
    for kind in KINDS:
        if last_kind == kind:
            continue
        for position in np.flatnonzero(state["masks"][kind].to_numpy()):
            position = int(position)
            if position < last - config["confirm_window"]:
                continue
            if frame["date"].iat[position].strftime("%Y-%m-%d") in confirmed_dates:
                continue
            if last_position is not None and position <= last_position:
                continue
            if not _swing_gate(frame, position, last_position, config):
                continue
            if last <= position:
                layers = {key: False for key in LAYER_KEYS}
            else:
                layers = layer_state(
                    frame, state["context"], state["breadth_delta5"],
                    last, kind, position, config,
                )
            records.append({
                "date": frame["date"].iat[position].strftime("%Y-%m-%d"),
                "type": kind,
                "price": float(frame["close"].iat[position]),
                "layers": layers,
                "passed": int(sum(bool(layers[key]) for key in LAYER_KEYS)),
                "trading_days_since": int(last - position),
                "confirmation_window": config["confirm_window"],
            })
    return records
