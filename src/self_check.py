"""静态站构建后的自检系统。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

from src.utils import safe_float


PASS = "✅ 已通过"
WARN = "⚠️ 需要人工确认"
FAIL = "❌ 未通过"
EMOTION_KEYWORDS = ("昨日", "涨停", "跌停", "连板", "打板", "炸板", "晋级", "高标", "情绪")


def run_self_check(snapshot: dict[str, Any], output_dir: Path | str) -> Path:
    """运行构建后自检并生成 Markdown 报告。"""
    output_path = Path(output_dir)
    output_path.mkdir(parents=True, exist_ok=True)
    checks = {
        "数据完整性": _check_data_integrity(snapshot),
        "页面完整性": _check_page_integrity(snapshot, output_path),
        "逻辑完整性": _check_logic_integrity(snapshot),
    }
    report_path = output_path / "self_check_report.md"
    report_path.write_text(_render_report(checks), encoding="utf-8")
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
    ]
    return rows


def _check_page_integrity(snapshot: dict[str, Any], output_dir: Path) -> list[dict[str, str]]:
    """检查关键页面模块是否存在。"""
    index = _read_text(output_dir / "index.html")
    lifecycle = _read_text(output_dir / "lifecycle.html")
    daily = _read_text(output_dir / "daily.html")
    generated_at = str(snapshot.get("generated_at", ""))
    return [
        _result("今日一句话" in index, "首页有今日一句话", "", "检查 render_index_page。"),
        _result("今日 Action" in index, "首页有今日 Action", "", "检查 render_index_page。"),
        _result("今日变化" in index, "首页有今日变化", "", "检查 render_index_page。"),
        _result("机会分" in lifecycle and "风险分" in lifecycle, "生命周期页有机会分/风险分", "", "检查 render_lifecycle_page。"),
        _result("进度" not in lifecycle, "生命周期页没有“进度”字段", "", "移除生命周期页 lifecycle_progress 展示，避免退潮期 100/100 误导。"),
        _result("3 分钟摘要" in daily or "3分钟摘要" in daily, "日报页有 3 分钟摘要", "", "在日报开头保留简短摘要说明。"),
        _result(
            bool(generated_at) and generated_at in index and generated_at in daily,
            "日报生成时间与首页一致",
            f"生成时间：{generated_at}",
            "检查 render_index_page/render_daily_page 是否使用同一 snapshot.generated_at。",
        ),
    ]


def _check_logic_integrity(snapshot: dict[str, Any]) -> list[dict[str, str]]:
    """检查关键业务约束。"""
    ops = snapshot.get("operating_summary", {})
    groups = ops.get("stock_groups", {})
    research = groups.get("可研究候选", []) or []
    all_group_rows = []
    for rows in groups.values():
        all_group_rows.extend(rows or [])
    sectors = snapshot.get("sectors", []) or []

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

    return [
        _result(not bad_research, "高位/退潮股票未进入可研究候选", f"异常 {len(bad_research)} 只。", "检查 build_stock_groups 分组条件。"),
        _result(
            not bad_research_parent,
            "可研究候选所属主线 Action 必须等于重点研究",
            f"异常 {len(bad_research_parent)} 只。",
            "检查 build_stock_groups，个股不能越过父级主线 Action 单独升级。",
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


def _render_report(checks: dict[str, list[dict[str, str]]]) -> str:
    """渲染自检 Markdown。"""
    lines = [
        "# A 股主线雷达自检报告",
        "",
        f"生成时间：{datetime.now().isoformat(timespec='seconds')}",
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
