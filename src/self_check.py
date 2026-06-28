"""静态站构建后的自检系统。"""

from __future__ import annotations

import html
import json
from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils import safe_float


PASS = "✅ 已通过"
WARN = "⚠️ 需要人工确认"
FAIL = "❌ 未通过"
EMOTION_KEYWORDS = ("昨日", "涨停", "跌停", "连板", "打板", "炸板", "晋级", "高标", "情绪")


def run_self_check(snapshot: dict[str, Any], output_dir: Path | str) -> Path:
    """运行构建后自检并生成 Markdown/HTML 报告。

    自检以最终产物为准：优先读取 output_dir/data/latest.json，并扫描
    output_dir/index.html，避免中间 DataFrame 正确但最终页面/JSON 失真的情况。
    """
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    final_snapshot = _load_final_snapshot(output_path, snapshot)
    checks = {
        "数据完整性": _check_data_integrity(final_snapshot),
        "页面完整性": _check_page_integrity(final_snapshot, output_path),
        "逻辑完整性": _check_logic_integrity(final_snapshot, output_path),
    }
    report_path = output_path / "self_check_report.md"
    generated_at = str(final_snapshot.get("generated_at", datetime.now().isoformat(timespec="seconds")))
    report_path.write_text(_clean_text(_render_report(checks, generated_at)), encoding="utf-8")
    html_path = output_path / "self_check_report.html"
    html_path.write_text(_clean_text(_render_html_report(checks, generated_at)), encoding="utf-8")
    return report_path


