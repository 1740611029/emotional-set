"""前端 JSON 契约及跨字段一致性校验。"""
from __future__ import annotations

import math
from datetime import date

from jsonschema import Draft202012Validator

DATE = {"type": "string", "pattern": r"^\d{4}-\d{2}-\d{2}$"}
NUMBER = {"type": "number"}
NULLABLE_NUMBER = {"type": ["number", "null"]}
DRAWDOWN_FIELDS = {
    "avg_max_adverse_excursion": NULLABLE_NUMBER,
    "worst_max_adverse_excursion": NULLABLE_NUMBER,
    "avg_forward_return_20": NULLABLE_NUMBER,
    "return_drawdown_ratio": NULLABLE_NUMBER,
}
ACCURACY = {
    "type": "object",
    "required": ["triggers", "successes", "rate", *DRAWDOWN_FIELDS],
    "properties": {
        "triggers": {"type": "integer", "minimum": 0},
        "successes": {"type": "integer", "minimum": 0},
        "rate": NUMBER,
        **DRAWDOWN_FIELDS,
    },
    "additionalProperties": False,
}
FACTOR_SCORE = {"type": ["number", "null"], "minimum": 0, "maximum": 1}
SIGNAL = {
    "type": "object",
    # 旧四因子算法只输出 date/type/price；波段引擎额外输出 grade 等明细字段。
    "required": ["date", "type", "price"],
    "properties": {
        "date": DATE, "type": {"enum": ["bottom", "top"]}, "price": NUMBER,
        "candidate_date": DATE, "grade": {"enum": ["A", "B"]},
        "score": FACTOR_SCORE, "core_hits": {"type": "integer", "minimum": 0, "maximum": 3},
        "factors": {"type": "object", "required": ["position", "divergence", "volume",
                                                  "volatility", "trend", "breadth"],
                    "properties": {key: FACTOR_SCORE for key in
                                   ("position", "divergence", "volume", "volatility", "trend", "breadth")},
                    "additionalProperties": False},
        "breadth": {"type": "object", "required": ["breadth20", "delta5"],
                    "properties": {"breadth20": NULLABLE_NUMBER, "delta5": NULLABLE_NUMBER},
                    "additionalProperties": False},
        "confirmation": {"type": "array", "items": {
            "type": "object",
            "required": ["date", "offset", "price", "momentum", "volume", "passed", "close"],
            "properties": {
                "date": DATE, "offset": {"type": "integer", "minimum": 1, "maximum": 3},
                "price": {"type": "boolean"}, "momentum": {"type": "boolean"},
                "volume": {"type": "boolean"}, "passed": {"type": "integer", "minimum": 0, "maximum": 3},
                "close": NUMBER, "candidate_reference": NUMBER,
                "rsi14": NULLABLE_NUMBER, "volume_ratio": NULLABLE_NUMBER,
                "breadth_delta5": NULLABLE_NUMBER,
            },
        }},
    },
    "additionalProperties": False,
}
COUNT = {"type": "integer", "minimum": 0}
NULLABLE_DATE = {**DATE, "type": ["string", "null"]}
RATE = {"type": ["number", "null"], "minimum": 0, "maximum": 1}
EVALUATION_SUMMARY = {
    "type": "object",
    "required": ["total", "resolved", "successes", "failures", "pending", "rate", "wilson95",
                 "small_sample", "note", *DRAWDOWN_FIELDS],
    "properties": {
        **{key: COUNT for key in ("total", "resolved", "successes", "failures", "pending")},
        "rate": RATE, "wilson95": {"type": "array", "minItems": 2, "maxItems": 2, "items": RATE},
        "small_sample": {"type": "boolean"}, "note": {"type": ["string", "null"]},
        **DRAWDOWN_FIELDS,
    },
}
EVALUATION_EVENT = {
    "type": "object",
    "required": ["date", "type", "price", "status", "confirmation_date", "confirmation_days",
                 "reason", "observed_trading_days", "max_adverse_excursion",
                 "max_adverse_excursion_20", "max_drawdown_20",
                 "forward_return_5", "forward_return_10", "forward_return_20"],
    "properties": {
        **SIGNAL["properties"], "status": {"enum": ["success", "failure", "pending"]},
        "confirmation_date": NULLABLE_DATE,
        "confirmation_days": {"type": ["integer", "null"], "minimum": 1},
        "reason": {"enum": ["ma_reversal", "opposite_signal", "segment_boundary", "data_end"]},
        "observed_trading_days": COUNT,
        "max_adverse_excursion": NULLABLE_NUMBER,
        "max_adverse_excursion_20": NULLABLE_NUMBER,
        "max_drawdown_20": NULLABLE_NUMBER,
        "forward_return_5": NULLABLE_NUMBER,
        "forward_return_10": NULLABLE_NUMBER,
        "forward_return_20": NULLABLE_NUMBER,
    },
}
BASELINE = {
    "type": "object",
    "required": ["horizon_days", "samples", "successes", "rate", "signal_rate_same_horizon", "seed"],
    "properties": {
        "horizon_days": {"type": "integer", "minimum": 1}, "samples": COUNT, "successes": COUNT,
        "rate": RATE, "signal_rate_same_horizon": RATE, "seed": {"type": "integer"},
    },
}
INDEX_EVALUATION = {
    "type": "object",
    "required": ["algorithm_version", "research_status", "rate_unit", "boundary_policy", "note",
                 "confirmation_date_note", "drawdown_note", "segments"],
    "properties": {
        # 必须与 confirmed_index_signals.ALGORITHM_VERSION 的两种取值之一保持一致
        "algorithm_version": {"enum": ["index-swing6-v2.0",
                                       "index-mf4-participation-v1.1-confirmed"]},
        "research_status": {"const": "post_hoc_exploratory_not_independent"},
        "rate_unit": {"const": "fraction"},
        "boundary_policy": {"const": "strict_segment_end_inclusive"},
        "note": {"type": "string", "minLength": 1}, "confirmation_date_note": {"type": "string", "minLength": 1},
        "drawdown_note": {"type": "string", "minLength": 1},
        "segments": {"type": "array", "minItems": 2, "maxItems": 2, "items": {
            "type": "object", "required": ["segment", "independent", "start", "end", "summary", "events", "baseline"],
            "properties": {
                "segment": {"enum": ["research", "exploratory_validation"]},
                "independent": {"const": False}, "start": NULLABLE_DATE, "end": NULLABLE_DATE,
                "summary": {"type": "object", "required": ["bottom", "top"],
                            "properties": {kind: EVALUATION_SUMMARY for kind in ("bottom", "top")}},
                "events": {"type": "array", "items": EVALUATION_EVENT},
                "baseline": {"type": "object", "required": ["bottom", "top"],
                             "properties": {kind: BASELINE for kind in ("bottom", "top")}},
            },
        }},
    },
}
# ---- 多持有期准确率矩阵（index-precision-pivot-signals）----
# 每格缺任一必填字段即构建失败；protected_write 会保留上一版且不产生半写文件。
PRECISION_CELL = {
    "type": "object",
    "required": ["segment", "horizon_days", "direction", "statistical_interval",
                 "triggers", "resolved", "n_resolved", "successes", "failures", "pending",
                 "direction_correct_rate", "direction_correct_wilson95",
                 "effective_amplitude_successes", "effective_amplitude_rate",
                 "effective_amplitude_wilson95", "effective_amplitude_line",
                 "small_sample", "baseline", "drawdown"],
    "properties": {
        "segment": {"enum": ["train", "validation"]},
        "horizon_days": {"enum": [5, 10, 20, 30]},
        "direction": {"enum": ["bottom", "top"]},
        "statistical_interval": {"type": "object", "required": ["start", "end"],
                                 "properties": {"start": NULLABLE_DATE, "end": NULLABLE_DATE}},
        **{key: COUNT for key in ("triggers", "resolved", "n_resolved", "successes",
                                  "failures", "pending", "effective_amplitude_successes")},
        "direction_correct_rate": RATE,
        "effective_amplitude_rate": RATE,
        "direction_correct_wilson95": {"type": "array", "minItems": 2, "maxItems": 2,
                                        "items": RATE},
        "effective_amplitude_wilson95": {"type": "array", "minItems": 2, "maxItems": 2,
                                          "items": RATE},
        "effective_amplitude_line": {"type": "number", "minimum": 0},
        "small_sample": {"type": "boolean"},
        "baseline": {"type": "object",
                     "required": ["baseline_samples", "baseline_successes", "baseline_rate",
                                  "signal_rate", "difference", "seed", "horizon_days"]},
        "drawdown": {"type": "object",
                     "required": ["avg_max_adverse_excursion", "worst_max_adverse_excursion",
                                  "avg_forward_return_20", "avg_forward_return_30",
                                  "return_drawdown_ratio", "mae_exceeded_failures",
                                  "mae_exceeded_failure_share"]},
        "events": {"type": "array"},
    },
}
PRECISION_SEGMENT = {
    "type": "object", "required": ["statistical_interval", "triggers", "horizons"],
    "properties": {
        "statistical_interval": {"type": "object"},
        "triggers": COUNT,
        "horizons": {"type": "object", "required": ["5", "10", "20", "30"],
                     "properties": {str(horizon): {
                         "type": "object", "required": ["bottom", "top"],
                         "properties": {kind: PRECISION_CELL for kind in ("bottom", "top")},
                     } for horizon in (5, 10, 20, 30)}},
    },
}
PRECISION_MATRIX = {
    "type": "object", "required": ["train", "validation"],
    "properties": {segment: PRECISION_SEGMENT for segment in ("train", "validation")},
}
PRECISION_BLOCK = {
    "type": "object",
    "required": ["algorithm_version", "signal_algorithm_version", "research_status",
                 "covered_universe", "universe_size", "segments", "horizons",
                 "criteria_note", "legacy_rate_note", "effective_amplitude_lines",
                 "decision", "pivot_anchors", "comparison"],
    "properties": {
        "algorithm_version": {"type": "string", "minLength": 1},
        "signal_algorithm_version": {"type": "string", "minLength": 1},
        "research_status": {"type": "string", "minLength": 1},
        "covered_universe": {"type": "array", "minItems": 7, "maxItems": 7,
                             "items": {"type": "string"}},
        "universe_size": {"const": 7},
        "segments": {"type": "object"},
        "horizons": {"type": "array", "minItems": 4, "maxItems": 4},
        "criteria_note": {"type": "string", "minLength": 1},
        "legacy_rate_note": {"type": "string", "minLength": 1},
        "effective_amplitude_lines": {"type": "object"},
        "decision": {"enum": ["更换建议", "观察", "保留现行"]},
        "pivot_anchors": {"type": "object"},
        "comparison": {"type": "object"},
    },
}

