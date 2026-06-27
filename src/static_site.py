"""静态站点生成器。

把动态扫描结果渲染成 GitHub Pages 可托管的纯 HTML 快照。
"""

from __future__ import annotations

import html
import json
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from config import BASE_DIR, BOARD_ANALYSIS_LIMIT, INDEX_SYMBOLS
from src.data_provider import get_provider
from src.report_generator import generate_daily_report
from src.scoring import score_market_temperature
from src.sector_radar import build_sector_radar
from src.stock_radar import build_leader_pool
from src.utils import safe_float, today_str


DEFAULT_OUTPUT_DIR = BASE_DIR / "docs"


def build_static_snapshot(
    output_dir: Path | str = DEFAULT_OUTPUT_DIR,
    report_date: str | None = None,
    max_boards: int = BOARD_ANALYSIS_LIMIT,
    include_concepts: bool = True,
) -> dict[str, Any]:
    """运行扫描并生成静态站点。"""
    report_date = report_date or today_str()
    output_path = Path(output_dir)
    assets_dir = output_path / "assets"
    data_dir = output_path / "data"
    history_dir = output_path / "history"
    for path in (output_path, assets_dir, data_dir, history_dir):
        path.mkdir(parents=True, exist_ok=True)

    provider = get_provider()
    market_df = provider.get_market_quotes()
    index_df = provider.get_index_quotes(INDEX_SYMBOLS)
    market_temperature = score_market_temperature(market_df, index_df)
    sector_pack = build_sector_radar(provider, max_boards=max_boards, include_concepts=include_concepts)
    sector_df = sector_pack["all"]
    leader_df = build_leader_pool(provider, sector_df)
    markdown = generate_daily_report(market_temperature, sector_df, leader_df, report_date=report_date)

    snapshot = {
        "report_date": report_date,
        "generated_at": datetime.now().isoformat(timespec="seconds"),
        "market_temperature": market_temperature,
        "data_basis": _build_data_basis(report_date, market_temperature, sector_df, leader_df),
        "index_quotes": _records(index_df),
        "sectors": _records(sector_df),
        "industry_sectors": _records(sector_pack.get("industry")),
        "concept_sectors": _records(sector_pack.get("concept")),
        "emotion_observations": _records(sector_pack.get("emotion")),
        "leaders": _records(leader_df),
        "daily_report": markdown,
        "data_sources": _collect_sources(market_df, index_df, sector_df, leader_df),
    }

    (assets_dir / "style.css").write_text(_site_css(), encoding="utf-8")
    (data_dir / "latest.json").write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    pages = {
        "index.html": render_index_page(snapshot),
        "sectors.html": render_sectors_page(snapshot),
        "stocks.html": render_stocks_page(snapshot),
        "daily.html": render_daily_page(snapshot),
    }
    for filename, content in pages.items():
        (output_path / filename).write_text(_clean_text(content), encoding="utf-8")

    history_file = history_dir / f"{report_date}.html"
    history_file.write_text(_clean_text(render_daily_page(snapshot, is_history=True)), encoding="utf-8")
    _write_history_index(history_dir)

    return {
        "output_dir": str(output_path),
        "report_date": report_date,
        "market_score": market_temperature.get("score", 0),
        "risk_preference": market_temperature.get("risk_preference", ""),
        "sector_count": len(sector_df) if sector_df is not None else 0,
        "leader_count": len(leader_df) if leader_df is not None else 0,
        "history_file": str(history_file),
    }


def clean_static_output(output_dir: Path | str = DEFAULT_OUTPUT_DIR) -> None:
    """清理静态站输出目录，但保留 history 历史页面。"""
    output_path = Path(output_dir)
    history_dir = output_path / "history"
    preserved_history = None
    if history_dir.exists():
        preserved_history = output_path.parent / ".radar_history_tmp"
        if preserved_history.exists():
            shutil.rmtree(preserved_history)
        shutil.copytree(history_dir, preserved_history)
    if output_path.exists():
        shutil.rmtree(output_path)
    output_path.mkdir(parents=True, exist_ok=True)
    if preserved_history and preserved_history.exists():
        shutil.copytree(preserved_history, output_path / "history")
        shutil.rmtree(preserved_history)


