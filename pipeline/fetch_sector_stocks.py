"""板块成分股快照管线（【板块趋势】页个股排序数据）。

只读展示层：抓取同花顺行业成分股快照表并落盘 sector-stocks.json，
不参与任何信号计算（AGENTS 第 1 条）。板块清单与板块日线同源：
manifest `universe`（90 个 881xxx 行业，2026-11-14 换源后的同花顺口径）。

抓取路径（2026-11-16 实测验证）：
- 非 ajax 整页 http://q.10jqka.com.cn/thshy/detail/code/{code}/page/{n}/
  （ajax/1 路径 401/403、带 v cookie 翻页 403，均不可用；本页仅带普通浏览器 UA 即可 200）
- 表格 <table class="m-table m-pager-table">，每页 20 只；超范围页码返回
  chameleon + upass 登录跳转插页（192 字节，反爬信号），因此翻页遇插页做退避重试，
  仍失败则记录告警并停止该板块，不因单板块风控中断全量。
- 页面为 GBK 编码，中文单位（万/亿）需转数值后再参与排序与热度计算。

热度算法（用户 2026-11-16 拍板）：
  换手率 / 量比 / 成交额（元）三指标各自板块内 min-max 归一化到 [0,1]，
  等权平均后 ×100 → 0-100；三指标全缺失（停牌）时 heat=null，前端排序置尾。
"""

from __future__ import annotations

import argparse
import datetime as dt
import html as html_mod
import json
import re
import time
from pathlib import Path

import requests

from io_utils import protected_write
from rate_limiter import BATCH_PAUSE, BATCH_SIZE, LIMITER, REQUEST_GAP

ROOT = Path(__file__).resolve().parents[1]
THS_MANIFEST = ROOT / "data" / "raw" / "sectors-ths-industry-manifest.json"

DETAIL_URL = "http://q.10jqka.com.cn/thshy/detail/code/{code}/page/{page}/"
PAGER_TABLE_RE = re.compile(
    r'<table[^>]*class="[^"]*m-pager-table[^"]*"[^>]*>(.*?)</table>', re.S
)
TR_RE = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S)
TD_RE = re.compile(r"<t[dh][^>]*>(.*?)</t[dh]>", re.S)
TAG_RE = re.compile(r"<[^>]+>")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    )
}

HEAT_COLUMNS = ("amount", "turnover_pct", "volume_ratio")

MAX_PAGES_PER_BOARD = 50  # 单板块最多 50 页（1000 只），远超实际
RETRY_TIMES = 3  # 首页反爬插页退避重试次数
TAIL_RETRY_TIMES = 1  # 非首页插页重试 1 次即按页面上限处理
RETRY_WAIT = 5.0  # 退避重试间隔（秒）
MAX_CONSECUTIVE_BLOCKED = 3  # 连续多少个板块首页被拦则终止全量


def parse_num(text: str) -> float | None:
    """解析同花顺单元格数值；中文单位换算，`--`/`-`/空返回 None。"""
    s = (text or "").strip().replace(",", "")
    if not s or s in ("-", "--"):
        return None
    try:
        if s.endswith("亿"):
            return round(float(s[:-1]) * 1e8, 2)
        if s.endswith("万"):
            return round(float(s[:-1]) * 1e4, 2)
        return float(s)
    except ValueError:
        return None


def clean(cell: str) -> str:
    return html_mod.unescape(TAG_RE.sub("", cell)).strip()


def fetch_page(code: str, page: int, session: requests.Session) -> str:
    """按统一限速器取令牌后抓取单页；冷却期直接抛错让整条管线中止（不落残缺盘）。"""
    if not LIMITER.acquire("ths", line="sector-stocks"):
        raise RuntimeError(
            f"同花顺限流冷却中，跳过板块 {code} 成分股请求（统一限速器）"
        )
    url = DETAIL_URL.format(code=code, page=page)
    resp = session.get(url, headers=HEADERS, timeout=30)
    return resp.content.decode("gbk", errors="replace")


def parse_table(text: str) -> list[list[str]] | None:
    """返回表格全部行；无 m-pager-table（反爬插页 / 空页）返回 None。"""
    match = PAGER_TABLE_RE.search(text)
    if not match:
        return None
    rows = []
    for row in TR_RE.findall(match.group(1)):
        cells = [clean(cell) for cell in TD_RE.findall(row)]
        rows.append(cells)
    return rows