INDEX_SCHEMA = {
    # precision 为可选顶层块：旧版产物（回滚目标）不带该字段时仍须通过校验；
    # 一旦存在，7 个指数的矩阵与全部矩阵格字段 MUST 完整（见 validate_precision）。
    "type": "object", "required": ["as_of", "is_mock", "indices", "recent_signals"],
    "properties": {
        "precision": PRECISION_BLOCK,
        "as_of": DATE, "is_mock": {"type": "boolean"},
        "indices": {"type": "array", "minItems": 7, "maxItems": 7, "items": {
            "type": "object", "required": ["code", "name", "latest", "change_pct", "current_signal", "history", "signals", "accuracy"],
            "properties": {
                "precision_matrix": PRECISION_MATRIX,
                "code": {"type": "string"}, "name": {"type": "string"}, "latest": NUMBER, "change_pct": NUMBER,
                "current_signal": {"type": "object", "required": ["type", "date"],
                                   "properties": {"type": {"enum": ["bottom", "top", "none"]}, "date": {**DATE, "type": ["string", "null"]}}},
                "history": {"type": "array", "minItems": 1250, "items": {"type": "object", "required": ["date", "close"],
                                                                         "properties": {"date": DATE, "close": NUMBER}}},
                "signals": {"type": "array", "items": SIGNAL},
                "evaluation": INDEX_EVALUATION,
                "accuracy": {"type": "object", "required": ["start", "end", "bottom", "top"],
                             "properties": {"start": DATE, "end": DATE, "bottom": ACCURACY, "top": ACCURACY}},
            },
        }},
        "recent_signals": {"type": "array", "items": {"type": "object",
            "required": ["date", "code", "name", "type", "price", "change_since"],
            "properties": {"date": DATE, "code": {"type": "string"}, "name": {"type": "string"},
                           "type": {"enum": ["bottom", "top"]}, "price": NUMBER, "change_since": NUMBER}}},
    },
}
SECTOR_STRENGTH_PARTS = {
    "type": ["object", "null"],
    "required": ["range_position", "ma_score"],
    "properties": {
        "range_position": {"type": "number", "minimum": 0, "maximum": 100},
        "ma_score": {"type": "number", "minimum": 0, "maximum": 100},
    },
    "additionalProperties": False,
}
SECTOR_SERIES = {
    "type": "object", "required": ["close", "state"],
    "properties": {
        "close": {"type": "array", "items": {"type": ["number", "null"]}},
        "state": {"type": "array", "items": {"enum": ["B", "W", "D", "R", None]}},
    },
    "additionalProperties": False,
}
SECTOR_BASE_PROPS = {
    "code": {"type": "string"}, "name": {"type": "string"},
    "date": {"type": ["string", "null"], "pattern": r"^\d{4}-\d{2}-\d{2}$"},
    "state": {"enum": ["bull", "warn", "bear", "rise", None]},
    "strength": {"type": ["number", "null"], "minimum": -50, "maximum": 50},
    "strength_parts": SECTOR_STRENGTH_PARTS,
    "strength_basis": {"enum": ["directional", "neutral", None]},
    "days": {"type": ["integer", "null"], "minimum": 0},
    "data_anomaly": {"type": "boolean"},
    "data_lag": {"type": "boolean"},
    "spark": {"type": "array", "minItems": 1, "items": {"type": "number"}},
    "series": SECTOR_SERIES,
}
# 单层行业板块：不得再携带层级、父级或子板块列表
SECTOR_REQUIRED = ["code", "name", "date", "state", "strength",
                   "strength_parts", "strength_basis", "days", "data_anomaly", "data_lag",
                   "spark", "series"]