def render_index_page(snapshot: dict[str, Any]) -> str:
    """首页。"""
    temp = snapshot["market_temperature"]
    metrics = temp.get("metrics", {})
    basis = snapshot.get("data_basis", {})
    sectors = snapshot["sectors"]
    leaders = snapshot["leaders"]
    research_leaders, watch_leaders = _split_leaders(leaders)
    sample_warning = "" if metrics.get("is_full_market_sample", True) else f'<div class="warning">{_e(metrics.get("sample_note", "非全市场样本"))}</div>'
    top_sectors = sectors[:10]
    continuous = [s for s in sectors if s.get("category") == "持续主线"][:6]
    hot = [s for s in sectors if s.get("category") == "短线热点"][:6]
    fading = [s for s in sectors if s.get("category") == "退潮板块"][:6]

    body = f"""
    <section class="hero">
      <div>
        <p class="eyebrow">A-Share Trend Radar</p>
        <h1>A股主线雷达</h1>
        <p class="muted">生成时间：{_e(snapshot["generated_at"])} · 本页面为静态快照，非实时行情。</p>
      </div>
      <div class="temperature">
        <div class="temperature-score">{_fmt(temp.get("score"), 1)}</div>
        <div class="muted">市场温度 / 100</div>
        <span class="badge">{_e(temp.get("risk_preference", "未知"))}</span>
      </div>
    </section>
    <section class="summary-grid">
      {_metric_card("统计股票数", str(metrics.get('sample_count', metrics.get('total', 0))), metrics.get("sample_note", "全市场样本"))}
      {_metric_card("上涨/下跌", f"{metrics.get('up_count', 0)} / {metrics.get('down_count', 0)}", "市场宽度")}
      {_metric_card("涨停/跌停", f"{metrics.get('limit_up', 0)} / {metrics.get('limit_down', 0)}", "强弱极值")}
      {_metric_card("成交额", f"{_fmt(metrics.get('total_amount_yi'), 0)} 亿", "全市场")}
    </section>
    {sample_warning}
    {_data_basis_panel(basis)}
    <section class="panel">
      <h2>主要指数</h2>
      {_table(snapshot["index_quotes"], ["index_name", "price", "change_pct", "amount_yi"], {"index_name": "指数", "price": "点位", "change_pct": "涨跌幅%", "amount_yi": "成交额亿"})}
    </section>
    <section class="panel">
      <h2>今日最强主线 Top 10</h2>
      {_sector_table(top_sectors)}
    </section>
    <section class="three-columns">
      {_mini_sector_panel("持续主线", continuous)}
      {_mini_sector_panel("短线热点", hot)}
      {_mini_sector_panel("退潮板块", fading)}
    </section>
    <section class="panel">
      <h2>今日可研究股票池</h2>
      {_leader_group("可研究候选", research_leaders[:20])}
      {_leader_group("高位观察/不适合追", watch_leaders[:20])}
    </section>
    """
    return _layout("A股主线雷达", "index", body)


def render_sectors_page(snapshot: dict[str, Any]) -> str:
    """主线雷达页。"""
    sectors = snapshot["sectors"]
    industry = snapshot.get("industry_sectors", [])
    concept = snapshot.get("concept_sectors", [])
    emotion = snapshot.get("emotion_observations", [])
    body = f"""
    <section class="page-title">
      <p class="eyebrow">Sector Radar</p>
      <h1>主线雷达</h1>
      <p class="muted">综合资金持续性、成交额活跃度、趋势强度、赚钱效应、龙头集中度与过热风险。</p>
    </section>
    <section class="panel">
      <h2>板块评分表</h2>
      {_sector_table(sectors)}
    </section>
    <section class="panel">
      <h2>行业板块</h2>
      {_sector_table(industry)}
    </section>
    <section class="panel">
      <h2>概念板块</h2>
      {_sector_table(concept)}
    </section>
    <section class="panel">
      <h2>短线情绪观察</h2>
      <p class="muted">昨日涨停、连板、打板等短线情绪标签不参与主线行业/概念排名。</p>
      {_emotion_table(emotion)}
    </section>
    <section class="sector-list">
      {''.join(_sector_card(sector) for sector in sectors[:24]) if sectors else _empty_state()}
    </section>
    """
    return _layout("主线雷达", "sectors", body)


