#!/usr/bin/env python3
"""手动生成 A股主线雷达静态站。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import BOARD_ANALYSIS_LIMIT
from src.static_site import DEFAULT_OUTPUT_DIR, build_static_snapshot, clean_static_output


def main() -> None:
    """命令行入口。"""
    parser = argparse.ArgumentParser(description="生成 GitHub Pages 静态快照")
    parser.add_argument("--date", default=None, help="日报日期，默认今天，格式 YYYY-MM-DD")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_DIR), help="输出目录，默认 docs/")
    parser.add_argument("--max-boards", type=int, default=BOARD_ANALYSIS_LIMIT, help="扫描板块数量")
    parser.add_argument("--industry-only", action="store_true", help="只扫描行业板块，不扫描概念板块")
    parser.add_argument("--clean", action="store_true", help="生成前清理输出目录，保留 history/")
    args = parser.parse_args()

    output_dir = Path(args.output)
    if args.clean:
        clean_static_output(output_dir)
    summary = build_static_snapshot(
        output_dir=output_dir,
        report_date=args.date,
        max_boards=args.max_boards,
        include_concepts=not args.industry_only,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