SECTOR_ITEM = {
    "type": "object", "required": SECTOR_REQUIRED,
    "properties": dict(SECTOR_BASE_PROPS),
    "additionalProperties": False,
}
SECTOR_HORIZON_EVALUATION = {
    "type": ["object", "null"],
    "required": ["entries", "samples", "successes", "pending", "rate", "wilson95", "small_sample", "note"],
    "properties": {
        "entries": COUNT, "samples": COUNT, "successes": COUNT, "pending": COUNT,
        "rate": RATE, "wilson95": {"type": "array", "minItems": 2, "maxItems": 2, "items": RATE},
        "small_sample": {"type": "boolean"}, "note": {"type": ["string", "null"]},
    },
    "additionalProperties": False,
}
# 四态评估：同一批事件换个分组键，键固定齐备，无事件的窗口为 null
SECTOR_STATE_HORIZONS = {
    "type": "object",
    "required": ["10", "20"],
    "properties": {"10": SECTOR_HORIZON_EVALUATION, "20": SECTOR_HORIZON_EVALUATION},
    "additionalProperties": False,
}
SECTOR_EVALUATION_STATES = {
    "type": "object",
    "required": ["bull", "warn", "bear", "rise"],
    "properties": {state: SECTOR_STATE_HORIZONS for state in ("bull", "warn", "bear", "rise")},
    "additionalProperties": False,
}
SECTOR_EVALUATION = {
    "type": "object",
    "required": ["algorithm_version", "research_status", "rate_unit", "boundary_policy", "note", "segments"],
    "properties": {
        "algorithm_version": {"const": "sector-donchian20-ma60-v2.0-em-source"},
        "research_status": {"const": "post_hoc_exploratory_not_independent"},
        "rate_unit": {"const": "fraction"},
        "boundary_policy": {"const": "strict_segment_end_inclusive"},
        "note": {"type": "string", "minLength": 1},
        "segments": {"type": "array", "minItems": 2, "maxItems": 2, "items": {
            "type": "object", "required": ["segment", "independent", "states"],
            "properties": {
                "segment": {"enum": ["research", "exploratory_validation"]},
                "independent": {"const": False},
                "states": SECTOR_EVALUATION_STATES,
            },
            "additionalProperties": False,
        }},
    },
}
SECTOR_SCHEMA = {
    "type": "object",
    "required": ["as_of", "is_mock", "dates", "groups", "algorithm_version", "data_source"],
    "properties": {"as_of": DATE, "is_mock": {"type": "boolean"},
                   "algorithm_version": {"type": "string", "minLength": 1},
                   "data_source": {"const": "东财行业板块"},
                   "dates": {"type": "array", "minItems": 1, "items": DATE},
                   "groups": {"type": "object", "required": ["bull", "warn", "bear", "rise"],
                              "properties": {key: {"type": "array", "items": SECTOR_ITEM} for key in ["bull", "warn", "bear", "rise"]},
                              "additionalProperties": {"type": "array", "items": SECTOR_ITEM}},
                   "evaluation": SECTOR_EVALUATION},
    # 顶层同样收紧：单层清单已无二级板块，旧产物的顶层键（如 subsector_evaluation）
    # 必须在校验阶段被拒，不能只靠板块条目层的 additionalProperties
    "additionalProperties": False,
}
INSTITUTION_SERIES_VIEW = {
    "type": "object",
    "required": ["long", "short", "net", "chg", "next_ret", "relation"],
    "properties": {
        "long": {"type": "array", "items": {"type": ["integer", "null"]}},
        "short": {"type": "array", "items": {"type": ["integer", "null"]}},
        "net": {"type": "array", "items": {"type": ["integer", "null"]}},
        "chg": {"type": "array", "items": {"type": ["integer", "null"]}},
        "next_ret": {"type": "array", "items": {"type": ["number", "null"]}},
        "relation": {"type": "array", "items": {"enum": ["S", "H", "P", "N"]}},
    },
    "additionalProperties": False,
}
INSTITUTION_SERIES_GROUP = {
    "type": "object",
    "required": ["all", "IF", "IC", "IH", "IM"],
    "properties": {view: INSTITUTION_SERIES_VIEW for view in ("all", "IF", "IC", "IH", "IM")},
    "additionalProperties": False,
}
INSTITUTION_STAT = {
    "type": "object",
    "required": ["rate", "rate_unit", "decided", "same", "hedge", "pending", "no_data",
                 "small_sample", "start", "end"],
    "properties": {
        "rate": RATE, "rate_unit": {"const": "fraction"},
        **{key: COUNT for key in ("decided", "same", "hedge", "pending", "no_data")},
        "small_sample": {"type": "boolean"},
        "start": NULLABLE_DATE, "end": NULLABLE_DATE,
    },
    "additionalProperties": False,
}
INSTITUTION_STATS_GROUP = {
    "type": "object",
    "required": ["all", "IF", "IC", "IH", "IM"],
    "properties": {view: INSTITUTION_STAT for view in ("all", "IF", "IC", "IH", "IM")},
    "additionalProperties": False,
}
INSTITUTION_MARKET_LINE = {
    "type": "object",
    "required": ["code", "name", "close"],
    "properties": {"code": {"type": "string"}, "name": {"type": "string"},
                   "close": {"type": "array", "items": {"type": ["number", "null"]}}},
    "additionalProperties": False,
}
INSTITUTION_SCHEMA = {
    "type": "object",
    "required": ["as_of", "generated_at", "algorithm_version", "window", "varieties",
                 "institutions", "aggregation_note", "scope_note", "benchmark", "dates",
                 "series", "stats", "quality"],
    "properties": {
        "as_of": NULLABLE_DATE,
        "generated_at": {"type": "string", "minLength": 1},
        "algorithm_version": {"type": "string", "minLength": 1},
        "window": {"type": "object", "required": ["start", "end", "years"],
                   "properties": {"start": NULLABLE_DATE, "end": NULLABLE_DATE,
                                  "years": {"type": "integer", "minimum": 1}},
                   "additionalProperties": False},
        "varieties": {"type": "array", "minItems": 4, "maxItems": 4, "items": {
            "type": "object", "required": ["code", "name"],
            "properties": {"code": {"enum": ["IF", "IC", "IH", "IM"]}, "name": {"type": "string"}},
            "additionalProperties": False}},
        "institutions": {"type": "array", "minItems": 3, "maxItems": 3, "items": {
            "type": "object", "required": ["id", "name", "note"],
            "properties": {"id": {"enum": ["guotai", "citic", "others"]},
                           "name": {"type": "string"}, "note": {"type": "string", "minLength": 1}},
            "additionalProperties": False}},
        "aggregation_note": {"type": "string", "minLength": 1},
        "scope_note": {"type": "string", "minLength": 1},
        "benchmark": {"type": "object", "required": ["code", "name", "close"],
                      "properties": {"code": {"type": "string"}, "name": {"type": "string"},
                                     "close": {"type": "array", "items": {"type": ["number", "null"]}}},
                      "additionalProperties": False},
        # 仅展示用的大盘对照序列（上证指数 + 沪深300），不参与统计；旧产物可缺该字段
        "market_lines": {"type": "array", "minItems": 1, "items": INSTITUTION_MARKET_LINE},
        "dates": {"type": "array", "minItems": 1, "items": DATE},
        "series": {"type": "object", "required": ["guotai", "citic", "others"],
                   "properties": {gid: INSTITUTION_SERIES_GROUP for gid in ("guotai", "citic", "others")},
                   "additionalProperties": False},
        "stats": {"type": "object", "required": ["guotai", "citic", "others"],
                  "properties": {gid: INSTITUTION_STATS_GROUP for gid in ("guotai", "citic", "others")},
                  "additionalProperties": False},
        "quality": {"type": "object",
                    "required": ["missing_dates", "unmatched_members", "conservation_checked",
                                 "source", "cached_days", "trading_days"],
                    "properties": {
                        "missing_dates": {"type": "array", "items": DATE},
                        "unmatched_members": {"type": "array", "items": {"type": "string"}},
                        "conservation_checked": COUNT,
                        "source": {"type": "string", "minLength": 1},
                        "cached_days": COUNT, "trading_days": COUNT,
                    },
                    "additionalProperties": False},
    },
    "additionalProperties": False,
}
ETF_VOLUME_STATES = ["下跌底部区", "趋势上行区", "中性过渡区"]
ETF_VOLUME_DAY = {
    "type": "object",
    "required": ["date", "level", "etf_codes", "total_amount", "state"],
    "properties": {
        "date": DATE,
        "level": {"enum": ["L1", "L2", "L3"]},
        "etf_codes": {"type": "array", "minItems": 1, "items": {"type": "string"}},
        "total_amount": {"type": "number", "exclusiveMinimum": 0},
        "state": {"enum": ETF_VOLUME_STATES},
    },
    "additionalProperties": False,
}
ETF_VOLUME_EVENT = {
    "type": "object",
    "required": ["start_date", "end_date", "days", "level", "state", "peak_amount"],
    "properties": {
        "start_date": DATE, "end_date": DATE,
        "days": {"type": "integer", "minimum": 1},
        "level": {"enum": ["L1", "L2", "L3"]},
        "state": {"enum": ETF_VOLUME_STATES},
        "peak_amount": {"type": "number", "exclusiveMinimum": 0},
    },
    "additionalProperties": False,
}
ETF_VOLUME_EVENTS_SCHEMA = {
    "type": "object",
    "required": ["as_of", "generated_at", "algorithm_version", "threshold_amount",
                 "cluster_gap_days", "mode_basis_index", "pool", "days", "events"],
    "properties": {
        "as_of": DATE,
        "generated_at": {"type": "string", "minLength": 1},
        "algorithm_version": {"type": "string", "minLength": 1},
        "threshold_amount": {"type": "number", "exclusiveMinimum": 0},
        "cluster_gap_days": {"type": "integer", "minimum": 0},
        "mode_basis_index": {"type": "string", "minLength": 1},
        "pool": {"type": "array", "minItems": 6, "maxItems": 6, "items": {
            "type": "object", "required": ["code", "name", "index"],
            "properties": {"code": {"type": "string"}, "name": {"type": "string"},
                           "index": {"type": "string"}},
            "additionalProperties": False}},
        "days": {"type": "array", "items": ETF_VOLUME_DAY},
        "events": {"type": "array", "items": ETF_VOLUME_EVENT},
    },
    "additionalProperties": False,
}
SCHEMAS = {"index-signals.json": INDEX_SCHEMA, "sector-trends.json": SECTOR_SCHEMA,
           "institution-positions.json": INSTITUTION_SCHEMA,
           "etf-volume-events.json": ETF_VOLUME_EVENTS_SCHEMA}


