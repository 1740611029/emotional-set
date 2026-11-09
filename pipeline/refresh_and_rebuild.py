"""先补原始日线，再重建前端 JSON；供「重建信号数据.bat」的补数据开关调用。

判定口径与服务启动时完全一致：只有本地日线落后于最近已完成交易日才做增量拉取。
没补到（源尚未发布或拉取失败）不会记为完成，下次运行还会再拉（`--force` 可强制）。
本模块只做编排与日志，不参与任何顶部/底部信号或板块趋势计算。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

PIPELINE = Path(__file__).resolve().parent
if str(PIPELINE) not in sys.path:
    sys.path.insert(0, str(PIPELINE))

import build_dashboard  # noqa: E402
from bootstrap import Progress, data_gap, ensure_fresh_data, update_etf  # noqa: E402

LABELS = {"indices": "指数", "sectors": "板块", "institutions": "机构"}


def describe(gap: dict) -> str:
    local = gap["local"]
    detail = "、".join(f"{LABELS[key]} {local.get(key) or '—'}" for key in LABELS)
    return f"最近已完成交易日 {gap['expected']}（口径 {gap['basis']}），本地 {detail}"


def report_after(gap: dict) -> None:
    """重建前明确说明是否仍落后，便于区分"源没发布"和"接口失败"。"""
    if not gap["stale"]:
        print(f"[rebuild] 数据已覆盖最近交易日 {gap['expected']}，重建完成")
        return
    for key in gap["stale"]:
        lag = gap["lag"].get(key)
        print(f"[rebuild] WARNING：{LABELS.get(key, key)} 仍停在 {gap['local'].get(key) or '—'}，"
              f"落后 {lag if lag is not None else '未知'} 个交易日（源尚未发布或拉取失败，"
              f"下次运行会自动再试）")


def describe_etf(etf: dict) -> str:
    """ETF 日线是并列的第四条线：单独一行描述有更新/跳过/失败。"""
    status = etf.get("status")
    if status == "failed":
        return f"失败（{etf.get('error') or '未知错误'}，已跳过）"
    if status == "uninitialized":
        return "尚未初始化，跳过"
    if status == "check_failed":
        return f"清单校验未通过，跳过（{(etf.get('failed') or [''])[0]}）"
    updated = len(etf.get("updated") or [])
    failed = len(etf.get("failed") or [])
    skipped = etf.get("skipped") or 0
    parts = [f"{updated} 只有更新", f"跳过 {skipped} 只"]
    if failed:
        parts.append(f"{failed} 只失败")
    return "、".join(parts)


def main(*, force: bool = False) -> int:
    progress = Progress()
    print(f"[rebuild] {describe(data_gap())}")
    result = ensure_fresh_data(progress, force=force)
    refreshed = result.get("refreshed") or {}
    # ETF 单独一行，三条主线的文案与分支保持原样
    etf = refreshed.get("etf") or update_etf(progress)
    core = {key: value for key, value in refreshed.items() if key != "etf"}
    if core:
        parts = []
        if "indices" in core:
            parts.append(f"指数 {len(core['indices'] or [])} 个有更新")
        if "sectors" in core:
            parts.append(f"板块 {core['sectors'] or 0} 个有更新")
        if "institutions" in core:
            parts.append(f"机构 {(core['institutions'] or {}).get('status', '-')}")
        print(f"[rebuild] 增量拉取：{'、'.join(parts)}")
    elif result.get("skipped"):
        detail = "、".join(f"{LABELS.get(key, key)} {result['gap']['local'].get(key) or '—'}"
                          for key in result["skipped"])
        print(f"[rebuild] 当日已标记补齐，跳过拉取：{detail}（加 --force 可强制再拉）")
    elif result.get("bootstrap"):
        print("[rebuild] 原始数据缺失，已完成首次全量拉取")
    else:
        print("[rebuild] 无落后数据，跳过拉取")
    # ensure_fresh_data 只在 ETF 真有更新/失败时并入 refreshed（保证其返回值语义不变），
    # 其余情况这里补一次台账核对（无待更新标的时不会触网），日志始终有 ETF 一行
    print(f"[rebuild] ETF 日线：{describe_etf(etf)}")

    # 无论是否拉取到新行情都重建：本开关的语义是"补数据 + 重建"
    build_dashboard.main()
    report_after(result.get("after") or data_gap())
    return 0


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="先补原始日线，再重建信号 JSON")
    parser.add_argument("--force", action="store_true", help="忽略“当日已补齐”标记，强制增量拉取")
    args = parser.parse_args()
    sys.exit(main(force=args.force))
