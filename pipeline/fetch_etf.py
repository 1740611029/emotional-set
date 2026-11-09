"""ETF 日线成交额数据线：固定核心池 + 东财不复权日线 + 增量合并。

只落原始日线，不做放量识别与信号计算。数据源与复权口径固定：
东财 `fund_etf_hist_em`（不复权），`amount` 单位为元。换源等于换一套放量历史，
必须单独立项并出对照报告。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd

from fetch_market import EM_COLUMN_RENAME, call_with_retry, save_csv
from trading_calendar import last_completed_trading_day

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
ETF_DAILY_RAW = RAW / "etf-daily"
ETF_DAILY_MANIFEST = RAW / "etf-daily-manifest.json"
ETF_SOURCE = "东财 ETF 历史日线（不复权）"
FIRST_FETCH_START = "20000101"
MIN_ETF_ROWS = 500
ETF_AMOUNT_COLUMNS = ["date", "open", "close", "high", "low", "volume", "amount"]

# 生产清单：硬编码，代码是唯一主键，名称只用于每日校验（2026-11-06 与东财 ETF 实时清单核对）。
# 不做动态名单：换标的等于换一套放量历史。
ETF_UNIVERSE: tuple[dict[str, str], ...] = (
    {"code": "588000", "name": "科创50ETF华夏", "index": "科创50"},
    {"code": "159915", "name": "创业板ETF易方达", "index": "创业板指"},
    {"code": "510300", "name": "沪深300ETF华泰柏瑞", "index": "沪深300"},
    {"code": "510050", "name": "上证50ETF华夏", "index": "上证50"},
    {"code": "512100", "name": "中证1000ETF南方", "index": "中证1000"},
    {"code": "510500", "name": "中证500ETF南方", "index": "中证500"},
)


def etf_spot() -> dict[str, str]:
    """东财 ETF 实时清单（代码 → 名称），只用于核对固定清单，不用于生成生产名单。"""
    frame = call_with_retry(ak.fund_etf_spot_em)
    code_col = next((column for column in frame.columns if "代码" in column), None)
    name_col = next((column for column in frame.columns if "名称" in column), None)
    if not code_col or not name_col:
        raise ValueError(f"东财 ETF 清单缺少代码/名称字段：{frame.columns.tolist()}")
    return {str(row[code_col]).strip(): str(row[name_col]).strip() for _, row in frame.iterrows()}


def check_etf_universe(spot: dict[str, str]) -> list[dict]:
    """逐个校验固化代码仍存在且名称一致；不一致直接报错，不返回替代标的。"""
    entries = []
    errors = []
    for item in ETF_UNIVERSE:
        code, name = item["code"], item["name"]
        current = spot.get(code)
        if current is None:
            errors.append(f"{code}「{name}」在东财 ETF 清单中已查不到")
        elif current != name:
            errors.append(f"{code} 固化名称「{name}」与接口名称「{current}」不一致")
        else:
            entries.append({"code": code, "name": name, "index": item["index"]})
    if errors:
        raise ValueError(f"ETF 清单校验失败，保留上一版数据：{'；'.join(errors)}")
    return entries


def fetch_etf_hist_em(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单只 ETF 东财不复权日线；列名与板块日线同构，`amount` 单位为元。"""
    frame = call_with_retry(
        ak.fund_etf_hist_em, symbol=symbol, period="daily",
        start_date=start_date, end_date=end_date, adjust="",
    )
    frame = frame.rename(columns=EM_COLUMN_RENAME)
    missing = [column for column in ETF_AMOUNT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{symbol} 日线缺少字段：{missing}")
    frame = frame[ETF_AMOUNT_COLUMNS].copy()
    frame["date"] = frame["date"].astype(str).str.slice(0, 10)
    for column in ETF_AMOUNT_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = (frame.dropna(subset=ETF_AMOUNT_COLUMNS[1:])
             .sort_values("date").drop_duplicates("date", keep="last")
             .reset_index(drop=True))
    if frame.empty:
        raise ValueError(f"{symbol} 在 {start_date}~{end_date} 区间无日线")
    return frame


def read_etf_csv(path: Path) -> pd.DataFrame:
    """读本地 ETF 日线（date 保持字符串，与落盘格式一致）。"""
    frame = pd.read_csv(path, dtype={"date": str})
    return frame.sort_values("date").reset_index(drop=True)


def _values_equal(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    """逐值比对两份日线：日期按字符串、价格量额按统一 6 位浮点比对。"""
    if left["date"].tolist() != right["date"].tolist():
        return False
    for column in ETF_AMOUNT_COLUMNS[1:]:
        fresh = pd.to_numeric(left[column], errors="coerce").astype("float64").round(6)
        cached = pd.to_numeric(right[column], errors="coerce").astype("float64").round(6)
        if not fresh.equals(cached):
            return False
    return True


def _request_start(tail: str | None) -> str:
    """增量起点含本地尾日（含）：每轮重取最后一根 bar，配合逐值比对修正未定稿量额。"""
    if not tail:
        return FIRST_FETCH_START
    return pd.Timestamp(tail).strftime("%Y%m%d")


def _relative(path: Path) -> str:
    """项目内路径写相对路径；演练目录在项目外时退回绝对路径。"""
    try:
        return str(path.relative_to(ROOT))
    except ValueError:
        return str(path)


def _write_manifest(rows: list[dict], *, snapshot: dict | None = None) -> None:
    payload = {
        "source": ETF_SOURCE,
        "universe": [dict(item) for item in ETF_UNIVERSE],
        "universe_snapshot": snapshot or {},
        "boards": rows,
    }
    ETF_DAILY_MANIFEST.parent.mkdir(parents=True, exist_ok=True)
    ETF_DAILY_MANIFEST.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def _merge_and_save(path: Path, fresh: pd.DataFrame, expected: str) -> tuple[pd.DataFrame, bool]:
    """合并本地与源侧日线；末行裁到最近已完成交易日，仅数值确实变化时才覆写。"""
    if path.exists():
        cached = read_etf_csv(path)
        merged = (pd.concat([cached, fresh], ignore_index=True)
                  .sort_values("date").drop_duplicates("date", keep="last")
                  .reset_index(drop=True))
    else:
        merged = fresh.sort_values("date").drop_duplicates("date", keep="last").reset_index(drop=True)
    # 兜底：源侧若给出盘中未完成 bar，也不得写盘（终点只认最近已完成交易日）
    merged = merged[merged["date"] <= expected].reset_index(drop=True)
    if path.exists() and len(merged) == len(cached) and _values_equal(merged, cached):
        return merged, False
    save_csv(merged, path)
    return merged, True


def update_etf_daily(end_date: str | None = None) -> dict:
    """增量合并核心池 ETF 日线；失败只打印告警，不抛出中断既有数据线。

    终点取交易日历给出的最近已完成交易日，盘中不会把未完成 bar 写盘。
    未初始化（无 manifest）时不触发全量抓取，交由 `fetch_etf_daily()` 显式执行。
    """
    expected = end_date or last_completed_trading_day()[0]
    if not ETF_DAILY_MANIFEST.exists():
        print(f"[etf] ETF 日线尚未初始化，跳过增量（可运行 python -m pipeline.fetch_etf）", flush=True)
        return {"status": "uninitialized", "updated": [], "skipped": 0, "failed": []}

    manifest = json.loads(ETF_DAILY_MANIFEST.read_text(encoding="utf-8"))
    rows_by_code = {row["code"]: row for row in manifest.get("boards", [])}
    pending = []
    skipped = 0
    for item in ETF_UNIVERSE:
        path = ETF_DAILY_RAW / f"{item['code']}.csv"
        tail = read_etf_csv(path)["date"].iat[-1] if path.exists() else None
        if tail and tail >= expected:
            skipped += 1
            continue
        pending.append((item, path, tail))
    if not pending:
        return {"status": "skipped", "updated": [], "skipped": skipped, "failed": []}

    try:
        entries = check_etf_universe(etf_spot())
    except Exception as exc:  # 清单异常不得继续采集，保留上一版 CSV
        print(f"[etf] ETF 清单校验未通过，本轮跳过 ETF 增量：{exc}", flush=True)
        return {"status": "check_failed", "updated": [], "skipped": skipped, "failed": [str(exc)]}

    updated: list[str] = []
    failed: list[str] = []
    for item, path, tail in pending:
        code = item["code"]
        try:
            fresh = fetch_etf_hist_em(code, _request_start(tail), expected.replace("-", ""))
            merged, changed = _merge_and_save(path, fresh, expected)
        except Exception as exc:
            failed.append(code)
            print(f"[etf] {code} {item['name']} 增量拉取失败：{exc}", flush=True)
            continue
        row = rows_by_code.get(code) or {"code": code, "name": item["name"], "index": item["index"]}
        row.update({
            "name": item["name"], "index": item["index"], "source": ETF_SOURCE,
            "rows": len(merged), "start": merged["date"].iat[0], "end": merged["date"].iat[-1],
            "insufficient_history": len(merged) < MIN_ETF_ROWS,
            "path": _relative(path),
        })
        rows_by_code[code] = row
        if changed:
            updated.append(code)
        time.sleep(0.2)

    rows = [rows_by_code.get(item["code"]) or {
        "code": item["code"], "name": item["name"], "index": item["index"],
        "source": ETF_SOURCE, "rows": 0, "start": None, "end": None,
        "insufficient_history": True, "path": _relative(ETF_DAILY_RAW / f"{item['code']}.csv"),
    } for item in ETF_UNIVERSE]
    _write_manifest(rows, snapshot={"接口校验": "通过", "校验日期": date.today().isoformat()})
    print(f"[etf] 增量完成：更新 {len(updated)} 只、跳过 {skipped} 只、失败 {len(failed)} 只", flush=True)
    return {"status": "ok", "updated": updated, "skipped": skipped, "failed": failed}


def fetch_etf_daily(end_date: str | None = None) -> dict:
    """首次全量抓取核心池 ETF 日线并生成 manifest；单只失败只告警继续。"""
    expected = end_date or last_completed_trading_day()[0]
    entries = check_etf_universe(etf_spot())
    rows = []
    failed: list[str] = []
    for item in entries:
        code, name = item["code"], item["name"]
        path = ETF_DAILY_RAW / f"{code}.csv"
        try:
            if path.exists():
                frame = read_etf_csv(path)
                print(f"使用已拉取 {code} {name}（{len(frame)} 行）")
            else:
                print(f"拉取 {code} {name} 全量日线")
                frame = fetch_etf_hist_em(code, FIRST_FETCH_START, expected.replace("-", ""))
                save_csv(frame, path)
        except Exception as exc:
            failed.append(code)
            print(f"警告：{code} {name} 拉取失败，继续处理其余 ETF：{exc}", flush=True)
            continue
        row = {
            "code": code, "name": name, "index": item["index"], "source": ETF_SOURCE,
            "rows": len(frame), "start": frame["date"].iat[0], "end": frame["date"].iat[-1],
            "insufficient_history": len(frame) < MIN_ETF_ROWS,
            "path": _relative(path),
        }
        if row["insufficient_history"]:
            print(f"  警告：{code} {name} 历史不足 {MIN_ETF_ROWS} 个交易日，已标记")
        rows.append(row)
        time.sleep(0.2)
    _write_manifest(rows, snapshot={
        "接口校验": "通过", "校验日期": date.today().isoformat(),
        "接口标的数": len(ETF_UNIVERSE),
    })
    print(f"完成：{len(rows)} 只 ETF（失败 {len(failed)} 只）")
    return {"status": "ok" if not failed else "partial", "rows": rows, "failed": failed}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", nargs="?", default="etf", choices=["etf"])
    arguments = parser.parse_args()
    fetch_etf_daily()