def validate_index_evaluation(item: dict) -> None:
    """仅校验展示数据，不重新计算指标或信号；旧JSON没有evaluation时不受影响。"""
    evaluation = item["evaluation"]
    parts = evaluation["segments"]
    if [part["segment"] for part in parts] != ["research", "exploratory_validation"]:
        raise ValueError("evaluation必须按研究/探索性验证分段且不重复")
    history = item["history"]
    days = [row["date"] for row in history]
    if days != sorted(set(days)):
        raise ValueError("evaluation对应的行情日期必须唯一且升序")
    for day in days:
        date.fromisoformat(day)
    positions = {day: position for position, day in enumerate(days)}
    facts = {(row["date"], row["type"]): row["price"] for row in item["signals"]}
    if len(facts) != len(item["signals"]):
        raise ValueError("信号事实重复")
    seen = set()
    for part in parts:
        available = [day for day in days if ("2020-10-30" <= day <= "2024-12-31"
                    if part["segment"] == "research" else day >= "2025-01-01")]
        expected_bounds = (available[0], available[-1]) if available else (None, None)
        if (part["start"], part["end"]) != expected_bounds:
            raise ValueError("评价区间必须对应实际行情及固定研究边界")
        events = part["events"]
        for event in events:
            key = (event["date"], event["type"])
            if key in seen or key not in facts or event["price"] != facts[key]:
                raise ValueError("评价事件重复或与信号事实不一致")
            seen.add(key)
            if event["date"] not in available or not math.isfinite(event["price"]):
                raise ValueError("信号不在评价区间内或价格无效")
            position = positions[event["date"]]
            if event["price"] != history[position]["close"]:
                raise ValueError("信号价格必须为信号日收盘价")
            opposite = sorted(day for day, kind in facts if kind != event["type"] and day > event["date"])
            next_opposite = opposite[0] if opposite and opposite[0] <= part["end"] else None
            resolved = event["confirmation_date"]
            if event["status"] == "pending":
                reason = "segment_boundary" if part["segment"] == "research" and days[-1] > "2024-12-31" else "data_end"
                if resolved is not None or event["confirmation_days"] is not None or next_opposite or event["reason"] != reason:
                    raise ValueError("未决事件的确认信息或截止原因不一致")
                observed = positions[part["end"]] - position
            else:
                if resolved not in positions or not event["date"] < resolved <= part["end"]:
                    raise ValueError("确认日期必须在信号次日之后且不越过评价边界")
                observed = positions[resolved] - position
                if event["confirmation_days"] != observed:
                    raise ValueError("确认耗时与交易日数量不一致")
                if event["status"] == "failure":
                    if resolved != next_opposite or event["reason"] != "opposite_signal":
                        raise ValueError("失败事件必须截止于首个反向信号")
                elif event["reason"] != "ma_reversal" or (next_opposite and resolved >= next_opposite):
                    raise ValueError("成功事件必须在反向信号之前确认")
            if event["observed_trading_days"] != observed:
                raise ValueError("事件观察天数不一致")
        for kind in ("bottom", "top"):
            rows = [event for event in events if event["type"] == kind]
            counts = {status: sum(row["status"] == status for row in rows) for status in ("success", "failure", "pending")}
            resolved = counts["success"] + counts["failure"]
            summary = part["summary"][kind]
            expected = (len(rows), resolved, counts["success"], counts["failure"], counts["pending"])
            if tuple(summary[key] for key in ("total", "resolved", "successes", "failures", "pending")) != expected:
                raise ValueError("评价汇总与逐笔事件数量不一致")
            rate = summary["rate"]
            if (not resolved and rate is not None) or (resolved and (rate is None or not math.isclose(rate, counts["success"] / resolved, abs_tol=1e-12))):
                raise ValueError("胜率必须使用成功加失败作为分母，无已判定样本时为null")
            bounds = summary["wilson95"]
            if (not resolved and bounds != [None, None]) or (resolved and (any(value is None or not math.isfinite(value) for value in bounds) or not bounds[0] - 1e-12 <= rate <= bounds[1] + 1e-12)):
                raise ValueError("Wilson区间无效")
            if summary["small_sample"] != (resolved < 30) or summary["note"] != ("样本量较小，仅供参考" if resolved < 30 else None):
                raise ValueError("小样本提示必须依据已判定数")
    if seen != {key for key in facts if key[0] >= "2020-10-30"}:
        raise ValueError("评价遗漏信号，末端未决也必须保留")
    available_parts = [part for part in parts if part["start"] is not None]
    accuracy = item["accuracy"]
    if available_parts and (accuracy["start"], accuracy["end"]) != (available_parts[0]["start"], available_parts[-1]["end"]):
        raise ValueError("旧accuracy统计区间不一致")
    for kind in ("bottom", "top"):
        summaries = [part["summary"][kind] for part in parts]
        total, successes, resolved = (sum(row[key] for row in summaries) for key in ("total", "successes", "resolved"))
        old = accuracy[kind]
        rate = 100 * successes / resolved if resolved else 0
        if old["triggers"] != total or old["successes"] != successes or not math.isclose(old["rate"], rate, abs_tol=1e-10):
            raise ValueError("旧accuracy字段必须与evaluation一致且rate使用百分数")