def parse_stock_rows(rows: list[list[str]]) -> list[dict]:
    """表行 → 成分股记录；跳过表头与非 6 位代码行。"""
    stocks = []
    for cells in rows:
        if len(cells) < 14:
            continue
        code, name = cells[1], cells[2]
        if not (code and code.isdigit() and len(code) == 6):
            continue
        amount_text = cells[10]
        stocks.append(
            {
                "code": code,
                "name": name,
                "price": parse_num(cells[3]),
                "change_pct": parse_num(cells[4]),
                "change": parse_num(cells[5]),
                "speed_pct": parse_num(cells[6]),
                "turnover_pct": parse_num(cells[7]),
                "volume_ratio": parse_num(cells[8]),
                "amplitude_pct": parse_num(cells[9]),
                "amount": parse_num(amount_text),
                "amount_text": amount_text,
                "float_shares": parse_num(cells[11]),
                "float_shares_text": cells[11],
                "float_mv": parse_num(cells[12]),
                "float_mv_text": cells[12],
                "pe": parse_num(cells[13]),
            }
        )
    return stocks


def fetch_board(
    code: str, name: str, session: requests.Session
) -> tuple[list[dict], int, bool, bool]:
    """翻页抓取单个板块成分股（同花顺详情页最多渲染前 100 只）。

    返回（stocks, 成功页数, 首页是否被拦, 是否超 100 只上限被截断）。
    - 非首页遇插页：视为同花顺页面上限（100 只），正常结束不报错；
    - 首页遇插页：视为数据源异常/风控，由调用方连续计数终止全量。
    """
    stocks: list[dict] = []
    page = 1
    first_page_blocked = False
    truncated = False
    seen_codes: set[str] = set()
    while page <= MAX_PAGES_PER_BOARD:
        rows = parse_table(fetch_page(code, page, session))
        if rows is None:  # 反爬插页 / 空页：退避重试
            got = None
            retry_times = RETRY_TIMES if page == 1 else TAIL_RETRY_TIMES
            for attempt in range(retry_times):
                if attempt:
                    print(
                        f"  {code} 第 {page} 页无表格，{RETRY_WAIT:.0f}s 后退避重试 "
                        f"({attempt + 1}/{retry_times})",
                        flush=True,
                    )
                    time.sleep(RETRY_WAIT)
                got = parse_table(fetch_page(code, page, session))
                if got is not None:
                    break
            if got is None:
                if page == 1:
                    first_page_blocked = True
                else:
                    truncated = True
                break
            rows = got
        data_rows = [row for row in rows if row and row[0].isdigit()]
        if not data_rows:
            if page == 1:
                first_page_blocked = True
            else:
                truncated = True
            break
        # 逐页快照存在翻页漂移：边界个股可能同时出现在上下两页，按代码去重（保留首次出现）
        for stock in parse_stock_rows(data_rows):
            if stock["code"] in seen_codes:
                continue
            seen_codes.add(stock["code"])
            stocks.append(stock)
        if len(data_rows) < 20:  # 尾页不足 20 只
            break
        page += 1
        time.sleep(REQUEST_GAP)
    if first_page_blocked or truncated:
        page = max(page - 1, 0)
    return stocks, page, first_page_blocked, truncated


def compute_heat(stocks: list[dict]) -> None:
    """板块内热度：三指标各自 min-max 归一化后等权平均 ×100；全缺失置 null。"""
    values = {column: [] for column in HEAT_COLUMNS}
    for stock in stocks:
        for column in HEAT_COLUMNS:
            value = stock.get(column)
            values[column].append(value if isinstance(value, (int, float)) else None)
    spans = {}
    for column in HEAT_COLUMNS:
        available = [v for v in values[column] if v is not None]
        if not available:
            spans[column] = (0.0, 0.0)
        else:
            spans[column] = (min(available), max(available))
    for i, stock in enumerate(stocks):
        normalized = []
        for column in HEAT_COLUMNS:
            value = values[column][i]
            if value is None:
                continue
            low, high = spans[column]
            span = high - low
            normalized.append(0.0 if span == 0 else (value - low) / span)
        if not normalized:
            stock["heat"] = None
        else:
            stock["heat"] = round(sum(normalized) / len(normalized) * 100, 2)


