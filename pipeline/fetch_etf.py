"""ETF 日线成交额数据线：固定核心池 + 新浪不复权日线 + 增量合并。

只落原始日线，不做放量识别与信号计算。2026-11-14 起换源（经用户授权，可用性优先）：
- 日线：东财 `fund_etf_hist_em` → 新浪 `fund_etf_hist_sina`（不复权，`amount` 单位元，
  `volume` 单位股，落盘时 /100 统一为手与既有 CSV 口径一致；换源前同日期逐值核对一致）；
- 清单校验：东财 `fund_etf_spot_em` → 腾讯 `qt.gtimg.cn` 实时接口（一次请求 6 只，
  不再与板块线争用东财配额）；
- 换源等于换一套放量历史：换源前已对同日期 close/amount 逐值核对（比值全 1.0）。
"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

from fetch_market import EmRateLimitedError, call_with_retry, save_csv
from rate_limiter import REQUEST_GAP, LIMITER
from trading_calendar import last_completed_trading_day

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
ETF_DAILY_RAW = RAW / "etf-daily"
ETF_DAILY_MANIFEST = RAW / "etf-daily-manifest.json"
ETF_SOURCE = "新浪 ETF 历史日线（不复权）"
ETF_SPOT_SOURCE = "腾讯实时清单（qt.gtimg.cn）"
FIRST_FETCH_START = "20000101"
MIN_ETF_ROWS = 500
ETF_AMOUNT_COLUMNS = ["date", "open", "close", "high", "low", "volume", "amount"]

# 新浪日线代码前缀：5/6 开头为沪市，其余（159 开头）为深市
_SINA_PREFIX = {"588000": "sh", "159915": "sz", "510300": "sh", "510050": "sh",
                "512100": "sh", "510500": "sh"}
# 腾讯实时清单的证券前缀（同映射）
_TENCENT_PREFIX = _SINA_PREFIX

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
    """腾讯实时清单（代码 → 名称），只用于核对固定清单，不用于生成生产名单。

    2026-11-14 起替换东财 fund_etf_spot_em：一次请求返回全部 6 只，不再与板块线
    争用东财 push2 配额（东财 clist 系接口是封 IP 的主要来源）。
    请求前经统一限速器取令牌；冷却期内返回 None（调用方据此跳过本轮校验）。
    """
    if not LIMITER.acquire("tencent", line="etf-spot"):
        print("[etf] 腾讯限流冷却中，跳过 ETF 清单校验（统一限速器）", flush=True)
        return None
    codes = ",".join(f"{_TENCENT_PREFIX[item['code']]}{item['code']}" for item in ETF_UNIVERSE)
    try:
        response = requests.get(f"https://qt.gtimg.cn/q={codes}", timeout=10)
        response.raise_for_status()
    except Exception as exc:
        raise RuntimeError(f"腾讯实时清单请求失败：{exc}") from exc
    spot: dict[str, str] = {}
    for line_text in response.text.strip().split(";"):
        fields = line_text.strip().split("~")
        if len(fields) > 3 and fields[2]:
            spot[fields[2]] = fields[1]
    if not spot:
        raise ValueError("腾讯实时清单返回空数据")
    return spot


def check_etf_universe(spot: dict[str, str]) -> list[dict]:
    """逐个校验固化代码仍存在且名称一致；不一致直接报错，不返回替代标的。"""
    entries = []
    errors = []
    for item in ETF_UNIVERSE:
        code, name = item["code"], item["name"]
        current = spot.get(code)
        if current is None:
            errors.append(f"{code}「{name}」在腾讯实时清单中已查不到")
        elif current != name:
            errors.append(f"{code} 固化名称「{name}」与接口名称「{current}」不一致")
        else:
            entries.append({"code": code, "name": name, "index": item["index"]})
    if errors:
        raise ValueError(f"ETF 清单校验失败，保留上一版数据：{'；'.join(errors)}")
    return entries


def fetch_etf_hist_sina(symbol: str) -> pd.DataFrame:
    """单只 ETF 新浪不复权日线（全历史一次返回）。

    新浪 `volume` 单位为股，落盘 /100 统一为手（与东财历史 CSV 口径一致，
    换源前同日期逐值核对：close/amount 完全一致、volume 转手后一致）。
    请求前经统一限速器取令牌。
    """
    sina_symbol = f"{_SINA_PREFIX.get(symbol, 'sh')}{symbol}"
    if not LIMITER.acquire("sina", line="etf-daily"):
        raise EmRateLimitedError(f"新浪限流冷却中，跳过 ETF {symbol} 日线请求（统一限速器）")
    frame = call_with_retry(ak.fund_etf_hist_sina, symbol=sina_symbol)
    if frame is None or frame.empty:
        raise ValueError(f"新浪 {symbol} ETF 日线返回空数据")
    frame = frame.copy()
    frame["date"] = frame["date"].astype(str).str.slice(0, 10)
    missing = [column for column in ETF_AMOUNT_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{symbol} 日线缺少字段：{missing}")
    frame = frame[ETF_AMOUNT_COLUMNS].copy()
    for column in ETF_AMOUNT_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame["volume"] = (frame["volume"] / 100).round(0)  # 股 → 手，与既有 CSV 口径一致
    frame = (frame.dropna(subset=ETF_AMOUNT_COLUMNS[1:])
             .sort_values("date").drop_duplicates("date", keep="last")
             .reset_index(drop=True))
    if frame.empty:
        raise ValueError(f"新浪 {symbol} 日线为空")
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
    """弃用：新浪日线全量返回、无起止参数，保留函数签名以备回滚东财通道时使用。"""
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

    spot = etf_spot()
    if spot is None:  # 腾讯冷却中：本轮零请求跳过
        return {"status": "cooldown", "updated": [], "skipped": skipped, "failed": []}
    try:
        entries = check_etf_universe(spot)
    except Exception as exc:  # 清单异常不得继续采集，保留上一版 CSV
        print(f"[etf] ETF 清单校验未通过，本轮跳过 ETF 增量：{exc}", flush=True)
        return {"status": "check_failed", "updated": [], "skipped": skipped, "failed": [str(exc)]}

    updated: list[str] = []
    failed: list[str] = []
    for item, path, tail in pending:
        code = item["code"]
        try:
            # 新浪接口无起止参数，全量返回后由 _merge_and_save 合并并裁到最近已完成交易日
            fresh = fetch_etf_hist_sina(code)
            merged, changed = _merge_and_save(path, fresh, expected)
        except EmRateLimitedError as exc:
            # 限流识别即止损：冷却期内中止本轮剩余标的，不再对同一批 ETF 继续请求
            failed.append(code)
            print(f"[etf] 限流/冷却，本轮在 {len(updated)} 只更新后中止：{exc}", flush=True)
            break
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
        time.sleep(REQUEST_GAP)  # 与板块线共用统一单请求间隔（批次串行，不并发）

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
    spot = etf_spot()
    if spot is None:  # 冷却中：全量首拉也不得在冷却期发请求
        return {"status": "cooldown", "rows": [], "failed": []}
    entries = check_etf_universe(spot)
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
                print(f"拉取 {code} {name} 全量日线（新浪）")
                frame = fetch_etf_hist_sina(code)
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
