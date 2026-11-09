"""外部情绪/资金/估值指标的抓取、落盘与口径核验。

研究用途：这些指标只用于候选因子调研，MUST NOT 参与生产信号计算。
所有适配器统一返回含 `date` 与 `value` 两列的 DataFrame；附加列允许保留原始字段。
缺失一律保留 NaN，不做跨日填充（跨日补值等于制造隐性未来信息）。
"""
from __future__ import annotations

import json
import os
import tempfile
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
EXTERNAL = ROOT / "data" / "raw" / "external"
MANIFEST_NAME = "manifest.json"

REQUIRED_FIELDS = (
    "indicator", "interface", "definition", "fetched_at",
    "start", "end", "rows", "missing_rate", "revision_detected",
)
DEFAULT_MAX_FFILL = 1


def _now() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _normalize(frame: pd.DataFrame) -> pd.DataFrame:
    """统一为 date(datetime) + value(float)，按日期升序去重。"""
    result = frame.copy()
    result["date"] = pd.to_datetime(result["date"], errors="coerce")
    result["value"] = pd.to_numeric(result["value"], errors="coerce")
    result = result.dropna(subset=["date"]).sort_values("date")
    result = result.drop_duplicates("date", keep="last").reset_index(drop=True)
    return result


def missing_rate(frame: pd.DataFrame) -> float:
    if frame.empty:
        return 1.0
    return float(frame["value"].isna().mean())


def detect_revision(new: pd.DataFrame, previous: pd.DataFrame | None) -> bool:
    """与上一版缓存比对同一历史日期的取值，任一不一致即视为存在事后修订。"""
    if previous is None or previous.empty or new.empty:
        return False
    merged = new[["date", "value"]].merge(
        previous[["date", "value"]], on="date", suffixes=("_new", "_old")
    )
    if merged.empty:
        return False
    difference = (merged["value_new"] - merged["value_old"]).abs()
    return bool((difference > 1e-9).any())


def build_entry(name: str, interface: str, definition: str, frame: pd.DataFrame,
                *, frequency: str = "daily", grade: str = "P0", notes: str = "",
                previous: pd.DataFrame | None = None, status: str = "ok",
                error: str | None = None) -> dict:
    """构造一条 manifest 记录；失败也保留一条，便于追溯而不阻塞其他指标。"""
    if status != "ok":
        entry = {
            "indicator": name, "interface": interface, "definition": definition,
            "fetched_at": _now(), "start": None, "end": None, "rows": 0,
            "missing_rate": None, "revision_detected": False,
            "frequency": frequency, "grade": grade, "notes": notes,
            "status": status, "error": error,
        }
        validate_entry(entry)
        return entry
    normalized = _normalize(frame)
    entry = {
        "indicator": name, "interface": interface, "definition": definition,
        "fetched_at": _now(),
        "start": normalized["date"].iat[0].strftime("%Y-%m-%d") if len(normalized) else None,
        "end": normalized["date"].iat[-1].strftime("%Y-%m-%d") if len(normalized) else None,
        "rows": int(len(normalized)),
        "missing_rate": round(missing_rate(normalized), 6),
        "revision_detected": detect_revision(normalized, previous),
        "frequency": frequency, "grade": grade, "notes": notes, "status": "ok",
    }
    validate_entry(entry)
    return entry


def validate_entry(entry: dict) -> None:
    missing = [field for field in REQUIRED_FIELDS if field not in entry]
    if missing:
        raise ValueError(f"manifest 记录缺少字段：{', '.join(missing)}")
    if entry["status"] == "ok":
        if not isinstance(entry["rows"], int) or entry["rows"] < 0:
            raise ValueError("manifest 记录 rows 必须为非负整数")
        rate = entry["missing_rate"]
        if rate is None or not (0.0 <= float(rate) <= 1.0):
            raise ValueError("manifest 记录 missing_rate 必须在 [0, 1] 区间")
        if entry["start"] is None or entry["end"] is None:
            raise ValueError("manifest 记录 start/end 不得为空")
    if not isinstance(entry["revision_detected"], bool):
        raise ValueError("manifest 记录 revision_detected 必须为布尔值")


def validate_entries(entries: list[dict]) -> None:
    for entry in entries:
        validate_entry(entry)


def write_manifest(entries: list[dict], path: Path | None = None) -> Path:
    """校验后原子写入 manifest；校验失败不产生半写文件。"""
    validate_entries(entries)
    target = Path(path) if path else EXTERNAL / MANIFEST_NAME
    target.parent.mkdir(parents=True, exist_ok=True)
    payload = {"generated_at": _now(), "entries": entries}
    descriptor, temp_name = tempfile.mkstemp(prefix=MANIFEST_NAME + ".", suffix=".tmp", dir=target.parent)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2, allow_nan=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, target)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)
    return target


