# A-Share Trend Radar / A股主线雷达

本项目目标不是预测股票，而是建立一个每日主线研究工作流：

**市场温度 → 主线识别 → 生命周期判断 → 龙头股票池 → 风险回避 → 回测验证**

它是本地可运行、也可生成 GitHub Pages 静态快照的 A 股研究辅助网站，用开源公开数据源扫描市场状态、行业/概念主线、龙头观察池和风险方向。输出只用于研究辅助，不构成投资建议。

## 数据源原则

主数据源集成自 `a-stock-data` 的 `SKILL.md`：

- 行情、K线优先使用 mootdx、腾讯财经、百度股市通等直连端点。
- 东财仅用于其独有或适合批量的数据，并在 `src/data_provider.py` 内通过 `em_get()` 串行限流。
- AKShare 只作为 fallback，业务模块和页面不直接调用 AKShare。
- 所有外部数据源调用都集中在 `src/data_provider.py`，其他模块只消费标准化后的 DataFrame。
- 个股展示价格默认使用不复权日 K 收盘价，MA5/MA10/MA20/MA60 默认同样使用不复权口径；腾讯/板块行情只用于当前行情偏差校验。
- 若不复权 close 与当前行情参考价偏差超过 3%，股票池会标记“价格校验异常”。

## 安装

```bash
pip install -r requirements.txt
```

如果你的 macOS/Homebrew Python 提示 `externally-managed-environment`，推荐使用项目内虚拟环境：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
```

## 启动

```bash
streamlit run app.py
```

使用虚拟环境时：

```bash
.venv/bin/streamlit run app.py
```

启动后打开 Streamlit 输出的本地地址即可访问。

## 生成 GitHub Pages 静态站

如果只想手机打开每日快照，不需要一直运行 Streamlit，可以生成纯静态页面：

```bash
.venv/bin/python scripts/update_static_site.py --clean
```

输出目录为 `docs/`，适合 GitHub Pages 设置为 `main` 分支的 `/docs` 发布源。

每天手动更新流程：

```bash
.venv/bin/python scripts/update_static_site.py
git add .
git commit -m "Update radar snapshot YYYY-MM-DD"
git push
```

## 页面

- 首页：今日一句话、今日 Action、今日变化、市场温度、参与统计股票数、数据口径、今日最强主线、最近 10 日主线趋势、可研究候选/强主线回调观察/等待回调/高位观察不追/回避五栏股票池。
- 市场温度：上涨下跌家数、涨跌停估算、成交额、主要指数与涨跌幅分布。
- 主线雷达：行业板块、概念板块、短线情绪标签分层展示；情绪标签不参与主线排名。
- 龙头股票池：代表性股票池、K线、均线、成交额、趋势状态、观察状态、价格校验和失效条件；同一股票按代码去重，多主线合并显示，并拆分为“可研究候选”“强主线回调观察”“等待回调”“高位观察/不追”“回避”。
- 日报：自动生成《A 股主线操作系统日报》Markdown，按 3 分钟阅读结构输出今日一句话、Action、变化、Top 主线解释、股票池、风险池和下个交易日观察点。
- 策略回测：用当前主线/股票池和历史 K 线验证主线轮动规则，输出收益曲线、回撤、交易明细、年度收益和指数对比。
- 主线生命周期：显示启动期、主升期、高潮期、分歧期、退潮期、修复期，以及机会分、风险分、信心分、解释和当前建议。
- 行业轮动：每天记录 Top10 主线，追踪连续上榜天数、排名变化、分数变化、生命周期变化和可能接力方向。

## V2 研究系统说明

V2 的目标是把结果从“展示当前强弱”推进到“可被历史数据检验的规则系统”：

- 生命周期规则集中在 `src/lifecycle.py`，阈值在 `config.py` 的 `LIFECYCLE_RULES`。
- 回测逻辑集中在 `src/backtest.py`，组合统计在 `src/portfolio.py`，基准指数在 `src/benchmark.py`。
- 行业轮动追踪集中在 `src/rotation.py`，Top10 主线快照保存到 SQLite。
- 回测交易记录保存到 `data/radar.db` 的 `backtest_runs` 和 `backtest_trades`。

回测策略 MVP：

- 每个交易日收盘后计算信号，下一交易日按配置价格成交，默认用收盘价。
- 市场温度阈值默认 50；历史市场温度暂用沪深300是否站上 MA20 代理。
- 选择综合评分 Top 3 主线，每个主线最多选择龙头评分最高的 1 只股票。
- 股票过滤：均线多头、站上 MA20、距 MA20 不超过 25%、非高位过热、非疑似连续一字板。
- 卖出条件：跌破 MA20、板块生命周期进入退潮期、跌出当日龙头信号、持仓超时仍未盈利、止损、止盈、移动止盈。

运行回测：

```bash
.venv/bin/streamlit run app.py
```

然后打开侧边栏页面“策略回测”，设置区间和参数后点击“运行回测”。

当前数据限制：

- MVP 回测使用当前雷达候选板块和股票池向历史回放，存在候选池幸存者偏差。
- 历史板块成分和真实历史资金流暂未完整落库，部分历史信号使用板块 K 线和成交额代理。
- 历史市场温度暂用沪深300趋势代理，不等同于真实逐日市场温度。
- 回测结果用于验证规则稳定性，不代表未来收益。

## 每日操作系统与自检

当前版本把首页重构为四个问题：

- 今天市场处于什么状态？
- 今天资金/热度正在流向哪里？
- 今天应该重点研究什么？
- 今天应该回避什么？

每日静态构建会保存主线操作快照到 SQLite，字段包括日期、主线名称、分层、综合分、机会分、风险分、信心分、生命周期、Action、排名、核心股票和市场温度。连续运行后，首页和生命周期页会显示今日变化、最近 10 日趋势和当前阶段持续天数。

构建后会自动生成：

```text
docs/self_check_report.md
```

自检覆盖数据完整性、页面完整性和逻辑完整性，例如股票池数量、行业/概念数量、关键字段、今日一句话、今日 Action、历史不足提示、高位过热是否混入可研究候选、退潮期主线是否被错误列入重点研究等。

## 工程结构

```text
a_share_trend_radar/
  app.py
  requirements.txt
  README.md
  config.py
  data/
    cache/
    radar.db
  src/
    data_provider.py
    indicators.py
    scoring.py
    sector_radar.py
    stock_radar.py
    report_generator.py
    database.py
    utils.py
  pages/
    1_市场温度.py
    2_主线雷达.py
    3_龙头股票池.py
    4_日报.py
```

## 说明

首次运行会请求公开接口，板块扫描会按东财限流规则串行执行，因此可能需要几十秒。后续命中文件缓存后会明显加快。若某个端点失败，程序会记录日志到 `data/cache/radar.log`，页面显示“该数据源暂不可用”，不会因为单个数据源异常崩溃。