def validate_index_display(payload: dict) -> None:
    expected_recent = []
    for item in payload["indices"]:
        days = [row["date"] for row in item["history"]]
        positions = {day: position for position, day in enumerate(days)}
        if len(positions) != len(days):
            raise ValueError("指数行情日期必须唯一")
        active_dates = set(days[-10:])
        active = [row for row in item["signals"] if row["date"] in active_dates]
        expected_current = ({"type": "none", "date": None} if not active else
                            {"type": active[-1]["type"], "date": active[-1]["date"]})
        if item["current_signal"] != expected_current:
            raise ValueError("当前信号必须是最近10个交易日内的最后一次触发")
        for signal in item["signals"]:
            if signal["date"] not in positions:
                raise ValueError("信号日期必须存在于指数行情")
            expected_recent.append({
                "date": signal["date"], "code": item["code"], "name": item["name"],
                "type": signal["type"], "price": signal["price"],
                "change_since": (item["latest"] / signal["price"] - 1) * 100,
            })
    expected_recent.sort(key=lambda row: (row["date"], row["code"]), reverse=True)
    actual_recent = payload["recent_signals"]
    if len(actual_recent) != len(expected_recent):
        raise ValueError("最近信号列表必须完整包含各指数信号")
    for actual, expected in zip(actual_recent, expected_recent):
        if any(actual[key] != expected[key] for key in ("date", "code", "name", "type", "price")):
            raise ValueError("最近信号列表内容或排序与指数信号不一致")
        if not math.isclose(actual["change_since"], expected["change_since"], abs_tol=0.0050001):
            raise ValueError("至今涨跌幅必须由触发价和最新价计算")