def render_stocks_page(snapshot: dict[str, Any]) -> str:
    """龙头股票池页。"""
    leaders = snapshot["leaders"]
    research_leaders, watch_leaders = _split_leaders(leaders)
    body = f"""
    <section class="page-title">
      <p class="eyebrow">Leader Pool</p>
      <h1>龙头股票池</h1>
      <p class="muted">只输出观察状态，不输出买入建议。</p>
    </section>
    <section class="panel">
      <h2>股票池排名</h2>
      {_leader_group("可研究候选", research_leaders)}
      {_leader_group("高位观察/不适合追", watch_leaders)}
    </section>
    <section class="stock-list">
      {''.join(_stock_card(stock) for stock in leaders[:40]) if leaders else _empty_state()}
    </section>
    """
    return _layout("龙头股票池", "stocks", body)


def render_daily_page(snapshot: dict[str, Any], is_history: bool = False) -> str:
    """日报页。"""
    title = f"日报 {snapshot['report_date']}" if is_history else "日报"
    body = f"""
    <section class="page-title">
      <p class="eyebrow">Daily Report</p>
      <h1>A股主线雷达日报</h1>
      <p class="muted">日期：{_e(snapshot["report_date"])} · 生成时间：{_e(snapshot["generated_at"])}</p>
    </section>
    <article class="markdown-body">
      {_markdown_to_html(snapshot["daily_report"])}
    </article>
    """
    return _layout(title, "daily", body, root_prefix="../" if is_history else "")


def _layout(title: str, active: str, body: str, root_prefix: str = "") -> str:
    """统一 HTML 布局。"""
    history_href = "index.html" if root_prefix else "history/index.html"
    nav = [
        ("index", "首页", f"{root_prefix}index.html"),
        ("sectors", "主线", f"{root_prefix}sectors.html"),
        ("stocks", "股票池", f"{root_prefix}stocks.html"),
        ("daily", "日报", f"{root_prefix}daily.html"),
        ("history", "历史", history_href),
    ]
    nav_html = "".join(
        f'<a class="{"active" if key == active else ""}" href="{href}">{label}</a>'
        for key, label, href in nav
    )
    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>{_e(title)} - A股主线雷达</title>
  <link rel="stylesheet" href="{root_prefix}assets/style.css">
</head>
<body>
  <header class="topbar">
    <a class="brand" href="{root_prefix}index.html">A股主线雷达</a>
    <nav>{nav_html}</nav>
  </header>
  <main>{body}</main>
  <footer>
    <span>研究辅助，不构成投资建议。</span>
    <a href="{root_prefix}data/latest.json">latest.json</a>
  </footer>