def collect_stocks(boards_meta: list[dict]) -> tuple[list[dict], int]:
    """抓取全部板块成分股并计算热度；返回 (boards, total_stocks)。

    供 CLI 与刷新链共用；写盘由调用方决定（调试模式不落盘）。
    """
    session = requests.Session()
    today = dt.date.today().isoformat()
    boards: list[dict] = []
    total_stocks = 0
    for index, meta in enumerate(boards_meta, 1):
        code, board_name = meta["code"], meta["name"]
        stocks, pages, first_page_blocked, truncated = fetch_board(
            code, board_name, session
        )
        if first_page_blocked:
            raise RuntimeError(
                f"{code} {board_name} 首页被同花顺反爬拦截，"
                "数据源异常，终止并保留上一版数据"
            )
        compute_heat(stocks)
        boards.append(
            {
                "code": code,
                "name": board_name,
                "as_of": today,
                "page_count": pages,
                "total": len(stocks),
                "stocks": stocks,
            }
        )
        total_stocks += len(stocks)
        if truncated:
            print(
                f"  提示：{code} {board_name} 超过同花顺详情页 100 只上限，取前 100 只",
                flush=True,
            )
        print(
            f"[{index}/{len(boards_meta)}] {code} {board_name}: "
            f"{pages} 页 {len(stocks)} 只",
            flush=True,
        )
        if index % BATCH_SIZE == 0 and index < len(boards_meta):
            print(f"  批间休息 {BATCH_PAUSE:.0f}s（避免触发同花顺风控）", flush=True)
            time.sleep(BATCH_PAUSE)
    return boards, total_stocks


def build_payload(boards_meta: list[dict] | None = None) -> dict:
    """全量抓取并构造 sector-stocks.json 落盘 payload（不写盘）。

    刷新链入口（无参调用）：自动读取 manifest 板块清单；
    调用方拿到 payload 后自行 protected_write，保证失败不落残缺盘。
    """
    if boards_meta is None:
        boards_meta = _load_boards_meta()
    boards, _ = collect_stocks(boards_meta)
    return {
        "as_of": dt.date.today().isoformat(),
        "generated_at": dt.datetime.now().isoformat(timespec="seconds"),
        "source": "同花顺行业成分股快照（m-pager-table 非 ajax 翻页）",
        "heat_algorithm": "换手率/量比/成交额等权各 1/3，板块内 min-max 归一化 0-100",
        "boards": boards,
    }


def _load_boards_meta() -> list[dict]:
    """读取 manifest 板块清单；为空抛错，由调用方决定终止策略。"""
    manifest = json.loads(THS_MANIFEST.read_text(encoding="utf-8"))
    boards_meta = manifest.get("universe") or []
    if not boards_meta:
        raise RuntimeError("manifest universe 为空，拒绝生成成分股产物")
    return boards_meta


def main() -> None:
    parser = argparse.ArgumentParser(
        description="抓取同花顺行业成分股快照并生成 sector-stocks.json"
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=0,
        help="仅处理前 N 个板块（0=全量）；调试模式不写盘",
    )
    parser.add_argument(
        "--boards",
        nargs="*",
        help="只处理指定板块代码（调试用，不写盘）",
    )
    args = parser.parse_args()

    boards_meta = _load_boards_meta()
    if args.limit:
        boards_meta = boards_meta[: args.limit]
    elif args.boards:
        codes = set(args.boards)
        boards_meta = [board for board in boards_meta if board["code"] in codes]
    if not boards_meta:
        raise SystemExit("没有匹配的板块（--limit / --boards 为空）")

    if args.limit or args.boards:
        boards, total_stocks = collect_stocks(boards_meta)
        print(f"调试模式：{len(boards)} 个板块 {total_stocks} 只成分股（未写盘）")
        for board in boards[:3]:
            sample = board["stocks"][0] if board["stocks"] else None
            print(
                json.dumps(
                    {
                        "code": board["code"],
                        "name": board["name"],
                        "total": board["total"],
                        "page_count": board["page_count"],
                        "sample": sample,
                    },
                    ensure_ascii=False,
                )
            )
        return

    payload = build_payload(boards_meta)
    protected_write("sector-stocks.json", payload)
    total = sum(board["total"] for board in payload["boards"])
    print(f"完成：{len(payload['boards'])} 个板块 {total} 只成分股")


if __name__ == "__main__":
    main()
