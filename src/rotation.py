"""行业/概念主线轮动追踪。"""

from __future__ import annotations

from datetime import datetime

import pandas as pd

from src.database import get_connection, init_db
from src.utils import safe_float, safe_int, today_str


ROTATION_TABLE = "sector_rotation_history"


def build_rotation_tracker(
    sector_df: pd.DataFrame,
    report_date: str | None = None,
    lookback_days: int = 20,
    persist: bool = True,
) -> dict[str, pd.DataFrame]:
    """保存并分析近 N 次 Top10 主线迁移。"""
    report_date = report_date or today_str()
    if sector_df is None or sector_df.empty:
        empty = pd.DataFrame()
        return {"history": empty, "migration": empty, "summary": empty, "current_top": empty}
    current_top = _current_top10(sector_df, report_date)
    if persist:
        save_rotation_snapshot(report_date, current_top)
    history = load_rotation_history(lookback_days=lookback_days)
    if history.empty:
        history = current_top
    migration = build_migration_table(history)
    summary = build_rotation_summary(history)
    return {
        "history": history,
        "migration": migration,
        "summary": summary,
        "current_top": current_top,
    }


def save_rotation_snapshot(report_date: str, top_df: pd.DataFrame) -> None:
    """保存某日 Top10 主线快照，重复日期自动覆盖。"""
    if top_df is None or top_df.empty:
        return
    _init_rotation_table()
    rows = _records_for_sql(top_df, report_date)
    with get_connection() as conn:
        conn.execute(f"DELETE FROM {ROTATION_TABLE} WHERE report_date = ?", (report_date,))
        conn.executemany(
            f"""
            INSERT INTO {ROTATION_TABLE}(
                report_date, rank, board_code, board_name, board_layer, category,
                score, lifecycle_state, lifecycle_progress, lifecycle_recommendation,
                change_pct, ret_5d, ret_10d, amount_ratio_20, created_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )


def load_rotation_history(lookback_days: int = 20) -> pd.DataFrame:
    """读取最近 N 个快照日的 Top10 主线历史。"""
    _init_rotation_table()
    with get_connection() as conn:
        dates = pd.read_sql_query(
            f"SELECT DISTINCT report_date FROM {ROTATION_TABLE} ORDER BY report_date DESC LIMIT ?",
            conn,
            params=(lookback_days,),
        )
        if dates.empty:
            return pd.DataFrame()
        date_list = dates["report_date"].tolist()
        placeholders = ",".join(["?"] * len(date_list))
        df = pd.read_sql_query(
            f"""
            SELECT * FROM {ROTATION_TABLE}
            WHERE report_date IN ({placeholders})
            ORDER BY report_date ASC, rank ASC
            """,
            conn,
            params=date_list,
        )
    return df


def build_migration_table(history: pd.DataFrame) -> pd.DataFrame:
    """生成“日期 | 第一主线 | 第二主线 | 第三主线 | 新增主线 | 退潮主线”表。"""
    if history is None or history.empty:
        return pd.DataFrame()
    rows = []
    prev_boards: set[str] = set()
    for date, group in history.groupby("report_date", sort=True):
        ranked = group.sort_values("rank")
        board_names = ranked["board_name"].astype(str).tolist()
        board_codes = set(ranked["board_code"].astype(str).tolist())
        new_names = ranked[~ranked["board_code"].astype(str).isin(prev_boards)]["board_name"].astype(str).tolist()
        faded_codes = prev_boards - board_codes
        faded_names = []
        if faded_codes:
            prev_rows = history[(history["report_date"] < date) & (history["board_code"].astype(str).isin(faded_codes))]
            prev_latest = prev_rows.sort_values("report_date").groupby("board_code").tail(1)
            faded_names = prev_latest["board_name"].astype(str).tolist()
        rows.append(
            {
                "日期": date,
                "第一主线": board_names[0] if len(board_names) > 0 else "",
                "第二主线": board_names[1] if len(board_names) > 1 else "",
                "第三主线": board_names[2] if len(board_names) > 2 else "",
                "新增主线": " / ".join(new_names[:5]),
                "退潮主线": " / ".join(faded_names[:5]),
            }
        )
        prev_boards = board_codes
    return pd.DataFrame(rows)


def build_rotation_summary(history: pd.DataFrame) -> pd.DataFrame:
    """统计每个板块的连续上榜、排名变化、分数变化和迁移状态。"""
    if history is None or history.empty:
        return pd.DataFrame()
    latest_date = str(history["report_date"].max())
    previous_dates = sorted(history["report_date"].astype(str).unique().tolist())
    previous_date = previous_dates[-2] if len(previous_dates) >= 2 else ""
    latest_codes = set(history[history["report_date"] == latest_date]["board_code"].astype(str).tolist())
    previous_codes = (
        set(history[history["report_date"] == previous_date]["board_code"].astype(str).tolist())
        if previous_date
        else set()
    )

    rows = []
    for board_code, group in history.groupby("board_code", sort=False):
        group = group.sort_values("report_date")
        latest = group.iloc[-1]
        first = group.iloc[0]
        prev = group.iloc[-2] if len(group) >= 2 else first
        consecutive_days = _consecutive_appearances(history, str(board_code), latest_date)
        rank_change = safe_int(prev.get("rank")) - safe_int(latest.get("rank")) if len(group) >= 2 else 0
        score_change = safe_float(latest.get("score")) - safe_float(prev.get("score")) if len(group) >= 2 else 0.0
        status = _migration_status(str(board_code), latest, rank_change, score_change, latest_codes, previous_codes)
        rows.append(
            {
                "board_code": board_code,
                "board_name": latest.get("board_name", ""),
                "board_layer": latest.get("board_layer", ""),
                "连续上榜天数": consecutive_days,
                "首次上榜日期": first.get("report_date", ""),
                "最近上榜日期": latest.get("report_date", ""),
                "当前排名": safe_int(latest.get("rank")),
                "排名变化": rank_change,
                "分数变化": round(score_change, 2),
                "生命周期": latest.get("lifecycle_state", ""),
                "生命周期变化": _lifecycle_change(prev.get("lifecycle_state", ""), latest.get("lifecycle_state", "")),
                "轮动状态": status,
                "score": safe_float(latest.get("score")),
            }
        )
    out = pd.DataFrame(rows)
    if out.empty:
        return out
    return out.sort_values(["最近上榜日期", "轮动状态", "当前排名"], ascending=[False, True, True]).reset_index(drop=True)


def _current_top10(sector_df: pd.DataFrame, report_date: str) -> pd.DataFrame:
    """取当前 Top10 主线并保留轮动字段。"""
    columns = [
        "rank",
        "board_code",
        "board_name",
        "board_layer",
        "category",
        "score",
        "lifecycle_state",
        "lifecycle_progress",
        "lifecycle_recommendation",
        "change_pct",
        "ret_5d",
        "ret_10d",
        "amount_ratio_20",
    ]
    out = sector_df.sort_values("score", ascending=False).head(10).copy()
    for col in columns:
        if col not in out.columns:
            out[col] = ""
    out = out[columns]
    out["report_date"] = report_date
    return out


def _records_for_sql(top_df: pd.DataFrame, report_date: str) -> list[tuple]:
    """把 DataFrame 转成 SQLite 参数行。"""
    created_at = datetime.now().isoformat(timespec="seconds")
    rows = []
    for _, row in top_df.iterrows():
        rows.append(
            (
                report_date,
                safe_int(row.get("rank")),
                str(row.get("board_code", "")),
                str(row.get("board_name", "")),
                str(row.get("board_layer", "")),
                str(row.get("category", "")),
                safe_float(row.get("score")),
                str(row.get("lifecycle_state", "")),
                safe_float(row.get("lifecycle_progress")),
                str(row.get("lifecycle_recommendation", "")),
                safe_float(row.get("change_pct")),
                safe_float(row.get("ret_5d")),
                safe_float(row.get("ret_10d")),
                safe_float(row.get("amount_ratio_20")),
                created_at,
            )
        )
    return rows


def _init_rotation_table() -> None:
    """初始化轮动历史表。"""
    init_db()
    with get_connection() as conn:
        conn.execute(
            f"""
            CREATE TABLE IF NOT EXISTS {ROTATION_TABLE} (
                report_date TEXT NOT NULL,
                rank INTEGER NOT NULL,
                board_code TEXT NOT NULL,
                board_name TEXT NOT NULL,
                board_layer TEXT,
                category TEXT,
                score REAL,
                lifecycle_state TEXT,
                lifecycle_progress REAL,
                lifecycle_recommendation TEXT,
                change_pct REAL,
                ret_5d REAL,
                ret_10d REAL,
                amount_ratio_20 REAL,
                created_at TEXT NOT NULL,
                PRIMARY KEY (report_date, board_code)
            )
            """
        )


def _consecutive_appearances(history: pd.DataFrame, board_code: str, latest_date: str) -> int:
    """计算截至最近日期的连续上榜次数。"""
    dates = sorted(history["report_date"].astype(str).unique().tolist(), reverse=True)
    count = 0
    for date in dates:
        codes = set(history[history["report_date"].astype(str) == date]["board_code"].astype(str).tolist())
        if board_code in codes:
            count += 1
        elif date <= latest_date:
            break
    return count


def _migration_status(
    board_code: str,
    latest: pd.Series,
    rank_change: int,
    score_change: float,
    latest_codes: set[str],
    previous_codes: set[str],
) -> str:
    """输出新进入、强化、分歧、退出和接力方向。"""
    lifecycle = str(latest.get("lifecycle_state", ""))
    if board_code not in latest_codes:
        return "退出主线"
    if board_code in latest_codes and board_code not in previous_codes:
        if lifecycle in {"启动期", "修复期"}:
            return "可能接力的新方向"
        return "新进入主线"
    if lifecycle in {"分歧期", "高潮期"} or score_change < -3:
        return "开始分歧主线"
    if rank_change > 0 or score_change > 2 or lifecycle == "主升期":
        return "正在强化主线"
    return "观察中"


def _lifecycle_change(previous: str, current: str) -> str:
    """生命周期变化文本。"""
    if not previous or previous == current:
        return "未变"
    return f"{previous} -> {current}"