SECTOR_STATE_CODES = {"bull": "B", "warn": "W", "bear": "D", "rise": "R"}


def validate_sector_display(payload: dict) -> None:
    """板块展示数据自洽性：共享日期、序列末项、强度构成与状态一致。"""
    dates = payload["dates"]
    if dates != sorted(set(dates)):
        raise ValueError("板块交易日必须唯一且升序")
    if dates and dates[-1] > payload["as_of"]:
        raise ValueError("板块序列不得包含晚于 as_of 的交易日")
    seen: set[str] = set()

    def check(item: dict) -> None:
        code = item["code"]
        if code in seen:
            raise ValueError(f"板块代码重复：{code}")
        seen.add(code)
        close = item["series"]["close"]
        states = item["series"]["state"]
        if len(close) != len(dates) or len(states) != len(dates):
            raise ValueError(f"板块 {code} 序列长度与共享日期不一致")
        if item["state"] is None:
            return
        positions = [index for index, value in enumerate(close) if value is not None]
        if not positions:
            raise ValueError(f"板块 {code} 有状态但无收盘序列")
        last = positions[-1]
        if dates[last] != item["date"]:
            raise ValueError(f"板块 {code} 序列末个交易日与 date 不一致")
        if states[last] != SECTOR_STATE_CODES[item["state"]]:
            raise ValueError(f"板块 {code} 序列末状态与 state 不一致")
        if not math.isclose(item["spark"][-1], close[last], abs_tol=1e-6):
            raise ValueError(f"板块 {code} 缩略序列末值与收盘不一致")
        parts = item["strength_parts"]
        if parts is None:
            raise ValueError(f"板块 {code} 有状态但缺少强度构成")
        average = (parts["range_position"] + parts["ma_score"]) / 2
        basis = item["strength_basis"]
        if basis == "neutral":
            expected = 0.0
        elif basis == "directional":
            expected = average - 50
        else:
            raise ValueError(f"板块 {code} 使用了已废弃的强度口径 {basis}")
        if not math.isclose(item["strength"], expected, abs_tol=1e-3):
            raise ValueError(f"板块 {code} 强度 {item['strength']} 与构成 {expected} 不一致")

    for rows in payload["groups"].values():
        for item in rows:
            check(item)


