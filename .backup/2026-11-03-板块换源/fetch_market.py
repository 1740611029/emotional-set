"""按固定数据源拉取指数及申万板块原始日线，不做信号计算。"""
from __future__ import annotations

import argparse
import json
import time
from datetime import date, timedelta
from pathlib import Path

import akshare as ak
import pandas as pd
import requests

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


def sector_list(level: str) -> pd.DataFrame:
    if level == "first":
        return call_with_retry(ak.sw_index_first_info)
    return call_with_retry(ak.sw_index_second_info)


def fetch_sectors() -> None:
    summary = []
    frames = {"first": sector_list("first"), "second": sector_list("second")}
    for level, info in frames.items():
        code_col = next((c for c in info.columns if "代码" in c), None)
        name_col = next((c for c in info.columns if "名称" in c), None)
        if not code_col or not name_col:
            raise ValueError(f"申万{level}列表缺少代码/名称字段：{info.columns.tolist()}")
        if level == "first" and len(info) != 31:
            raise ValueError(f"申万一级板块应为 31 个，实际 {len(info)} 个")
        for _, item in info.iterrows():
            code = str(item[code_col]).split(".")[0].zfill(6)
            name = str(item[name_col])
            path = RAW / "sectors" / level / f"{code}.csv"
            if path.exists():
                frame = pd.read_csv(path)
                print(f"使用已拉取申万{level} {code} {name}（{len(frame)} 行）")
            else:
                print(f"拉取申万{level} {code} {name}")
                frame = call_with_retry(ak.index_hist_sw, symbol=code, period="day")
                frame = frame.sort_values("日期").drop_duplicates("日期", keep="last")
                save_csv(frame, path)
            summary.append({"level": level, "code": code, "name": name,
                            "parent": str(item["上级行业"]) if level == "second" else None,
                            "rows": len(frame), "insufficient_history": len(frame) < 60,
                            "path": str(path.relative_to(ROOT))})
            if len(frame) < 60:
                print(f"  警告：{code} {name} 历史不足 60 个交易日，已标记")
            time.sleep(0.3)
    RAW.mkdir(parents=True, exist_ok=True)
    (RAW / "sectors-manifest.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"完成：一级 {sum(x['level'] == 'first' for x in summary)} 个，二级 {sum(x['level'] == 'second' for x in summary)} 个")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("target", choices=["indices", "sectors"])
    args = parser.parse_args()
    if args.target == "indices":
        fetch_indexes()
    else:
        fetch_sectors()