def load_manifest(path: Path | None = None) -> dict[str, dict]:
    target = Path(path) if path else EXTERNAL / MANIFEST_NAME
    if not target.exists():
        return {}
    payload = json.loads(target.read_text(encoding="utf-8"))
    return {entry["indicator"]: entry for entry in payload.get("entries", [])}


def series_path(name: str) -> Path:
    return EXTERNAL / f"{name}.csv"


def save_series(name: str, frame: pd.DataFrame) -> Path:
    EXTERNAL.mkdir(parents=True, exist_ok=True)
    target = series_path(name)
    normalized = _normalize(frame)
    normalized.to_csv(target, index=False, encoding="utf-8-sig", date_format="%Y-%m-%d")
    return target


def load_series(name: str) -> pd.DataFrame | None:
    target = series_path(name)
    if not target.exists():
        return None
    frame = pd.read_csv(target)
    if frame.empty:
        return None
    return _normalize(frame)


def align_to_calendar(frame: pd.DataFrame, calendar, max_ffill: int = DEFAULT_MAX_FFILL) -> pd.Series:
    """按交易日历对齐；缺口最多前向填充 max_ffill 个交易日，更长的缺口保留 NaN。"""
    series = frame.set_index("date")["value"].sort_index().astype(float)
    aligned = series.reindex(pd.DatetimeIndex(calendar))
    if max_ffill and max_ffill > 0:
        aligned = aligned.ffill(limit=int(max_ffill))
    return aligned


def normalize_margin_units(series: pd.Series) -> tuple[pd.Series, list[str]]:
    """两融接口的单位一致性守卫：若出现 100 倍以上的数量级跳变，视为单位切换并还原。

    实测（akshare 1.20.18）沪深两市接口在 2010 年首年试点期即为「元」，未发现单位断点；
    该函数保留为守卫，断点列表为空即代表单位一致。同时把 0 值视为缺失（余额不可能为 0）。
    """
    cleaned = series.astype(float).replace(0, np.nan)
    values = cleaned.to_numpy(copy=True)
    breaks: list[str] = []
    for position in range(1, len(values)):
        previous, current = values[position - 1], values[position]
        if not (np.isfinite(previous) and np.isfinite(current)) or previous <= 0 or current <= 0:
            continue
        ratio = current / previous
        if ratio >= 100:
            values[:position] *= 1e4
            breaks.append(series.index[position].strftime("%Y-%m-%d"))
        elif ratio <= 0.01:
            values[:position] /= 1e4
            breaks.append(series.index[position].strftime("%Y-%m-%d"))
    return pd.Series(values, index=series.index), breaks


def _import_akshare():
    import akshare as ak
    return ak


def fetch_qvix(underlying: str = "50etf") -> pd.DataFrame:
    ak = _import_akshare()
    function_names = {
        "50etf": "index_option_50etf_qvix",
        "300etf": "index_option_300etf_qvix",
        "500etf": "index_option_500etf_qvix",
    }
    if underlying not in function_names:
        raise ValueError(f"未知的 QVIX 标的：{underlying}")
    raw = getattr(ak, function_names[underlying])()
    return _normalize(raw.rename(columns={"close": "value"})[["date", "value"]])


def fetch_below_net_asset() -> pd.DataFrame:
    ak = _import_akshare()
    raw = ak.stock_a_below_net_asset_statistics(symbol="全部A股")
    frame = raw.rename(columns={"below_net_asset_ratio": "value"})
    return _normalize(frame[["date", "value", "below_net_asset", "total_company"]])


def fetch_high_low() -> pd.DataFrame:
    ak = _import_akshare()
    raw = ak.stock_a_high_low_statistics(symbol="all")
    frame = raw.copy()
    total = frame["high20"].astype(float) + frame["low20"].astype(float)
    frame["value"] = (frame["high20"].astype(float) / total.replace(0, np.nan))
    return _normalize(frame)


_MARGIN_CACHE: dict[str, pd.DataFrame] = {}


