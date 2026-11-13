"""原始行情补齐与增量刷新。

首次运行（`data/raw` 为空）时全量拉取；之后按交易日增量合并，避免重复下载历史。
本模块只负责原始日线，不参与信号计算。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
PIPELINE = ROOT / "pipeline"
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

import pandas as pd  # noqa: E402

from fetch_market import (  # noqa: E402
    EM_SECTOR_MANIFEST, EM_SECTOR_RAW, INDEX_RAW, INDEXES, MIN_SECTOR_ROWS, RAW, SECTOR_BATCH_PAUSE,
    SECTOR_BATCH_SIZE, SECTOR_COLUMNS, SECTOR_REQUEST_GAP, THS_CODES,
    EmRateLimitedError, call_with_retry, check_sector_universe, em_cooldown_remaining,
    fetch_sector_hist_em, fetch_ths_year, save_csv, sector_request_start, sector_spot,
)
from research_algorithms import read_index_csv  # noqa: E402
from trading_calendar import (  # noqa: E402
    lag_trading_days, last_completed_trading_day, refresh_calendar,
)

INDEX_MANIFEST = RAW / "indices-ths-manifest.json"
CFFEX_RAW = RAW / "cffex"
REFRESH_STATE = RAW / "refresh-state.json"
MIN_INDEX_ROWS = 1250
STALE_SECTOR_LAG_DAYS = 60  # 板块本地尾部落后超过该交易天数视为已停更，不再请求
CN = ZoneInfo("Asia/Shanghai")
# 补齐完成标记的语义：只有“确实覆盖到当时的最近交易日”才算完成。
# 失败（源尚未发布或拉取报错）不算完成，下一次启动仍会重新尝试补齐。

_index_attempt_day: str | None = None  # 进程内当日降频标记：已覆盖最近交易日才算尝试完成


def update_indices(progress: Progress | None = None, *, force: bool = False) -> list[str]:
    """增量合并当年日线；返回更新后有变化的指数代码。

    当日降频：同花顺日线约 15:30 定稿，且判重只看行数与尾日、量额修正本来就要等次日
    重取覆盖，因此"本地已覆盖最近交易日且当日已请求过"时后续轮次直接跳过，避免每次
    启动/补齐都无条件打 7 个请求。本地未覆盖（源未发布）时不受标记影响，继续请求。
    force=True（手动 --force）绕过标记强制重拉。
    """
    global _index_attempt_day
    progress = progress or Progress()
    today = date.today().isoformat()
    gap = data_gap()
    if not force and "indices" not in gap["stale"] and _index_attempt_day == today:
        progress.update("indices", f"指数已覆盖 {gap['expected']}，当日已更新过，跳过")
        return []
    year = date.today().year
    changed: list[str] = []
    for code, name in INDEXES.items():
        path = INDEX_RAW / f"{code}.csv"
        try:
            fresh = call_with_retry(fetch_ths_year, symbol=THS_CODES[code], year=year)
        except Exception as exc:  # 单只失败不影响其余指数
            print(f"[bootstrap] {code} {name} 增量拉取失败：{exc}", flush=True)
            continue
        if path.exists():
            cached = read_index_csv(path)
            fresh["date"] = pd.to_datetime(fresh["date"])
            merged = (pd.concat([cached, fresh], ignore_index=True)
                      .sort_values("date").drop_duplicates("date", keep="last")
                      .reset_index(drop=True))
            if len(merged) == len(cached) and merged["date"].iat[-1] == cached["date"].iat[-1]:
                time.sleep(0.2)
                continue
        else:
            merged = fresh.sort_values("date").reset_index(drop=True)
        if len(merged) < MIN_INDEX_ROWS:
            print(f"[bootstrap] {code} 仅 {len(merged)} 行，跳过写入", flush=True)
            continue
        save_csv(merged, path)
        changed.append(code)
        progress.update("indices", f"{code} 更新至 {merged['date'].iat[-1]:%Y-%m-%d}")
        time.sleep(0.2)
    if changed:
        _write_index_manifest()
    # 只有真覆盖到最近交易日才标记当日已完成；未覆盖（源未发布）留给下轮再试
    if "indices" not in data_gap()["stale"]:
        _index_attempt_day = today
    return changed


class Progress:
    """极简进度记录，供 API 展示"数据准备中"的当前阶段。"""

    def __init__(self) -> None:
        self.stage = "idle"
        self.detail = ""
        self.started_at: str | None = None

    def update(self, stage: str, detail: str = "") -> None:
        self.stage = stage
        self.detail = detail
        if self.started_at is None:
            self.started_at = pd.Timestamp.now().isoformat(timespec="seconds")
        print(f"[bootstrap] {stage} {detail}", flush=True)

    def as_dict(self) -> dict:
        return {"stage": self.stage, "detail": self.detail, "started_at": self.started_at}


def _index_files() -> dict[str, Path]:
    return {code: INDEX_RAW / f"{code}.csv" for code in INDEXES}


def raw_ready() -> bool:
    """原始数据是否足以构建信号（指数齐全 + 板块清单存在）。"""
    files = _index_files()
    if not all(path.exists() for path in files.values()):
        return False
    for path in files.values():
        try:
            if len(pd.read_csv(path)) < MIN_INDEX_ROWS:
                return False
        except Exception:
            return False
    return EM_SECTOR_MANIFEST.exists()


def _write_index_manifest() -> None:
    rows = []
    for code, name in INDEXES.items():
        path = INDEX_RAW / f"{code}.csv"
        if not path.exists():
            continue
        frame = read_index_csv(path)
        rows.append({
            "code": code, "name": name, "source": "同花顺公开日线（按年份）",
            "rows": len(frame), "start": frame["date"].iat[0].strftime("%Y-%m-%d"),
            "end": frame["date"].iat[-1].strftime("%Y-%m-%d"),
            "path": str(path.relative_to(ROOT)),
        })
    INDEX_RAW.mkdir(parents=True, exist_ok=True)
    INDEX_MANIFEST.write_text(json.dumps(rows, ensure_ascii=False, indent=2), encoding="utf-8")


def ensure_raw_data(progress: Progress | None = None) -> bool:
    """缺失时全量拉取。返回是否真正执行了拉取。"""
    progress = progress or Progress()
    if raw_ready():
        return False

    progress.update("indices", "全量拉取指数日线")
    from fetch_market import fetch_indexes
    fetch_indexes()

    progress.update("sectors", "全量拉取东财行业板块日线")
    from fetch_market import fetch_sectors
    fetch_sectors()

    progress.update("ready", "原始数据就绪")
    return True


def _sector_universe_check(manifest: dict) -> dict:
    """采集前校验固定清单，并把接口侧差异写入留痕；校验失败直接向上抛。

    清单是硬编码的，因此“变化”只会是东财改名/下架或细分板块增减：前两者必须人工评审，
    后者只留痕不入列。绝不允许静默换一个板块继续算。
    """
    spot = sector_spot()
    _boards, snapshot = check_sector_universe(spot)
    previous = manifest.get("universe_snapshot") or {}
    if previous and any(previous.get(key) != value for key, value in snapshot.items()):
        changes = list(manifest.get("universe_changes") or [])
        changes.append({"date": date.today().isoformat(), "from": previous, "to": snapshot})
        manifest["universe_changes"] = changes[-20:]
    manifest["universe_snapshot"] = snapshot
    return manifest


def _frame_values_equal(left: pd.DataFrame, right: pd.DataFrame) -> bool:
    """逐值比对两份日线：日期按字符串、价格量额按 float（统一 6 位）比对。"""
    if left["date"].tolist() != right["date"].tolist():
        return False
    for column in SECTOR_COLUMNS:
        if column == "date":
            continue
        fresh = pd.to_numeric(left[column], errors="coerce").astype("float64").round(6)
        cached = pd.to_numeric(right[column], errors="coerce").astype("float64").round(6)
        if not fresh.equals(cached):
            return False
    return True


def update_sectors(progress: Progress | None = None) -> int:
    """增量合并东财行业板块日线；返回有变化的板块数量。

    先读本地尾日：已覆盖最近已完成交易日的板块不请求；确实需要请求时才校验固定清单。
    拉取终点由交易日历给出（15:00 分界），因此盘中不会把未完成 bar 当收盘数据。
    起点含尾日（见 `sector_request_start`）且写入前逐值比对，因此尾日那根 bar 会在
    下一轮被重取并在数值确实发生修正时被覆写，不会把收盘瞬间未定稿的量额定格。

    push2 节奏控制：入口先看限流冷却（冷却中整轮跳过，零请求）；请求按
    SECTOR_BATCH_SIZE 分批、批间 SECTOR_BATCH_PAUSE，单板块间隔 SECTOR_REQUEST_GAP；
    途中再触发限流则立即中止本轮（已落盘的板块保留，manifest 不写半截状态）。
    """
    progress = progress or Progress()
    remaining = em_cooldown_remaining()
    if remaining > 0:
        progress.update("sectors",
                        f"东财限流冷却中（剩余 {remaining / 60:.0f} 分钟），本轮跳过板块增量")
        return 0
    if not EM_SECTOR_MANIFEST.exists():
        return 0

    manifest = json.loads(EM_SECTOR_MANIFEST.read_text(encoding="utf-8"))
    expected, _basis = last_completed_trading_day()
    pending = []
    skipped = 0
    for item in manifest["boards"]:
        path = EM_SECTOR_RAW / f"{item['code']}.csv"
        tail = _tail_date(path, 0) if path.exists() else None
        # 已覆盖最近交易日 → 跳过；落后超 60 个交易日 → 视为停更（东财撤销的板块不再发布），跳过
        if tail and (tail >= expected or lag_trading_days(tail, expected) > STALE_SECTOR_LAG_DAYS):
            skipped += 1
            continue
        pending.append((item, path, tail))
    if not pending:
        if skipped:
            progress.update("sectors", f"板块已覆盖 {expected}，跳过 {skipped} 个")
        return 0

    try:
        manifest = _sector_universe_check(manifest)
    except EmRateLimitedError:
        # 冷却期满后的第一次请求即解封探测：仍限流则重新计时，本轮到此为止
        raise
    except Exception as exc:  # 口径异常不得继续采集，保留上一版产物
        raise RuntimeError(f"板块清单校验未通过，本轮跳过板块增量：{exc}") from exc

    changed = 0
    for batch_start in range(0, len(pending), SECTOR_BATCH_SIZE):
        if batch_start:
            progress.update("sectors",
                            f"已请求 {batch_start}/{len(pending)} 个板块，批间休息 {SECTOR_BATCH_PAUSE:.0f}s")
            time.sleep(SECTOR_BATCH_PAUSE)
        for item, path, tail in pending[batch_start:batch_start + SECTOR_BATCH_SIZE]:
            code = item["code"]
            try:
                fresh = fetch_sector_hist_em(code, sector_request_start(tail), expected.replace("-", ""))
            except EmRateLimitedError as exc:
                print(f"[bootstrap] 东财限流，本轮在 {changed} 个板块更新后中止（冷却自动计时）：{exc}",
                      flush=True)
                raise
            except Exception as exc:
                print(f"[bootstrap] 板块 {code} 拉取失败：{exc}", flush=True)
                continue
            if path.exists():
                cached = pd.read_csv(path, dtype={"date": str})
                merged = (pd.concat([cached, fresh], ignore_index=True)
                          .sort_values("date").drop_duplicates("date", keep="last")
                          .reset_index(drop=True))
                # 起点含尾日意味着每轮都会重取最后一根 bar，因此不能再用「行数与尾日未变」
                # 判定无变化：那会把未定稿的量额修正直接丢掉
                if len(merged) == len(cached) and _frame_values_equal(merged, cached):
                    time.sleep(SECTOR_REQUEST_GAP)
                    continue
            else:
                merged = fresh
            save_csv(merged, path)
            item["rows"] = len(merged)
            item["start"] = merged["date"].iat[0]
            item["end"] = merged["date"].iat[-1]
            item["insufficient_history"] = len(merged) < MIN_SECTOR_ROWS
            changed += 1
            if changed % 10 == 0:
                progress.update("sectors", f"已更新 {changed} 个板块")
            time.sleep(SECTOR_REQUEST_GAP)
    if skipped:
        progress.update("sectors", f"跳过已最新 {skipped} 个板块")
    EM_SECTOR_MANIFEST.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return changed


def update_institutions(progress: Progress | None = None) -> dict:
    """机构持仓增量更新：独立产物，失败只记录日志，不阻断指数与板块刷新。"""
    progress = progress or Progress()
    progress.update("institutions", "更新机构持仓")
    try:
        from institution_positions import build_payload  # noqa: PLC0415
        from io_utils import protected_write  # noqa: PLC0415

        payload = build_payload()
        protected_write("institution-positions.json", payload, compact_arrays=True)
        return {
            "status": "ok",
            "as_of": payload["as_of"],
            "missing": len(payload["quality"]["missing_dates"]),
        }
    except Exception as exc:  # 机构数据缺失不应影响既有页面
        detail = f"{type(exc).__name__}: {exc}"
        print(f"机构持仓更新失败（已跳过）：{detail}")
        return {"status": "failed", "error": detail}


def refresh_daily(progress: Progress | None = None) -> dict:
    """收盘后刷新：先补原始日线，再由调用方重建 JSON。

    三条数据线彼此解耦：板块（东财）限流/失败只影响自身，不再中断机构持仓更新。
    """
    progress = progress or Progress()
    ensure_raw_data(progress)
    indices = update_indices(progress)
    sectors = 0
    try:
        sectors = update_sectors(progress)
    except Exception as exc:  # 板块线失败不拖累机构数据线（二者数据源无关）
        print(f"[bootstrap] 板块增量失败（不影响指数与机构数据线）：{exc}", flush=True)
    institutions = update_institutions(progress)
    progress.update("ready", f"指数 {len(indices)} 个、板块 {sectors} 个有更新")
    return {"indices": indices, "sectors": sectors, "institutions": institutions}


# ---------- 数据新鲜度判定（只影响调度，不参与任何信号计算） ----------

def _tail_date(path: Path, column: int) -> str | None:
    """廉价读文件尾部的日期：只取最后一行，完整文件按日期升序落盘。"""
    try:
        with path.open("rb") as handle:
            handle.seek(0, os.SEEK_END)
            handle.seek(max(handle.tell() - 256, 0))
            chunk = handle.read().decode("utf-8", "ignore")
    except OSError:
        return None
    lines = [line for line in chunk.splitlines() if line.strip()]
    if len(lines) < 2:  # 只剩被截断的半行时放弃，宁可判为未知触发刷新
        return None
    fields = lines[-1].split(",")
    if len(fields) <= column:
        return None
    value = fields[column].strip()[:10]
    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return value
    return None


def latest_local_dates() -> dict[str, str | None]:
    """各数据线本地最后日期：指数取最小（与 index-signals 口径一致）、板块取最大。"""
    index_ends = [_tail_date(path, 0) for path in _index_files().values()]
    sector_ends = [_tail_date(path, 0) for path in sorted(EM_SECTOR_RAW.glob("*.csv"))]
    cffex = sorted(CFFEX_RAW.glob("*.csv"))
    return {
        "indices": min(index_ends) if index_ends and all(index_ends) else None,
        "sectors": max(sector_ends) if sector_ends else None,
        "institutions": cffex[-1].stem if cffex else None,
    }


def data_gap(now: datetime | None = None) -> dict:
    """本地数据与最近已完成交易日的差距；`lag` 为 None 表示本地无该数据线。"""
    expected, basis = last_completed_trading_day(now)
    local = latest_local_dates()
    lag = {key: (lag_trading_days(value, expected) if value else None)
           for key, value in local.items()}
    return {"expected": expected, "basis": basis, "local": local, "lag": lag,
            "stale": sorted(key for key, value in lag.items() if value is None or value > 0)}


def _load_state() -> dict:
    if not REFRESH_STATE.exists():
        return {}
    try:
        payload = json.loads(REFRESH_STATE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return payload if isinstance(payload, dict) else {}


def _save_state(state: dict) -> None:
    REFRESH_STATE.parent.mkdir(parents=True, exist_ok=True)
    REFRESH_STATE.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _record_attempt(state: dict, day: str, key: str) -> None:
    """记录当日尝试次数（仅供日志）：只清除当日以外的记录。"""
    for other in [item for item in state if item != day]:
        state.pop(other)
    day_state = state.setdefault(day, {})
    tries = day_state.setdefault("tries", {})
    tries[key] = int(tries.get(key, 0)) + 1


def _mark_done(state: dict, day: str, key: str, expected: str) -> None:
    """记下“该类已补到 expected”；expected 变化（如跨过 15:00）时标记自动失效。"""
    day_state = state.setdefault(day, {})
    day_state.setdefault("done", {})[key] = expected


def _done_marks(state: dict, day: str) -> dict[str, str]:
    return state.get(day, {}).get("done", {}) or {}


def _attempt_count(state: dict, day: str, key: str) -> int:
    return int((state.get(day, {}).get("tries", {}) or {}).get(key, 0))


def update_etf(progress: Progress | None = None) -> dict:
    """ETF 日线增量补齐：独立数据线，失败只告警，且不参与新鲜度判定。

    首次全量由 `fetch_etf.fetch_etf_daily()` 显式执行；这里只做增量，
    未初始化时 fetch_etf 会直接返回，不会在启动阶段触发 6 只历史全量抓取。
    """
    progress = progress or Progress()
    try:
        from fetch_etf import update_etf_daily  # noqa: PLC0415

        result = update_etf_daily()
    except Exception as exc:  # ETF 失败不得阻断指数/板块/机构刷新
        detail = f"{type(exc).__name__}: {exc}"
        print(f"[bootstrap] ETF 日线更新失败（已跳过）：{detail}", flush=True)
        return {"status": "failed", "error": detail}
    if result.get("updated"):
        progress.update("etf", f"ETF {len(result['updated'])} 只有更新")
    return result


def ensure_fresh_data(progress: Progress | None = None, *, force: bool = False) -> dict:
    """启动时判断数据是否覆盖最近已完成交易日，落后就当场增量刷新。

    失败不计入当日次数：只要还没补到，下次调用（下次启动）仍会再试。
    三条主线的判定逻辑一行不改，全部保留在 `_ensure_fresh_data_core()`；ETF 日线作为
    并列的第四条线在其后 best-effort 增量（失败只告警，不参与新鲜度判定）。
    """
    result = _ensure_fresh_data_core(progress, force=force)
    etf = update_etf(progress)
    # 只在 ETF 真有更新/失败时并入，未初始化或全量跳过时不污染既有返回值
    if etf.get("updated") or etf.get("failed") or etf.get("status") == "failed":
        result.setdefault("refreshed", {})["etf"] = etf
    return result


def _ensure_fresh_data_core(progress: Progress | None = None, *, force: bool = False) -> dict:
    """启动时判断数据是否覆盖最近已完成交易日，落后就当场增量刷新。

    失败不计入当日次数：只要还没补到，下次调用（下次启动）仍会再试。
    """
    progress = progress or Progress()
    refresh_calendar()
    if ensure_raw_data(progress):  # 首次全量拉取刚完成，不再重复判定
        return {"bootstrap": True, "gap": data_gap(), "refreshed": {}}
    gap = data_gap()
    if not gap["stale"]:
        progress.update("ready", f"数据已覆盖最近交易日 {gap['expected']}")
        return {"bootstrap": False, "gap": gap, "refreshed": {}}

    day = datetime.now(CN).strftime("%Y-%m-%d")
    state = _load_state()
    done = _done_marks(state, day)
    targets = [key for key in gap["stale"]
               if force or done.get(key) != gap["expected"]]
    if not targets:
        detail = "、".join(f"{key} 已补到 {gap['expected']}" for key in gap["stale"])
        progress.update("ready", f"{detail}，本地数据与源发布进度一致（{day}）")
        return {"bootstrap": False, "gap": gap, "refreshed": {}, "skipped": gap["stale"]}

    detail = "、".join(f"{key}（本地 {gap['local'][key]}，应为 {gap['expected']}）" for key in targets)
    tries = "、".join(f"{key} 第 {_attempt_count(state, day, key) + 1} 次" for key in targets)
    progress.update("refresh", f"增量补齐：{detail}（{tries}）")
    refreshed: dict[str, object] = {}
    if "indices" in targets:
        refreshed["indices"] = update_indices(progress, force=force)
        _record_attempt(state, day, "indices")
    if "sectors" in targets:
        try:
            refreshed["sectors"] = update_sectors(progress)
        except Exception as exc:  # 板块线失败不拖累机构数据线，失败也计入当日尝试
            refreshed["sectors"] = 0
            print(f"[bootstrap] sectors 增量失败（不影响其余数据线）：{exc}", flush=True)
        _record_attempt(state, day, "sectors")
    if "institutions" in targets:
        refreshed["institutions"] = update_institutions(progress)
        _record_attempt(state, day, "institutions")

    after = data_gap()
    if not after["stale"]:
        for key in targets:
            _mark_done(state, day, key, gap["expected"])
    _save_state({day: state.get(day, {})})
    for key in targets:
        if after["lag"].get(key):
            print(f"[freshness] {key} 刷新后仍停在 {after['local'][key]}"
                  f"（应为 {after['expected']}，源尚未发布或拉取失败，下次启动会再试）", flush=True)
    return {"bootstrap": False, "gap": gap, "refreshed": refreshed, "after": after}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true",
                        help="只做新鲜度判定：数据已覆盖最近交易日则不拉取")
    parser.add_argument("--force", action="store_true", help="忽略“当日已补齐”标记，再拉一次")
    args = parser.parse_args()
    if args.check:
        print(json.dumps(ensure_fresh_data(force=args.force), ensure_ascii=False, default=str))
    else:
        print(json.dumps(refresh_daily(), ensure_ascii=False, default=str))