</body>
</html>
"""


def _write_history_index(history_dir: Path) -> None:
    """生成历史归档索引。"""
    files = sorted(history_dir.glob("*.html"), reverse=True)
    items = "\n".join(
        f'<li><a href="{file.name}">{file.stem}</a></li>'
        for file in files
        if file.name != "index.html"
    )
    body = f"""
    <section class="page-title">
      <p class="eyebrow">Archive</p>
      <h1>历史快照</h1>
      <p class="muted">每次手动更新都会在这里保留一份日报归档。</p>
    </section>
    <section class="panel">
      <ul class="archive-list">{items or '<li>暂无历史快照</li>'}</ul>
    </section>
    """
    html_text = _layout("历史快照", "history", body, root_prefix="../")
    (history_dir / "index.html").write_text(_clean_text(html_text), encoding="utf-8")


def _metric_card(label: str, value: str, note: str) -> str:
    """指标卡片。"""
    return f"""
    <div class="metric-card">
      <div class="metric-value">{_e(value)}</div>
      <div class="metric-label">{_e(label)}</div>
      <div class="metric-note">{_e(note)}</div>
    </div>
    """


def _data_basis_panel(basis: dict[str, Any]) -> str:
    """首页数据口径说明。"""
    items = [
        ("数据日期", basis.get("data_date", "")),
        ("股票池范围", basis.get("stock_pool_scope", "")),
        ("价格口径", basis.get("price_basis", "")),
        ("资金口径", basis.get("fund_basis", "")),
    ]
    return f"""
    <section class="panel">
      <h2>数据口径</h2>
      <div class="basis-grid">
        {''.join(f'<div class="basis-item"><span>{_e(label)}</span><strong>{_e(value)}</strong></div>' for label, value in items)}
      </div>
    </section>
    """


def _split_leaders(rows: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """按股票池分组拆成主候选和高位观察。"""
    research = [row for row in rows if row.get("pool_group") == "可研究候选"]
    watch = [row for row in rows if row.get("pool_group") != "可研究候选"]
    return research, watch


def _leader_group(title: str, rows: list[dict[str, Any]]) -> str:
    """带标题的股票池分组。"""
    return f"""
    <div class="subsection">
      <h3>{_e(title)}</h3>
      {_leader_table(rows)}
    </div>
    """


def _sector_table(rows: list[dict[str, Any]]) -> str:
    """板块表。"""
    return _table(
        rows,
        ["rank", "board_name", "board_layer", "category", "score", "rank_stability_score", "flow_score_label", "flow_score", "change_pct", "ret_5d", "ret_10d", "amount_ratio_20", "up_ratio", "top_stocks"],
        {
            "rank": "排名",
            "board_name": "板块",
            "board_layer": "分层",
            "category": "分类",
            "score": "综合分",
            "rank_stability_score": "稳定性",
            "flow_score_label": "资金/代理类型",
            "flow_score": "流/活跃分",
            "change_pct": "当日%",
            "ret_5d": "5日%",
            "ret_10d": "10日%",
            "amount_ratio_20": "量能倍数",
            "up_ratio": "上涨占比",
            "top_stocks": "核心成分股",
        },
    )


def _emotion_table(rows: list[dict[str, Any]]) -> str:
    """短线情绪表。"""
    return _table(
        rows,
        ["board_name", "change_pct", "amount_yi", "up_count", "down_count", "leader", "emotion_reason"],
        {
            "board_name": "标签",
            "change_pct": "涨跌幅%",
            "amount_yi": "成交额亿",
            "up_count": "上涨家数",
            "down_count": "下跌家数",
            "leader": "领涨",
            "emotion_reason": "说明",
        },
    )


def _leader_table(rows: list[dict[str, Any]]) -> str:
    """股票池表。"""
    return _table(
        rows,
        [
            "pool_group",
            "code",
            "name",
            "board_name",
            "leader_score",
            "research_priority_score",
            "price",
            "price_basis",
            "current_price",
            "price_check_diff_pct",
            "change_pct",
            "amount_yi",
            "ret_20d",
            "ret_60d",
            "close",
            "ma20",
            "distance_ma20_pct",
            "trend_status",
            "observe_status",
            "price_check_status",
            "invalid_condition",
        ],
        {
            "pool_group": "分组",
            "code": "代码",
            "name": "名称",
            "board_name": "主线",
            "leader_score": "龙头分",
            "research_priority_score": "研究优先级",
            "price": "价格",
            "price_basis": "价格口径",
            "current_price": "行情参考价",
            "price_check_diff_pct": "校验偏差%",
            "change_pct": "涨跌幅%",
            "amount_yi": "成交额亿",
            "ret_20d": "20日%",
            "ret_60d": "60日%",
            "close": "Close",
            "ma20": "MA20",
            "distance_ma20_pct": "距MA20%",
            "trend_status": "趋势",
            "observe_status": "观察状态",
            "price_check_status": "价格校验",
            "invalid_condition": "失效条件",
        },
    )


def _table(rows: list[dict[str, Any]], columns: list[str], headers: dict[str, str]) -> str:
    """渲染响应式表格。"""
    if not rows:
        return _empty_state()
    thead = "".join(f"<th>{_e(headers.get(col, col))}</th>" for col in columns)
    body_rows = []
    for row in rows:
        cells = []
        for col in columns:
            value = row.get(col, "")
            classes = _value_class(col, value)
            cells.append(f'<td data-label="{_e(headers.get(col, col))}" class="{classes}">{_format_cell(col, value)}</td>')
        body_rows.append(f"<tr>{''.join(cells)}</tr>")
    return f'<div class="table-wrap"><table><thead><tr>{thead}</tr></thead><tbody>{"".join(body_rows)}</tbody></table></div>'


def _mini_sector_panel(title: str, rows: list[dict[str, Any]]) -> str:
    """首页三分类小面板。"""
    items = "".join(
        f"""
        <li>
          <span>{_e(row.get("board_name", ""))}</span>
          <strong>{_fmt(row.get("score"), 1)}</strong>
        </li>
        """
        for row in rows
    )
    return f"""
    <section class="panel compact">
      <h2>{_e(title)}</h2>
      <ul class="mini-list">{items or '<li><span>暂无</span><strong>-</strong></li>'}</ul>
    </section>
    """


def _sector_card(row: dict[str, Any]) -> str:
    """板块卡片。"""
    return f"""
    <article class="detail-card">
      <div class="card-title">
        <h3>{_e(row.get("board_name", ""))}</h3>
        <span class="badge">{_e(row.get("category", ""))}</span>
      </div>
      <div class="score-line"><span style="width:{safe_float(row.get("score"))}%"></span></div>
      <dl>
        <div><dt>综合分</dt><dd>{_fmt(row.get("score"), 1)}</dd></div>
        <div><dt>当日涨幅</dt><dd class="{_value_class("change_pct", row.get("change_pct"))}">{_fmt(row.get("change_pct"), 2)}%</dd></div>
        <div><dt>5日涨幅</dt><dd>{_fmt(row.get("ret_5d"), 2)}%</dd></div>
        <div><dt>量能倍数</dt><dd>{_fmt(row.get("amount_ratio_20"), 2)}</dd></div>
      </dl>
      <p class="muted">核心成分股：{_e(row.get("top_stocks", "") or "暂无")}</p>
    </article>
    """


def _stock_card(row: dict[str, Any]) -> str:
    """股票卡片。"""
    return f"""
    <article class="detail-card">
      <div class="card-title">
        <h3>{_e(row.get("name", ""))} <span>{_e(row.get("code", ""))}</span></h3>
        <span class="badge">{_e(row.get("observe_status", ""))}</span>
      </div>
      <dl>
        <div><dt>分组</dt><dd>{_e(row.get("pool_group", ""))}</dd></div>
        <div><dt>所属主线</dt><dd>{_e(row.get("board_name", ""))}</dd></div>
        <div><dt>龙头分</dt><dd>{_fmt(row.get("leader_score"), 1)}</dd></div>
        <div><dt>成交额</dt><dd>{_fmt(row.get("amount_yi"), 1)} 亿</dd></div>
        <div><dt>价格口径</dt><dd>{_e(row.get("price_basis", "不复权"))}</dd></div>
        <div><dt>Close / MA20</dt><dd>{_fmt(row.get("close"), 2)} / {_fmt(row.get("ma20"), 2)}</dd></div>
        <div><dt>距 MA20</dt><dd>{_fmt(row.get("distance_ma20_pct"), 2)}%</dd></div>
        <div><dt>趋势</dt><dd>{_e(row.get("trend_status", ""))}</dd></div>
        <div><dt>价格校验</dt><dd>{_e(row.get("price_check_status", ""))}</dd></div>
      </dl>
      <p class="muted">失效条件：{_e(row.get("invalid_condition", ""))}</p>
    </article>
    """


def _markdown_to_html(markdown_text: str) -> str:
    """把当前日报 Markdown 子集渲染成 HTML。"""
    html_lines: list[str] = []
    in_list = False
    for raw in markdown_text.splitlines():
        line = raw.strip()
        if not line:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            continue
        if line.startswith("# "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h1>{_inline_md(line[2:])}</h1>")
        elif line.startswith("## "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<h2>{_inline_md(line[3:])}</h2>")
        elif line.startswith("> "):
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<blockquote>{_inline_md(line[2:])}</blockquote>")
        elif line.startswith("- "):
            if not in_list:
                html_lines.append("<ul>")
                in_list = True
            html_lines.append(f"<li>{_inline_md(line[2:])}</li>")
        else:
            if in_list:
                html_lines.append("</ul>")
                in_list = False
            html_lines.append(f"<p>{_inline_md(line)}</p>")
    if in_list:
        html_lines.append("</ul>")
    return "\n".join(html_lines)


def _inline_md(text: str) -> str:
    """渲染少量行内 Markdown。"""
    escaped = _e(text)
    parts = escaped.split("**")
    if len(parts) < 3:
        return escaped
    rendered = []
    for index, part in enumerate(parts):
        rendered.append(f"<strong>{part}</strong>" if index % 2 else part)
    return "".join(rendered)


def _records(df: pd.DataFrame | None) -> list[dict[str, Any]]:
    """DataFrame 转 JSON 记录，清理 NaN。"""
    if df is None or df.empty:
        return []
    clean = df.copy()
    clean = clean.replace({pd.NA: None})
    clean = clean.where(pd.notnull(clean), None)
    return json.loads(clean.to_json(orient="records", force_ascii=False))


def _clean_text(content: str) -> str:
    """写静态文件前去掉行尾空白。"""
    return "\n".join(line.rstrip() for line in content.splitlines()) + "\n"


def _build_data_basis(
    report_date: str,
    market_temperature: dict[str, Any],
    sector_df: pd.DataFrame | None,
    leader_df: pd.DataFrame | None,
) -> dict[str, str]:
    """生成页面和 JSON 可追溯的数据口径说明。"""
    metrics = market_temperature.get("metrics", {})
    data_date = report_date
    if leader_df is not None and not leader_df.empty and "last_trade_date" in leader_df.columns:
        dates = [item for item in leader_df["last_trade_date"].dropna().astype(str).tolist() if item]
        if dates:
            data_date = max(dates)
    price_basis = _unique_text(leader_df, "price_basis") or "不复权"
    ma_basis = _unique_text(leader_df, "ma_basis") or price_basis
    fund_basis = _unique_text(sector_df, "flow_score_label") or "成交活跃度代理评分"
    sample_note = str(metrics.get("sample_note", "全市场样本"))
    return {
        "data_date": data_date,
        "stock_pool_scope": (
            f"{sample_note}；龙头池来自强势行业/概念成分股，按股票代码去重并合并多个主线。"
        ),
        "price_basis": f"{price_basis} 最新日K收盘价；均线口径：{ma_basis}；实时行情仅用于3%偏差校验。",
        "fund_basis": f"{fund_basis}；真实资金流不可用时不写资金流入，使用成交活跃度代理评分。",
    }


def _unique_text(df: pd.DataFrame | None, column: str) -> str:
    """提取 DataFrame 某列的非空唯一文本。"""
    if df is None or df.empty or column not in df.columns:
        return ""
    values = []
    for value in df[column].dropna().astype(str).tolist():
        text = value.strip()
        if text and text not in values:
            values.append(text)
    return " / ".join(values)


def _collect_sources(*frames: pd.DataFrame | None) -> list[str]:
    """收集数据源标签。"""
    sources: set[str] = set()
    for frame in frames:
        if frame is not None and not frame.empty and "data_source" in frame.columns:
            sources.update(str(item) for item in frame["data_source"].dropna().unique())
    return sorted(sources)


def _format_cell(col: str, value: Any) -> str:
    """表格单元格格式化。"""
    if value is None:
        return ""
    if col in {"change_pct", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "ret_60d", "up_ratio"}:
        multiplier = 100 if col == "up_ratio" and safe_float(value) <= 1 else 1
        return f"{safe_float(value) * multiplier:.2f}"
    if col in {
        "score",
        "leader_score",
        "research_priority_score",
        "sector_score",
        "price",
        "quote_price",
        "current_price",
        "board_price",
        "price_check_diff_pct",
        "amount_yi",
        "amount_ratio_20",
        "rank_stability_score",
        "flow_score",
        "close",
        "ma20",
        "ma5",
        "ma10",
        "ma60",
        "distance_ma20_pct",
    }:
        return _fmt(value, 2)
    return _e(value)


def _value_class(col: str, value: Any) -> str:
    """根据数值正负输出 CSS 类。"""
    if col not in {"change_pct", "ret_3d", "ret_5d", "ret_10d", "ret_20d", "ret_60d"}:
        return ""
    number = safe_float(value)
    if number > 0:
        return "positive"
    if number < 0:
        return "negative"
    return ""


def _fmt(value: Any, digits: int = 2) -> str:
    """数字格式化。"""
    return f"{safe_float(value):,.{digits}f}"


def _e(value: Any) -> str:
    """HTML 转义。"""
    return html.escape("" if value is None else str(value), quote=True)


def _empty_state() -> str:
    """空数据提示。"""
    return '<div class="empty">该数据源暂不可用</div>'


def _site_css() -> str:
    """站点样式。"""
    return """