def _check_data_integrity(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """检查数据覆盖、日期和关键字段。"""
    metrics = snapshot.get("market_temperature", {}).get("metrics", {})
    basis = snapshot.get("data_basis", {})
    sample_count = int(metrics.get("sample_count") or metrics.get("total") or 0)
    board_count = int(snapshot.get("board_universe_count") or 0)
    data_date = str(basis.get("data_date") or snapshot.get("report_date") or "")
    rows = [
        _result(sample_count > 5000, "股票池数量 > 5000", f"本次参与统计 {sample_count} 只股票。", "若低于 5000，检查全市场行情接口是否只返回部分样本。"),
        _result(board_count > 50, "行业/概念数量 > 50", f"原始行业/概念数量 {board_count}。", "若低于 50，检查行业板块和概念板块接口。"),
        _date_result(data_date),
        _result(bool(snapshot.get("sectors")), "主线表非空", f"输出主线 {len(snapshot.get('sectors', []))} 条。", "检查板块行情、板块历史和评分链路。"),
        _result(bool(snapshot.get("leaders")), "龙头股票池非空", f"输出股票 {len(snapshot.get('leaders', []))} 只。", "检查成分股接口和个股历史 K 线接口。"),
        _result(bool(snapshot.get("operating_summary", {}).get("one_liner")), "今日一句话非空", "操作摘要已生成。", "检查 operating_system.generate_one_liner。"),
        _result(
            bool(snapshot.get("operating_summary", {}).get("history_snapshot", {}).get("saved")),
            "真实历史快照已保存",
            str(snapshot.get("operating_summary", {}).get("history_snapshot", {}).get("database", "data/radar_history.db")),
            "检查 src.history_db.save_radar_history_snapshot 是否正常写入。",
        ),
    ]
    return rows


def _check_page_integrity(snapshot: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
    """检查关键页面模块是否存在。"""
    index = _read_text(output_dir / "index.html")
    lifecycle = _read_text(output_dir / "lifecycle.html")
    daily = _read_text(output_dir / "daily.html")
    v3 = _read_text(output_dir / "v3.html")
    generated_at = str(snapshot.get("generated_at", ""))
    latest = _read_text(output_dir / "data" / "latest.json")
    return [
        _result("今日一句话" in index, "首页有今日一句话", "", "检查 render_index_page。"),
        _result("历史快照已保存" in index, "首页显示历史快照已保存", "", "检查 render_index_page 历史快照状态模块。"),
        _result("今日 Action" in index, "首页有今日 Action", "", "检查 render_index_page。"),
        _result("今日变化" in index, "首页有今日变化", "", "检查 render_index_page。"),
        _result("机会分" in lifecycle and "风险分" in lifecycle, "生命周期页有机会分/风险分", "", "检查 render_lifecycle_page。"),
        _result("进度" not in lifecycle, "生命周期页没有“进度”字段", "", "移除生命周期页 lifecycle_progress 展示，避免退潮期 100/100 误导。"),
        _result("3 分钟摘要" in daily or "3分钟摘要" in daily, "日报页有 3 分钟摘要", "", "在日报开头保留简短摘要说明。"),
        _result(
            bool(generated_at) and generated_at in index and generated_at in daily and generated_at in latest,
            "首页/日报/latest.json 生成时间一致",
            f"生成时间：{generated_at}",
            "检查 build_static_snapshot 是否先生成同一个 snapshot，再写 latest.json/index.html/daily.html。",
        ),
        _result("近似回放" in v3 and "成分幸存者偏差" in v3, "V3 页面标注近似回放限制", "", "检查 render_v3_page。"),
    ]


def _check_logic_integrity(snapshot: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
    """检查关键业务约束。"""
    ops = snapshot.get("operating_summary", {})
    groups = ops.get("stock_groups", {})
    research = groups.get("可研究候选", []) or []
    all_group_rows = []
    for rows in groups.values():
        all_group_rows.extend(rows or [])
    sectors = snapshot.get("sectors", []) or []
    action_by_sector = {
        str(row.get("board_name", "")).strip(): str(row.get("action", "")).strip()
        for row in sectors
        if str(row.get("board_name", "")).strip()
    }

    bad_research = [
        row
        for row in research
        if str(row.get("observe_status", "")) in {"高位过热", "趋势破坏", "不适合追", "等待回调"}
        or safe_float(row.get("distance_ma20_pct")) > 25
        or "退潮期" in str(row.get("matched_lifecycle", ""))
    ]
    bad_research_parent = [
        row
        for row in research
        if str(row.get("matched_action", "")).strip() != "重点研究"
    ]
    bad_research_display_action = [
        row
        for row in research
        if not _candidate_display_actions_are_focus(row, action_by_sector)
    ]
    bad_focus = [
        row
        for row in sectors
        if row.get("action") == "重点研究"
        and (row.get("lifecycle_state") == "退潮期" or safe_float(row.get("risk_score")) >= 72)
    ]
    emotion_in_main = [
        row
        for row in sectors
        if any(keyword in str(row.get("board_name", "")) for keyword in EMOTION_KEYWORDS)
    ]
    codes = [str(row.get("code", "")) for row in all_group_rows if row.get("code")]
    duplicate_codes = sorted({code for code in codes if codes.count(code) > 1})
    proxy_labels_ok = _proxy_label_ok(sectors, snapshot)
    history_available = bool(ops.get("history_available"))
    history_message_ok = history_available or "暂无昨日数据" in str(ops.get("changes", {}).get("message", ""))
    index = _read_text(output_dir / "index.html")
    index_candidate_section = _index_stock_group_section(index, "可研究候选", "强主线回调观察")
    index_candidate_count = _index_stock_count(index_candidate_section)
    bad_index_candidates = _bad_index_candidate_rows(index_candidate_section, all_group_rows, action_by_sector)

    return [
        _result(not bad_research, "高位/退潮股票未进入可研究候选", f"异常 {len(bad_research)} 只。", "检查 build_stock_groups 分组条件。"),
        _result(
            not bad_research_parent,
            "可研究候选所属主线 Action 必须等于重点研究",
            f"异常 {len(bad_research_parent)} 只。",
            "检查 build_stock_groups，个股不能越过父级主线 Action 单独升级。",
        ),
        _result(
            not bad_research_display_action,
            "可研究候选展示主线 Action 必须全部为重点研究",
            _bad_display_action_detail(bad_research_display_action, action_by_sector),
            "检查最终 latest.json/index.html：可研究候选不能展示等回调、只观察/不追或回避主线。",
        ),
        _result(
            not bad_index_candidates,
            "首页可研究候选不得出现非重点研究主线股票",
            _bad_index_candidate_detail(bad_index_candidates, action_by_sector),
            "重新生成 docs/index.html，确保首页股票池来自最新 latest.json。",
        ),
        _result(
            index_candidate_count == len(research),
            "首页可研究候选数量等于 latest.json",
            f"index.html={index_candidate_count}，latest.json={len(research)}。",
            "检查 render_index_page 是否用最新 operating_summary.stock_groups 渲染。",
        ),
        _result(not bad_focus, "退潮期主线未进入重点研究", f"异常 {len(bad_focus)} 条。", "检查 determine_action 风险阈值。"),
        _result(not emotion_in_main, "短线情绪标签未进入主线排名", f"异常 {len(emotion_in_main)} 条。", "检查 sector_radar._split_concept_and_emotion。"),
        _result(not duplicate_codes, "股票池按代码去重", f"重复代码：{', '.join(duplicate_codes[:10]) if duplicate_codes else '无'}", "检查 build_stock_groups 去重逻辑。"),
        _result(proxy_labels_ok, "资金不可用时标注成交活跃度代理", "", "检查 flow_score_label 和 data_basis.fund_basis。"),
        _result(history_message_ok, "无历史数据时明确提示", "", "检查 build_today_changes。"),
    ]


def _proxy_label_ok(sectors: list[dict[str, Any]], snapshot: dict[str, Any]) -> bool:
    """确认真实资金流不可用时没有写成资金流入。"""
    if not sectors:
        return True
    real_available = any(bool(row.get("real_flow_available")) for row in sectors)
    if real_available:
        return True
    fund_basis = str(snapshot.get("data_basis", {}).get("fund_basis", ""))
    labels = {str(row.get("flow_score_label", "")) for row in sectors}
    return "成交活跃度代理" in fund_basis and any("成交活跃度代理" in label for label in labels)


def _candidate_display_actions_are_focus(row: dict[str, Any], action_by_sector: dict[str, str]) -> bool:
    """最终 JSON 中可研究候选展示出来的主线 Action 必须全部是重点研究。"""
    names = _display_sector_names(row)
    actions = [action_by_sector.get(name, "") for name in names]
    return bool(actions) and all(action == "重点研究" for action in actions)


def _display_sector_names(row: dict[str, Any]) -> list[str]:
    """从最终 JSON 的展示主线字段拆出主线名称。"""
    text = str(row.get("board_name", ""))
    return [item.strip() for item in text.split("/") if item.strip()]


def _bad_display_action_detail(rows: list[dict[str, Any]], action_by_sector: dict[str, str]) -> str:
    """展示主线 Action 错误详情。"""
    if not rows:
        return "异常 0 只。"
    items = []
    for row in rows[:8]:
        names = _display_sector_names(row)
        action_text = " / ".join(f"{name}:{action_by_sector.get(name, '未知')}" for name in names)
        items.append(f"{row.get('code', '')}{row.get('name', '')}({action_text})")
    return "异常 " + str(len(rows)) + " 只：" + "；".join(items)


def _index_stock_group_section(index_html: str, start_title: str, next_title: str) -> str:
    """截取最终首页某个股票池分组的 HTML。"""
    start_marker = f"<h3>{start_title}</h3>"
    next_marker = f"<h3>{next_title}</h3>"
    start = index_html.find(start_marker)
    if start < 0:
        return ""
    end = index_html.find(next_marker, start + len(start_marker))
    return index_html[start:end if end >= 0 else len(index_html)]


def _index_stock_count(section_html: str) -> int:
    """统计首页股票池分组中真实股票条数。"""
    if not section_html or "当前没有符合条件的股票" in section_html:
        return 0
    return section_html.count("<li>")


def _bad_index_candidate_rows(
    section_html: str,
    rows: list[dict[str, Any]],
    action_by_sector: dict[str, str],
) -> list[dict[str, Any]]:
    """找出首页可研究候选段落中出现的非重点研究主线股票。"""
    bad_rows = []
    if not section_html:
        return rows
    for row in rows:
        code = str(row.get("code", ""))
        name = str(row.get("name", ""))
        if not code and not name:
            continue
        if code not in section_html and name not in section_html:
            continue
        if not _candidate_display_actions_are_focus(row, action_by_sector):
            bad_rows.append(row)
    return bad_rows


def _bad_index_candidate_detail(rows: list[dict[str, Any]], action_by_sector: dict[str, str]) -> str:
    """首页候选段落错误详情。"""
    if not rows:
        return "异常 0 只。"
    items = []
    for row in rows[:8]:
        names = _display_sector_names(row)
        action_text = " / ".join(f"{name}:{action_by_sector.get(name, '未知')}" for name in names)
        items.append(f"{row.get('code', '')}{row.get('name', '')}({action_text})")
    return "异常 " + str(len(rows)) + " 只：" + "；".join(items)


def _date_result(data_date: str) -> dict[str, str]:
    """检查行情日期是否接近当前日期。"""
    try:
        parsed = datetime.fromisoformat(data_date[:10])
        days = (datetime.now() - parsed).days
    except Exception:
        return {"status": WARN, "item": "当日行情日期可解析", "detail": f"数据日期：{data_date}", "suggestion": "检查 data_basis.data_date。"}
    if days <= 5:
        return {"status": PASS, "item": "当日行情日期为最近交易日", "detail": f"数据日期：{data_date}，距今 {days} 天。", "suggestion": ""}
    if days <= 10:
        return {"status": WARN, "item": "当日行情日期为最近交易日", "detail": f"数据日期：{data_date}，距今 {days} 天。", "suggestion": "遇到长假可人工确认，否则检查行情接口。"}
    return {"status": FAIL, "item": "当日行情日期为最近交易日", "detail": f"数据日期：{data_date}，距今 {days} 天。", "suggestion": "重新拉取行情或检查接口缓存。"}


def _result(ok: bool, item: str, detail: str, suggestion: str) -> dict[str, str]:
    """生成检查结果。"""
    return {
        "status": PASS if ok else FAIL,
        "item": item,
        "detail": detail,
        "suggestion": "" if ok else suggestion,
    }


def _read_text(path: Path) -> str:
    """安全读取页面文本。"""
    if not path.exists():
        return ""
    return path.read_text(encoding="utf-8", errors="ignore")


def _load_final_snapshot(output_dir: Path, fallback: dict[str, Any]) -> dict[str, Any]:
    """优先从最终 latest.json 读取自检输入。"""
    latest = output_dir / "data" / "latest.json"
    if not latest.exists():
        return fallback
    try:
        return json.loads(latest.read_text(encoding="utf-8"))
    except Exception:
        return fallback


def _render_report(checks: dict[str, list[dict[str, str]]], generated_at: str) -> str:
    """渲染自检 Markdown。"""
    lines = [
        "# A 股主线雷达自检报告",
        "",
        f"生成时间：{generated_at}",
        "",
    ]
    for section, rows in checks.items():
        lines.extend([f"## {section}", ""])
        for row in rows:
            lines.append(f"- {row['status']}：{row['item']}")
            if row.get("detail"):
                lines.append(f"  - 说明：{row['detail']}")
            if row.get("suggestion"):
                lines.append(f"  - 修复建议：{row['suggestion']}")
        lines.append("")
    return "\n".join(lines)


def _render_html_report(checks: dict[str, list[dict[str, str]]], generated_at: str) -> str:
    """渲染 HTML 自检报告，便于 GitHub Pages 直接打开。"""
    sections = []
    for section, rows in checks.items():
        items = []
        for row in rows:
            detail = f"<p>{html.escape(row.get('detail', ''))}</p>" if row.get("detail") else ""
            suggestion = f"<p><strong>修复建议：</strong>{html.escape(row.get('suggestion', ''))}</p>" if row.get("suggestion") else ""
            cls = "pass" if row["status"].startswith("✅") else "fail" if row["status"].startswith("❌") else "warn"
            items.append(
                f"""
                <li class="{cls}">
                  <strong>{html.escape(row['status'])}：{html.escape(row['item'])}</strong>
                  {detail}
                  {suggestion}
                </li>
                """
            )
        sections.append(f"<section><h2>{html.escape(section)}</h2><ul>{''.join(items)}</ul></section>")
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>A股主线雷达自检报告</title>
  <style>
    body {{ margin: 0; padding: 28px; background: #f5f7fb; color: #202631; font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", sans-serif; }}
    main {{ max-width: 980px; margin: 0 auto; }}
    section {{ background: #fff; border: 1px solid #d9e0ea; border-radius: 8px; padding: 18px; margin: 16px 0; }}
    h1 {{ margin: 0 0 6px; }}
    h2 {{ margin: 0 0 12px; }}
    ul {{ list-style: none; padding: 0; margin: 0; }}
    li {{ border-bottom: 1px solid #edf1f6; padding: 11px 0; }}
    li:last-child {{ border-bottom: 0; }}
    p {{ margin: 6px 0 0; color: #667085; }}
    .pass strong {{ color: #0f766e; }}
    .warn strong {{ color: #b7791f; }}
    .fail strong {{ color: #b42318; }}
    .muted {{ color: #667085; }}
  </style>
</head>
<body>
  <main>
    <h1>A 股主线雷达自检报告</h1>
    <p class="muted">生成时间：{html.escape(generated_at)}</p>
    {''.join(sections)}
  </main>
</body>
</html>
"""


def _clean_text(content: str) -> str:
    """清理报告行尾空白。"""
    return "\n".join(line.rstrip() for line in content.splitlines()) + "\n"