def validate_institution_display(payload: dict) -> None:
    """机构持仓展示数据自洽性：序列对齐、净持仓与变化可复算、关系判定与统计一致。"""
    dates = payload["dates"]
    if dates != sorted(set(dates)):
        raise ValueError("机构持仓交易日必须唯一且升序")
    window = payload["window"]
    if (window["start"], window["end"]) != (dates[0], dates[-1]):
        raise ValueError("统计窗口必须与日期序列首尾一致")
    if payload["as_of"] is not None and payload["as_of"] not in dates:
        raise ValueError("as_of 必须存在于日期序列")
    close = payload["benchmark"]["close"]
    if len(close) != len(dates):
        raise ValueError("基准收盘序列长度与日期不一致")
    for line in payload.get("market_lines") or []:
        if len(line["close"]) != len(dates):
            raise ValueError(f"大盘对照序列 {line['code']} 长度与日期不一致")

    expected_next = []
    for index in range(len(dates)):
        if index + 1 < len(dates) and close[index] and close[index + 1]:
            expected_next.append(close[index + 1] / close[index] - 1)
        else:
            expected_next.append(None)

    for gid, block in payload["series"].items():
        for view, view_block in block.items():
            arrays = {key: view_block[key] for key in ("long", "short", "net", "chg", "next_ret", "relation")}
            for key, values in arrays.items():
                if len(values) != len(dates):
                    raise ValueError(f"{gid}.{view}.{key} 长度与日期不一致")
            for index, day in enumerate(dates):
                long_oi, short_oi, net = view_block["long"][index], view_block["short"][index], view_block["net"][index]
                chg, next_ret = view_block["chg"][index], view_block["next_ret"][index]
                if (long_oi is None) != (short_oi is None) or (long_oi is None) != (net is None):
                    raise ValueError(f"{gid}.{view} {day} 多空单与净持仓缺失状态不一致")
                if net is not None and net != long_oi - short_oi:
                    raise ValueError(f"{gid}.{view} {day} 净持仓不等于多单减空单")
                expected_chg = None
                if index > 0 and net is not None and view_block["net"][index - 1] is not None:
                    expected_chg = net - view_block["net"][index - 1]
                if chg != expected_chg:
                    raise ValueError(f"{gid}.{view} {day} 净持仓变化必须相对上一交易日")
                if next_ret != expected_next[index] and not (
                    next_ret is not None and expected_next[index] is not None
                    and math.isclose(next_ret, expected_next[index], abs_tol=1e-12)
                ):
                    raise ValueError(f"{gid}.{view} {day} 次日收益率必须由基准收盘复算")
                if net is None:
                    expected_relation = "N"
                elif chg in (None, 0) or next_ret in (None, 0):
                    expected_relation = "P"
                else:
                    expected_relation = "S" if (chg > 0) == (next_ret > 0) else "H"
                if view_block["relation"][index] != expected_relation:
                    raise ValueError(f"{gid}.{view} {day} 同向/对冲判定与规则不一致")

    for gid, block in payload["stats"].items():
        for view, stat in block.items():
            relation = payload["series"][gid][view]["relation"]
            counts = {code: relation.count(code) for code in ("S", "H", "P", "N")}
            if (stat["same"], stat["hedge"], stat["pending"], stat["no_data"]) != (
                    counts["S"], counts["H"], counts["P"], counts["N"]):
                raise ValueError(f"{gid}.{view} 统计计数与关系序列不一致")
            decided = counts["S"] + counts["H"]
            if stat["decided"] != decided:
                raise ValueError(f"{gid}.{view} 已判定样本数不等于同向加对冲")
            if (decided and (stat["rate"] is None or not math.isclose(stat["rate"], counts["S"] / decided, abs_tol=1e-12))) \
                    or (not decided and stat["rate"] is not None):
                raise ValueError(f"{gid}.{view} 同向率必须以同向加对冲为分母")
            if stat["small_sample"] != (decided < 30):
                raise ValueError(f"{gid}.{view} 小样本标记必须依据已判定样本数")
            decided_days = [day for day, code in zip(dates, relation) if code in ("S", "H")]
            expected_bounds = (decided_days[0], decided_days[-1]) if decided_days else (None, None)
            if (stat["start"], stat["end"]) != expected_bounds:
                raise ValueError(f"{gid}.{view} 统计区间必须覆盖首末已判定交易日")