def _margin_pair() -> pd.DataFrame:
    """沪深两市融资余额/融资买入额按日合并；仅保留两市都有数据的交易日。"""
    if "pair" in _MARGIN_CACHE:
        return _MARGIN_CACHE["pair"]
    ak = _import_akshare()
    parts = {}
    for label, func in (("sh", ak.macro_china_market_margin_sh), ("sz", ak.macro_china_market_margin_sz)):
        raw = func().rename(columns={"日期": "date", "融资余额": "balance", "融资买入额": "buy"})
        raw["date"] = pd.to_datetime(raw["date"], errors="coerce")
        raw = raw.dropna(subset=["date"]).sort_values("date").drop_duplicates("date", keep="last")
        for column in ("balance", "buy"):
            normalized, _ = normalize_margin_units(raw.set_index("date")[column].astype(float))
            raw[column] = normalized.to_numpy()
        parts[label] = raw.set_index("date")[["balance", "buy"]]
    merged = parts["sh"].join(parts["sz"], how="inner", suffixes=("_sh", "_sz"))
    merged = merged.dropna(subset=["balance_sh", "balance_sz", "buy_sh", "buy_sz"])
    pair = pd.DataFrame({
        "date": merged.index,
        "balance": merged["balance_sh"] + merged["balance_sz"],
        "buy": merged["buy_sh"] + merged["buy_sz"],
    }).reset_index(drop=True)
    _MARGIN_CACHE["pair"] = pair
    return pair


def fetch_margin_balance() -> pd.DataFrame:
    pair = _margin_pair()
    return _normalize(pair[["date", "balance"]].rename(columns={"balance": "value"}))


def fetch_margin_turnover() -> pd.DataFrame:
    """融资买入额 / 融资余额：单位无关，绕开两融接口的单位断点。"""
    pair = _margin_pair()
    frame = pd.DataFrame({
        "date": pair["date"],
        "value": pair["buy"] / pair["balance"].replace(0, np.nan),
    })
    return _normalize(frame)


def fetch_erp(index_symbol: str = "沪深300") -> pd.DataFrame:
    """股债利差 = 100 / 滚动市盈率 - 10 年期国债收益率（百分点）。

    乐咕口径的指数市盈率为**月频**，因此该指标为月频；对齐日频时不得跨月填充。
    """
    ak = _import_akshare()
    pe = ak.stock_index_pe_lg(symbol=index_symbol).rename(columns={"日期": "date", "滚动市盈率": "pe"})
    pe["date"] = pd.to_datetime(pe["date"], errors="coerce")
    pe = pe.dropna(subset=["date", "pe"]).sort_values("date")
    bond = ak.bond_zh_us_rate(start_date="20050101").rename(
        columns={"日期": "date", "中国国债收益率10年": "yield10"}
    )
    bond["date"] = pd.to_datetime(bond["date"], errors="coerce")
    bond = bond.dropna(subset=["date"]).sort_values("date")
    merged = pd.merge_asof(pe[["date", "pe"]], bond[["date", "yield10"]], on="date", direction="backward")
    merged["value"] = 100.0 / merged["pe"].astype(float) - merged["yield10"].astype(float)
    return _normalize(merged[["date", "value", "pe", "yield10"]])


def _recent_trading_days(days: int) -> list[str]:
    try:
        ak = _import_akshare()
        calendar = pd.to_datetime(ak.tool_trade_date_hist_sina()["trade_date"])
        calendar = calendar[calendar <= pd.Timestamp.today().normalize()]
        recent = calendar.tail(days)
    except Exception:
        recent = pd.bdate_range(end=pd.Timestamp.today().normalize(), periods=days)
    return [day.strftime("%Y%m%d") for day in recent]


def fetch_limit_pool(days: int = 60) -> pd.DataFrame:
    """涨停/跌停家数：东财股池接口仅覆盖近期，历史无法回溯，按 P1 使用。"""
    ak = _import_akshare()
    rows = []
    for day in _recent_trading_days(days):
        up = down = np.nan
        try:
            up = float(len(ak.stock_zt_pool_em(date=day)))
        except Exception:
            pass
        try:
            down = float(len(ak.stock_zt_pool_dtgc_em(date=day)))
        except Exception:
            pass
        if not (np.isfinite(up) or np.isfinite(down)):
            continue
        rows.append({"date": pd.to_datetime(day), "limit_up": up, "limit_down": down})
    if not rows:
        raise ValueError("涨停股池在近期区间内全部返回空数据")
    frame = pd.DataFrame(rows)
    total = frame["limit_up"].fillna(0) + frame["limit_down"].fillna(0)
    frame["value"] = frame["limit_up"].fillna(0) / total.replace(0, np.nan)
    return _normalize(frame)


