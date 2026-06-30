#!/usr/bin/env python3
"""手动生成 A股主线雷达静态站。"""

from __future__ import annotations

import argparse
import json
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from config import BOARD_ANALYSIS_LIMIT
from src.static_site import DEFAULT_OUTPUT_DIR, build_static_snapshot, clean_static_output

FAIL_MARKER = "❌ 未通过"


def _backup_output(output_dir: Path) -> Path | None:
    """生成前备份当前静态站，失败时恢复旧有效版本。"""
    if not output_dir.exists():
        return None
    backup_dir = output_dir.parent / f".{output_dir.name}_last_good_backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    shutil.copytree(output_dir, backup_dir)
    return backup_dir


def _restore_output(output_dir: Path, backup_dir: Path | None) -> None:
    """构建失败或自检失败时恢复备份。"""
    if output_dir.exists():
        shutil.rmtree(output_dir)
    if backup_dir and backup_dir.exists():
        shutil.copytree(backup_dir, output_dir)
        shutil.rmtree(backup_dir)


def _remove_backup(backup_dir: Path | None) -> None:
    """成功后删除临时备份。"""
    if backup_dir and backup_dir.exists():
        shutil.rmtree(backup_dir)


def _assert_self_check_passed(summary: dict) -> None:
    """自检失败时阻止发布，避免空数据覆盖线上有效页面。"""
    report_path = Path(str(summary.get("self_check_report", "")))
    if not report_path.exists():
        raise RuntimeError("自检报告不存在，停止发布。")
    report_text = report_path.read_text(encoding="utf-8", errors="ignore")
    if FAIL_MARKER in report_text:
        raise RuntimeError(f"自检未通过，已停止发布：{report_path}")


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
    backup_dir = _backup_output(output_dir) if args.clean else None
    try:
        if args.clean:
            clean_static_output(output_dir)
        summary = build_static_snapshot(
            output_dir=output_dir,
            report_date=args.date,
            max_boards=args.max_boards,
            include_concepts=not args.industry_only,
        )
        _assert_self_check_passed(summary)
    except Exception as exc:
        if args.clean:
            _restore_output(output_dir, backup_dir)
        print(f"静态站构建失败，已保留/恢复上次有效版本：{exc}", file=sys.stderr)
        raise SystemExit(2) from exc
    else:
        _remove_backup(backup_dir)
        print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