def validate_etf_volume_display(payload: dict) -> None:
    """ETF 放量事件展示数据自洽：日期唯一升序、级别合法、代码属于池、事件起点在 days 内。"""
    days = payload["days"]
    dates = [row["date"] for row in days]
    if dates != sorted(set(dates)):
        raise ValueError("ETF 放量日必须唯一且升序")
    codes = {item["code"] for item in payload["pool"]}
    for row in days:
        if row["level"] not in ("L1", "L2", "L3"):
            raise ValueError(f"放量日 {row['date']} 级别非法：{row['level']}")
        extra = sorted(set(row["etf_codes"]) - codes)
        if extra:
            raise ValueError(f"放量日 {row['date']} 出现池外代码：{extra}")
        if row["total_amount"] <= 0:
            raise ValueError(f"放量日 {row['date']} 合计成交额必须为正")
    known = set(dates)
    for event in payload["events"]:
        if event["start_date"] not in known:
            raise ValueError(f"事件起点 {event['start_date']} 不在放量日清单中")
        if event["end_date"] not in known or event["end_date"] < event["start_date"]:
            raise ValueError(f"事件止点 {event['end_date']} 不在放量日清单中或早于起点")
        if event["peak_amount"] <= 0:
            raise ValueError("事件峰值成交额必须为正")


def validate_precision(payload: dict) -> None:
    """多持有期矩阵跨字段一致性：触发次数守恒、双口径分母、小样本标记、覆盖范围。

    兼容回滚：整块 precision 缺失（旧版产物）时直接放行；但不允许只带部分矩阵。
    """
    precision = payload.get("precision")
    codes = [item["code"] for item in payload["indices"]]
    present = [item for item in payload["indices"] if item.get("precision_matrix") is not None]
    if precision is None:
        if present:
            raise ValueError("旧版产物不带 precision 时，所有指数 MUST 同时不带矩阵")
        return
    if len(present) != len(payload["indices"]):
        raise ValueError("带 precision 的产物 MUST 为全部 7 个指数提供矩阵")
    if precision["covered_universe"] != codes:
        raise ValueError("统计覆盖标的清单必须与 7 张指数图卡逐项一致")
    if precision["universe_size"] != 7:
        raise ValueError("统计覆盖标的数必须为 7")
    if "历史基线" not in precision["legacy_rate_note"]:
        raise ValueError("旧聚合 rate 必须标注为历史基线")
    for item in payload["indices"]:
        matrix = item["precision_matrix"]
        for segment in ("train", "validation"):
            block = matrix[segment]
            for horizon in ("5", "10", "20", "30"):
                for kind in ("bottom", "top"):
                    cell = block["horizons"][horizon][kind]
                    label = f"{item['code']}/{segment}/{horizon}/{kind}"
                    if (cell["segment"], cell["horizon_days"], cell["direction"]) != (
                            segment, int(horizon), kind):
                        raise ValueError(f"{label} 矩阵格坐标不匹配")
                    if cell["n_resolved"] != cell["resolved"]:
                        raise ValueError(f"{label} n_resolved 与 resolved 不一致")
                    if cell["resolved"] + cell["pending"] != cell["triggers"]:
                        raise ValueError(f"{label} 触发次数不守恒")
                    if cell["successes"] + cell["failures"] != cell["resolved"]:
                        raise ValueError(f"{label} 成败数不等于已判定数")
                    rate = cell["direction_correct_rate"]
                    expected = (cell["successes"] / cell["resolved"]
                                if cell["resolved"] else None)
                    if (rate is None) != (expected is None) or (
                            rate is not None and not math.isclose(rate, expected, abs_tol=1e-12)):
                        raise ValueError(f"{label} 方向正确率与成败数不一致")
                    amplitude = cell["effective_amplitude_rate"]
                    expected_amp = (cell["effective_amplitude_successes"] / cell["resolved"]
                                    if cell["resolved"] else None)
                    if (amplitude is None) != (expected_amp is None) or (
                            amplitude is not None
                            and not math.isclose(amplitude, expected_amp, abs_tol=1e-12)):
                        raise ValueError(f"{label} 有效幅度率与成功数不一致")
                    if cell["small_sample"] != (cell["resolved"] < 30):
                        raise ValueError(f"{label} small_sample 标记不实")


def validate_payload(name: str, payload: dict) -> None:
    Draft202012Validator(SCHEMAS[name]).validate(payload)
    if name == "index-signals.json":
        codes = {item["code"] for item in payload["indices"]}
        if len(codes) != 7:
            raise ValueError("7 个指数代码必须唯一")
        validate_index_display(payload)
        validate_precision(payload)
        for item in payload["indices"]:
            if "evaluation" in item:
                validate_index_evaluation(item)
    elif name == "sector-trends.json":
        total = sum(len(rows) for rows in payload["groups"].values())
        if total != 31:
            raise ValueError(f"行业板块（含 unclassified 占位）必须为 31 个，实际 {total}")
        validate_sector_display(payload)
    elif name == "institution-positions.json":
        validate_institution_display(payload)
    elif name == "etf-volume-events.json":
        validate_etf_volume_display(payload)
