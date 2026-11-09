"""按固定数据源拉取指数及东财行业板块原始日线，不做信号计算。"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

from trading_calendar import last_completed_trading_day  # 全量拉取的收盘定稿口径

ROOT = Path(__file__).resolve().parents[1]
RAW = ROOT / "data" / "raw"
INDEXES = {
    "sh000001": "上证指数", "sz399001": "深证成指", "sz399006": "创业板指",
    "sh000688": "科创50", "sh000300": "沪深300", "sh000905": "中证500",
    "sh000852": "中证1000",
}
THS_CODES = {
    "sh000001": "zs_1A0001", "sz399001": "zs_399001", "sz399006": "zs_399006",
    "sh000688": "zs_1B0688", "sh000300": "zs_1B0300", "sh000905": "zs_1B0905",
    "sh000852": "zs_1B0852",
}
INDEX_RAW = RAW / "indices-ths"
EM_SECTOR_RAW = RAW / "sectors-em-industry"
EM_SECTOR_MANIFEST = RAW / "sectors-em-industry-manifest.json"
SECTOR_SOURCE = "东财行业板块"  # 数据源标识（产物与 manifest 共用）；不复权日 K 的口径由 fetch_sector_hist_em 保证
FIRST_FETCH_START = "20000101"
MIN_SECTOR_ROWS = 60
SECTOR_COLUMNS = ["date", "open", "close", "high", "low", "volume", "amount"]
EM_COLUMN_RENAME = {
    "日期": "date", "开盘": "open", "收盘": "close", "最高": "high",
    "最低": "low", "成交量": "volume", "成交额": "amount",
}
# 生产板块清单：BK 代码为唯一主键，名称只用于每日校验。
# 2026-11-03 与旧申万一级 31 个名称 31/31 对齐后固化；东财接口不返回层级字段，故不能动态生成。
EM_SECTOR_UNIVERSE: tuple[tuple[str, str], ...] = (
    ("BK0433", "农林牧渔"), ("BK1206", "基础化工"), ("BK0479", "钢铁"),
    ("BK0478", "有色金属"), ("BK1201", "电子"), ("BK1211", "汽车"),
    ("BK0456", "家用电器"), ("BK0438", "食品饮料"), ("BK0436", "纺织服饰"),
    ("BK1212", "轻工制造"), ("BK1216", "医药生物"), ("BK0427", "公用事业"),
    ("BK1210", "交通运输"), ("BK1202", "房地产"), ("BK1213", "商贸零售"),
    ("BK1214", "社会服务"), ("BK1283", "银行"), ("BK1203", "非银金融"),
    ("BK1217", "综合"), ("BK1208", "建筑材料"), ("BK1209", "建筑装饰"),
    ("BK1200", "电力设备"), ("BK1205", "机械设备"), ("BK1204", "国防军工"),
    ("BK1207", "计算机"), ("BK0486", "传媒"), ("BK1215", "通信"),
    ("BK0437", "煤炭"), ("BK0464", "石油石化"), ("BK0728", "环保"),
    ("BK1035", "美容护理"),
)


def call_with_retry(func, *, attempts: int = 4, **kwargs) -> pd.DataFrame:
    last_error = None
    for attempt in range(1, attempts + 1):
        try:
            frame = func(**kwargs)
            if frame is None or frame.empty:
                raise ValueError("接口返回空数据")
            return frame
        except Exception as exc:  # 网络接口需保留最后一次错误并退避重试
            last_error = exc
            print(f"  第 {attempt}/{attempts} 次调用失败：{exc}")
            if attempt < attempts:
                time.sleep(attempt * 2)
    raise RuntimeError(f"数据接口重试后仍失败：{last_error}") from last_error


def save_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False, encoding="utf-8-sig")


def fetch_ths_year(symbol: str, year: int) -> pd.DataFrame:
    url = f"https://d.10jqka.com.cn/v6/line/{symbol}/01/{year}.js"
    response = requests.get(url, headers={
        "User-Agent": "Mozilla/5.0", "Referer": "https://q.10jqka.com.cn/",
    }, timeout=30)
    response.raise_for_status()
    text = response.text
    payload = json.loads(text[text.index("(") + 1:text.rindex(")")])
    rows = []
    for record in payload["data"].split(";"):
        fields = record.split(",")
        if len(fields) >= 7 and len(fields[0]) == 8 and fields[0].isdigit():
            day = fields[0]
            rows.append([f"{day[:4]}-{day[4:6]}-{day[6:]}", *fields[1:7]])
    if not rows:
        raise ValueError(f"同花顺 {symbol} {year} 年返回空数据")
    frame = pd.DataFrame(rows, columns=["date", "open", "high", "low", "close", "volume", "amount"])
    for column in ["open", "high", "low", "close", "volume", "amount"]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = frame.dropna(subset=["open", "high", "low", "close"])
    return frame[["date", "open", "close", "high", "low", "volume", "amount"]]


def fetch_indexes() -> None:
    start = (date.today() - timedelta(days=365 * 6)).strftime("%Y-%m-%d")
    end = date.today().strftime("%Y-%m-%d")
    summary = []
    failures = []
    for code, name in INDEXES.items():
        path = INDEX_RAW / f"{code}.csv"
        try:
            if path.exists():
                cached = pd.read_csv(path).dropna(subset=["open", "high", "low", "close"]).reset_index(drop=True)
                if len(cached) >= 1250:
                    frame = cached
                    save_csv(frame, path)
                    print(f"使用已拉取 {code} {name}（{len(frame)} 行）")
                else:
                    raise ValueError(f"{code} 缓存仅 {len(cached)} 行，不足至少 5 年日线要求")
            else:
                print(f"拉取同花顺 {code} {name}")
                years = []
                for year in range(int(start[:4]), int(end[:4]) + 1):
                    years.append(call_with_retry(fetch_ths_year, symbol=THS_CODES[code], year=year))
                    time.sleep(0.2)
                frame = pd.concat(years, ignore_index=True)
                frame = frame.sort_values("date").drop_duplicates("date", keep="last")
                frame = frame[(frame["date"] >= start) & (frame["date"] <= end)].reset_index(drop=True)
                if len(frame) < 1250:
                    raise ValueError(f"{code} 仅 {len(frame)} 行，不足至少 5 年日线要求")
                save_csv(frame, path)
            row = {"code": code, "name": name, "source": "同花顺公开日线（按年份）",
                   "rows": len(frame), "start": str(frame.iloc[0]["date"]),
                   "end": str(frame.iloc[-1]["date"]), "path": str(path.relative_to(ROOT))}
            summary.append(row)
            print(f"  {row['rows']} 行，{row['start']} ~ {row['end']}")
        except Exception as exc:
            failures.append(code)
            print(f"警告：{code} {name} 拉取失败，继续处理其余指数：{exc}", flush=True)
        time.sleep(0.3)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "indices-ths-manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    if failures:
        raise RuntimeError(f"以下指数尚未拉取成功，请续跑：{', '.join(failures)}")


def sector_spot() -> dict[str, str]:
    """东财行业板块接口清单（板块代码 → 名称）。

    只用于每日校验固定清单；东财接口把 496 个一/二/三级板块平铺返回且不携带任何层级
    字段，因此生产清单不允许由该接口动态生成。
    """
    frame = call_with_retry(ak.stock_board_industry_name_em)
    code_col = next((c for c in frame.columns if "代码" in c), None)
    name_col = next((c for c in frame.columns if "名称" in c), None)
    if not code_col or not name_col:
        raise ValueError(f"东财行业板块清单缺少代码/名称字段：{frame.columns.tolist()}")
    return {str(row[code_col]): str(row[name_col]) for _, row in frame.iterrows()}


def check_sector_universe(spot: dict[str, str]) -> tuple[list[dict], dict]:
    """逐个校验硬编码 BK 代码仍存在且名称一致。

    名称不一致或代码查不到时直接报错：换板块等于换一套信号历史，不静默重新匹配。
    返回（生产清单条目，接口侧差异留痕）。
    """
    boards = []
    errors = []
    for code, name in EM_SECTOR_UNIVERSE:
        current = spot.get(code)
        if current is None:
            errors.append(f"{code}「{name}」在东财行业板块清单中已查不到")
        elif current != name:
            errors.append(f"{code} 固化名称「{name}」与接口名称「{current}」不一致")
        else:
            boards.append({"code": code, "name": name})
    if errors:
        raise ValueError(f"板块清单校验失败，保留上一版数据：{'；'.join(errors)}")
    extra = sorted(set(spot) - {code for code, _ in EM_SECTOR_UNIVERSE})
    snapshot = {"接口板块总数": len(spot), "生产清单板块数": len(boards), "未入列板块数": len(extra)}
    return boards, snapshot


def sector_request_start(tail: str | None) -> str:
    """增量起点：本地尾日当日（含）；无缓存时全量回溯。

    起点含尾日是自愈设计：每轮都重取最后一根 bar，配合 merge 的 `keep="last"`
    与逐值比对覆盖掉收盘瞬间可能未定稿的量额（东财当日日线在收盘刚过才陆续定稿）。
    """
    if not tail:
        return FIRST_FETCH_START
    return pd.Timestamp(tail).strftime("%Y%m%d")


def fetch_sector_hist_em(symbol: str, start_date: str, end_date: str) -> pd.DataFrame:
    """单个东财行业板块日线（不复权）；列名统一为既有价格口径。

    symbol 传 BK 代码：akshare 对 `^BK` 开头的入参直接用代码请求，不再按名称查表。
    """
    frame = call_with_retry(
        ak.stock_board_industry_hist_em, symbol=symbol, start_date=start_date,
        end_date=end_date, period="日k", adjust="",
    )
    frame = frame.rename(columns=EM_COLUMN_RENAME)
    missing = [column for column in SECTOR_COLUMNS if column not in frame.columns]
    if missing:
        raise ValueError(f"{symbol} 日线缺少字段：{missing}")
    frame = frame[SECTOR_COLUMNS].copy()
    frame["date"] = frame["date"].astype(str).str.slice(0, 10)
    for column in SECTOR_COLUMNS[1:]:
        frame[column] = pd.to_numeric(frame[column], errors="raise")
    frame = (frame.dropna(subset=SECTOR_COLUMNS[1:])
             .sort_values("date").drop_duplicates("date", keep="last")
             .reset_index(drop=True))
    if frame.empty:
        raise ValueError(f"{symbol} 在 {start_date}~{end_date} 区间无日线")
    return frame


def fetch_sectors(end_date: str | None = None) -> None:
    """首次全量拉取 31 个东财行业板块日线并生成生产 manifest。

    默认终点取交易日历给出的最近已完成交易日（与 `update_sectors()` 同一口径），
    避免盘中初始化时把当日未完成 bar 写入缓存；回填演练可显式传 `end_date`。
    """
    end = end_date or last_completed_trading_day()[0].replace("-", "")
    spot = sector_spot()
    boards, snapshot = check_sector_universe(spot)
    rows = []
    for board in boards:
        code, name = board["code"], board["name"]
        path = EM_SECTOR_RAW / f"{code}.csv"
        if path.exists():
            frame = pd.read_csv(path, dtype={"date": str})
            print(f"使用已拉取 {code} {name}（{len(frame)} 行）")
        else:
            print(f"拉取 {code} {name} 全量日线")
            frame = fetch_sector_hist_em(code, FIRST_FETCH_START, end)
            save_csv(frame, path)
        rows.append({
            "code": code, "name": name, "source": SECTOR_SOURCE,
            "rows": len(frame), "start": str(frame["date"].iat[0]),
            "end": str(frame["date"].iat[-1]),
            "insufficient_history": len(frame) < MIN_SECTOR_ROWS,
            "path": str(path.relative_to(ROOT)),
        })
        if len(frame) < MIN_SECTOR_ROWS:
            print(f"  警告：{code} {name} 历史不足 {MIN_SECTOR_ROWS} 个交易日，已标记")
        time.sleep(0.2)
    EM_SECTOR_RAW.mkdir(parents=True, exist_ok=True)
    EM_SECTOR_MANIFEST.write_text(json.dumps({
        "source": SECTOR_SOURCE,
        "universe": [{"code": code, "name": name} for code, name in EM_SECTOR_UNIVERSE],
        "universe_snapshot": snapshot,
        "universe_changes": [],
        "boards": rows,
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成：{len(rows)} 个东财行业板块，历史不足 {sum(r['insufficient_history'] for r in rows)} 个")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["indices", "sectors"])
    args = parser.parse_args()
    if args.target == "indices":
        fetch_indexes()
    else:
        fetch_sectors()