:root {
  color-scheme: light;
  --bg: #f5f7fb;
  --panel: #ffffff;
  --ink: #202631;
  --muted: #667085;
  --line: #d9e0ea;
  --accent: #006d77;
  --accent-2: #8b5e34;
  --positive: #c0392b;
  --negative: #16794c;
  --shadow: 0 10px 30px rgba(24, 36, 52, 0.08);
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--bg);
  color: var(--ink);
  font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif;
  line-height: 1.5;
}
a { color: inherit; text-decoration: none; }
.topbar {
  position: sticky;
  top: 0;
  z-index: 10;
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 18px;
  padding: 14px 28px;
  background: rgba(255, 255, 255, 0.92);
  border-bottom: 1px solid var(--line);
  backdrop-filter: blur(12px);
}
.brand { font-weight: 800; letter-spacing: 0; }
nav { display: flex; gap: 6px; flex-wrap: wrap; }
nav a {
  padding: 7px 10px;
  border-radius: 6px;
  color: var(--muted);
  font-size: 14px;
}
nav a.active, nav a:hover { color: var(--ink); background: #e9eef5; }
main { width: min(1180px, calc(100vw - 32px)); margin: 28px auto 48px; }
.hero, .page-title, .panel, .detail-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  box-shadow: var(--shadow);
}
.hero {
  display: grid;
  grid-template-columns: 1fr auto;
  gap: 22px;
  align-items: center;
  padding: 30px;
}
.page-title { padding: 28px; margin-bottom: 18px; }
.eyebrow { margin: 0 0 8px; color: var(--accent); font-size: 13px; font-weight: 800; text-transform: uppercase; }
h1, h2, h3 { margin: 0; letter-spacing: 0; }
h1 { font-size: 34px; }
h2 { font-size: 20px; margin-bottom: 14px; }
h3 { font-size: 17px; }
.muted { color: var(--muted); margin: 8px 0 0; }
.temperature { text-align: right; min-width: 160px; }
.temperature-score { font-size: 52px; line-height: 1; font-weight: 850; color: var(--accent); }
.badge {
  display: inline-flex;
  align-items: center;
  min-height: 26px;
  padding: 3px 9px;
  border-radius: 999px;
  background: #e3f4f2;
  color: #075e63;
  font-weight: 700;
  font-size: 13px;
}
.summary-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 14px;
  margin: 18px 0;
}
.metric-card {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 18px;
}
.metric-value { font-size: 26px; font-weight: 820; }
.metric-label { color: var(--ink); margin-top: 4px; font-weight: 700; }
.metric-note { color: var(--muted); font-size: 13px; margin-top: 3px; }
.panel { padding: 20px; margin: 18px 0; }
.compact { margin: 0; }
.basis-grid {
  display: grid;
  grid-template-columns: repeat(4, 1fr);
  gap: 12px;
}
.basis-item {
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 12px;
  background: #fbfcfe;
}
.basis-item span {
  display: block;
  color: var(--muted);
  font-size: 12px;
  margin-bottom: 4px;
}
.basis-item strong {
  display: block;
  font-size: 14px;
  line-height: 1.45;
}
.subsection { margin-top: 16px; }
.subsection h3 { margin: 0 0 10px; }
.three-columns {
  display: grid;
  grid-template-columns: repeat(3, 1fr);
  gap: 14px;
  margin: 18px 0;
}
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; min-width: 760px; }
th, td {
  border-bottom: 1px solid var(--line);
  padding: 10px 9px;
  text-align: left;
  vertical-align: top;
  font-size: 14px;
}
th { color: var(--muted); font-weight: 800; background: #f8fafc; }
tr:hover td { background: #fbfcfe; }
.positive { color: var(--positive); font-weight: 750; }
.negative { color: var(--negative); font-weight: 750; }
.mini-list, .archive-list { list-style: none; padding: 0; margin: 0; }
.mini-list li {
  display: flex;
  justify-content: space-between;
  gap: 12px;
  padding: 10px 0;
  border-bottom: 1px solid var(--line);
}
.mini-list li:last-child { border-bottom: 0; }
.sector-list, .stock-list {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 14px;
}
.detail-card { padding: 18px; }
.card-title {
  display: flex;
  justify-content: space-between;
  align-items: flex-start;
  gap: 12px;
  margin-bottom: 12px;
}
.card-title h3 span { color: var(--muted); font-size: 13px; font-weight: 600; }
.score-line { height: 8px; background: #edf1f6; border-radius: 999px; overflow: hidden; margin-bottom: 14px; }
.score-line span { display: block; height: 100%; background: var(--accent); }
dl {
  display: grid;
  grid-template-columns: repeat(2, 1fr);
  gap: 12px;
  margin: 0;
}
dt { color: var(--muted); font-size: 12px; }
dd { margin: 2px 0 0; font-weight: 750; }
.markdown-body {
  background: var(--panel);
  border: 1px solid var(--line);
  border-radius: 8px;
  padding: 28px;
  box-shadow: var(--shadow);
}
.markdown-body h1 { font-size: 28px; margin-bottom: 16px; }
.markdown-body h2 { margin-top: 26px; }
.markdown-body blockquote {
  margin: 16px 0;
  padding: 12px 16px;
  border-left: 4px solid var(--accent);
  background: #eef7f6;
}
.markdown-body li { margin: 7px 0; }
.empty {
  padding: 18px;
  color: var(--muted);
  border: 1px dashed var(--line);
  border-radius: 8px;
  background: #fafbfd;
}
.warning {
  margin: 16px 0;
  padding: 12px 16px;
  border: 1px solid #e4a11b;
  border-radius: 8px;
  background: #fff8e6;
  color: #7a4b00;
  font-weight: 700;
}
.archive-list li { padding: 12px 0; border-bottom: 1px solid var(--line); }
footer {
  width: min(1180px, calc(100vw - 32px));
  margin: 0 auto 32px;
  display: flex;
  justify-content: space-between;
  gap: 14px;
  color: var(--muted);
  font-size: 13px;
}
footer a { color: var(--accent); font-weight: 700; }
@media (max-width: 800px) {
  .topbar { align-items: flex-start; flex-direction: column; padding: 12px 16px; }
  main { width: min(100vw - 20px, 1180px); margin-top: 14px; }
  .hero { grid-template-columns: 1fr; padding: 22px; }
  .temperature { text-align: left; }
  h1 { font-size: 28px; }
  .summary-grid, .three-columns, .sector-list, .stock-list { grid-template-columns: 1fr; }
  .basis-grid { grid-template-columns: 1fr; }
  .metric-value { font-size: 23px; }
  table { min-width: 0; }
  thead { display: none; }
  tr {
    display: block;
    padding: 10px 0;
    border-bottom: 1px solid var(--line);
  }
  td {
    display: grid;
    grid-template-columns: 92px 1fr;
    gap: 10px;
    border-bottom: 0;
    padding: 6px 4px;
  }
  td::before {
    content: attr(data-label);
    color: var(--muted);
    font-weight: 700;
  }
  dl { grid-template-columns: 1fr; }
  footer { flex-direction: column; }
}
"""
