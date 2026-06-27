"""校验静态快照里的价格口径、股票池去重和分组约束。"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


BAD_RESEARCH_STATUSES = {"高位过热", "等待回调", "不适合追", "趋势破坏"}
WATCH_GROUP = "高位观察/不适合追"
RESEARCH_GROUP = "可研究候选"


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser()
    parser.add_argument("snapshot", nargs="?", default="docs/data/latest.json")
    args = parser.parse_args()
    data = json.loads(Path(args.snapshot).read_text(encoding="utf-8"))
    leaders = data.get("leaders") or []
    errors: list[str] = []

    codes = [row.get("code") for row in leaders if row.get("code")]
    duplicate_codes = sorted({code for code in codes if codes.count(code) > 1})
    if duplicate_codes:
        errors.append(f"股票池存在重复代码: {duplicate_codes[:20]}")

    for row in leaders:
        code = row.get("code", "")
        price = _num(row.get("price"))
        close = _num(row.get("close"))
        current_price = _num(row.get("current_price"))
        diff_pct = _num(row.get("price_check_diff_pct"))
        distance_ma20 = _num(row.get("distance_ma20_pct"))
        group = str(row.get("pool_group", ""))
        observe_status = str(row.get("observe_status", ""))

        if row.get("price_basis") != "不复权":
            errors.append(f"{code} 价格口径不是不复权: {row.get('price_basis')}")
        if close > 0 and abs(price - close) > 0.000001:
            errors.append(f"{code} 展示价格不等于不复权 close: price={price}, close={close}")
        if current_price > 0 and close > 0 and abs(diff_pct) > 3 and row.get("price_check_status") != "价格校验异常":
            errors.append(f"{code} 偏差超过 3% 但未标记异常: {diff_pct:.2f}%")
        if group == RESEARCH_GROUP and observe_status in BAD_RESEARCH_STATUSES:
            errors.append(f"{code} {observe_status} 混入可研究候选")
        if group == RESEARCH_GROUP and distance_ma20 > 25:
            errors.append(f"{code} 距 MA20 超过 25% 仍在可研究候选: {distance_ma20:.2f}%")
        if distance_ma20 > 35 and group != WATCH_GROUP:
            errors.append(f"{code} 距 MA20 超过 35% 未进入观察组: {distance_ma20:.2f}%")

    if errors:
        raise SystemExit("\n".join(errors))

    research_count = sum(1 for row in leaders if row.get("pool_group") == RESEARCH_GROUP)
    watch_count = len(leaders) - research_count
    print(
        f"snapshot ok: leaders={len(leaders)}, research={research_count}, watch={watch_count}, "
        f"report_date={data.get('report_date')}"
    )


def _num(value: Any) -> float:
    """安全转成 float。"""
    try:
        return float(value or 0)
    except Exception:
        return 0.0


if __name__ == "__main__":
    main()
