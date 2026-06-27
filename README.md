# A-Share Trend Radar / A股主线雷达

本项目是本地可运行的 A 股研究辅助网站，用开源公开数据源扫描市场温度、行业/概念主线和龙头观察池。输出只用于研究辅助，不构成投资建议。

## 数据源原则

主数据源集成自 `a-stock-data` 的 `SKILL.md`：

- 行情、K线优先使用 mootdx、腾讯财经、百度股市通等直连端点。
- 东财仅用于其独有或适合批量的数据，并在 `src/data_provider.py` 内通过 `em_get()` 串行限流。
- AKShare 只作为 fallback，业务模块和页面不直接调用 AKShare。
- 所有外部数据源调用都集中在 `src/data_provider.py`，其他模块只消费标准化后的 DataFrame。

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

- 首页：市场温度、参与统计股票数、今日最强主线、持续主线/短线热点/退潮板块、股票池。
- 市场温度：上涨下跌家数、涨跌停估算、成交额、主要指数与涨跌幅分布。
- 主线雷达：行业板块、概念板块、短线情绪标签分层展示；情绪标签不参与主线排名。
- 龙头股票池：代表性股票池、K线、均线、成交额、趋势状态、观察状态、价格校验和失效条件。
- 日报：自动生成《A股主线雷达日报》Markdown。

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