ADAPTERS: dict[str, dict] = {
    "qvix_50": {
        "interface": "akshare index_option_50etf_qvix",
        "definition": "上证50ETF期权波动率指数（QVIX）日收盘，A股版 VIX",
        "frequency": "daily", "grade": "P0", "func": lambda: fetch_qvix("50etf"),
    },
    "qvix_300": {
        "interface": "akshare index_option_300etf_qvix",
        "definition": "沪深300ETF期权波动率指数（QVIX）日收盘",
        "frequency": "daily", "grade": "P0", "func": lambda: fetch_qvix("300etf"),
    },
    "qvix_500": {
        "interface": "akshare index_option_500etf_qvix",
        "definition": "中证500ETF期权波动率指数（QVIX）日收盘",
        "frequency": "daily", "grade": "P0", "func": lambda: fetch_qvix("500etf"),
    },
    "below_net_asset_ratio": {
        "interface": "akshare stock_a_below_net_asset_statistics(symbol='全部A股')",
        "definition": "全A破净股占比（破净家数 / 上市公司总数），日频",
        "frequency": "daily", "grade": "P0", "func": fetch_below_net_asset,
    },
    "high_low_ratio_20": {
        "interface": "akshare stock_a_high_low_statistics(symbol='all')",
        "definition": "20 日创新高家数 / (创新高 + 创新低家数)，个股层宽度",
        "frequency": "daily", "grade": "P1",
        "notes": "接口仅返回约 2 年历史，不足 5 年，按 P1 处理",
        "func": fetch_high_low,
    },
    "margin_balance": {
        "interface": "akshare macro_china_market_margin_sh + macro_china_market_margin_sz",
        "definition": "沪深两市融资余额合计（元）；已核验两市单位一致，0 值按缺失处理",
        "frequency": "daily", "grade": "P0", "func": fetch_margin_balance,
    },
    "margin_turnover": {
        "interface": "akshare macro_china_market_margin_sh + macro_china_market_margin_sz",
        "definition": "融资买入额 / 融资余额（单位无关），衡量杠杆资金活跃度",
        "frequency": "daily", "grade": "P0", "func": fetch_margin_turnover,
    },
    "erp_csi300": {
        "interface": "akshare stock_index_pe_lg(沪深300) + bond_zh_us_rate",
        "definition": "股债利差 = 100 / 滚动市盈率 - 10 年期国债收益率（百分点）",
        "frequency": "monthly", "grade": "P1",
        "notes": "指数市盈率仅月频可得，无法在不做跨月填充的前提下并入日频因子层",
        "func": fetch_erp,
    },
    "limit_up_ratio": {
        "interface": "akshare stock_zt_pool_em + stock_zt_pool_dtgc_em",
        "definition": "涨停家数 / (涨停 + 跌停家数)，近 60 个交易日",
        "frequency": "daily", "grade": "P1",
        "notes": "东财股池接口仅覆盖近期，历史无法回溯，样本不足以支撑统计结论",
        "func": fetch_limit_pool,
    },
}


def fetch_all(names: list[str] | None = None, *, save: bool = True) -> dict[str, pd.DataFrame]:
    """逐个抓取并落盘；单指标失败只记录，不中断其余指标。"""
    selected = names or list(ADAPTERS)
    unknown = [name for name in selected if name not in ADAPTERS]
    if unknown:
        raise ValueError(f"未知指标：{', '.join(unknown)}")
    previous_manifest = load_manifest()
    frames: dict[str, pd.DataFrame] = {}
    entries: list[dict] = []
    for name in selected:
        spec = ADAPTERS[name]
        try:
            frame = spec["func"]()
            previous = load_series(name)
            entry = build_entry(
                name, spec["interface"], spec["definition"], frame,
                frequency=spec["frequency"], grade=spec["grade"],
                notes=spec.get("notes", ""), previous=previous,
            )
            if save:
                save_series(name, frame)
            frames[name] = frame
            print(f"  {name}: {entry['rows']} 行，{entry['start']} ~ {entry['end']}，"
                  f"缺失率 {entry['missing_rate']:.2%}")
        except Exception as exc:  # 单指标失败不应阻塞其他指标
            entry = build_entry(
                name, spec["interface"], spec["definition"], pd.DataFrame(columns=["date", "value"]),
                frequency=spec["frequency"], grade=spec["grade"],
                notes=spec.get("notes", ""), status="failed", error=f"{type(exc).__name__}: {exc}",
            )
            print(f"  警告：{name} 抓取失败，已记入 manifest：{exc}")
        entries.append(entry)
    if save:
        existing = {name: entry for name, entry in previous_manifest.items() if name not in selected}
        write_manifest(list(existing.values()) + entries)
    return frames


if __name__ == "__main__":
    import sys
    targets = sys.argv[1:] or None
    print("抓取外部指标：")
    fetch_all(targets)
