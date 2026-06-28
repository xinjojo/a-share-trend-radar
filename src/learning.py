"""V4 Learning Engine。

学习引擎只输出权重优化建议，不自动修改评分公式。
"""

from __future__ import annotations

from typing import Any

from config import OPERATING_SYSTEM_RULES
from src.utils import safe_float


def build_optimization_report(evidence: dict[str, Any]) -> dict[str, Any]:
    """基于 Evidence 生成权重优化建议。"""
    stats = evidence.get("snapshot_stats", {}) if isinstance(evidence, dict) else {}
    technical = evidence.get("technical_rules", []) if isinstance(evidence, dict) else []
    snapshot_days = int(stats.get("snapshot_days") or 0)
    event_count = sum(int(row.get("sample_count", 0) or 0) for row in technical)
    enough = snapshot_days >= 60 and event_count >= 500
    suggestions = []
    if not enough:
        suggestions.append(
            {
                "item": "保持现有权重",
                "reason": f"真实快照 {snapshot_days} 天、技术信号样本 {event_count} 次，暂不足以做月度权重优化。",
                "suggested_change": "不调整",
            }
        )
        suggestions.append(
            {
                "item": "优先积累证据",
                "reason": "继续保存每日快照，并每月运行一次个股技术信号回测。",
                "suggested_change": "积累到 60 个快照日或 500 个信号样本后再评估。",
            }
        )
    else:
        best_pullback = _best_rule(technical, ["缩量回踩MA5", "缩量回踩MA10", "缩量回踩MA20"])
        best_reversal = _best_rule(technical, ["放量反包"])
        if best_pullback and safe_float(best_pullback.get("avg_return_pct")) > safe_float(best_reversal.get("avg_return_pct")):
            suggestions.append(
                {
                    "item": "提高回踩确认权重",
                    "reason": f"{best_pullback.get('rule')} 的 10 日平均收益更好，样本 {best_pullback.get('sample_count')} 次。",
                    "suggested_change": "下月可人工测试提高回踩信号权重。",
                }
            )
        elif best_reversal:
            suggestions.append(
                {
                    "item": "保留放量反包权重",
                    "reason": f"放量反包样本 {best_reversal.get('sample_count')} 次，平均收益 {safe_float(best_reversal.get('avg_return_pct')):.2f}%。",
                    "suggested_change": "不自动修改，仅进入人工复核。",
                }
            )
    return {
        "title": "Optimization Report",
        "status": "样本充足，可人工复核" if enough else "样本不足，不建议调参",
        "snapshot_days": snapshot_days,
        "technical_event_count": event_count,
        "current_rules": OPERATING_SYSTEM_RULES,
        "suggestions": suggestions,
        "policy": "学习引擎只生成建议，不自动修改评分权重。",
    }


def _best_rule(rows: list[dict[str, Any]], names: list[str]) -> dict[str, Any]:
    """按平均收益选出证据最强的规则。"""
    candidates = [
        row
        for row in rows
        if str(row.get("rule")) in names and int(row.get("sample_count") or 0) >= 30
    ]
    if not candidates:
        return {}
    return sorted(candidates, key=lambda row: safe_float(row.get("avg_return_pct")), reverse=True)[0]
