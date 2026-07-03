# QuantChive · 通用市场观测平台

观测**任意可交易品种**的**任意指标**的**时序变化**——核心抽象是「可观测主体 × 观测指标 × 时间序列」。资金流只是第一个指标、A股只是第一个品种。

- **主体分层下钻**：大盘 → 板块（行业/概念）→ 个体（个股/ETF）。
- **能力自描述**：不同主体/品种支持的指标不同，平台诚实按能力暴露（不支持时明确告知，绝不伪造）。
- **本期真实数据**：A股（大盘资金流求和 + 行业/概念板块五档 + 个股价量五档 + 板块内下钻）、基金/ETF（价/量/规模 proxy + 开放式基金按需净值）。债券/期货/商品/外汇架构预留。
- **agent-ready**：查询能力沉淀在人与未来 LangChain agent 共用的只读 Service 层，字段自描述、错误结构化。

## 技术栈

- Python 3.12 · **uv**（不用 pip，不手改 uv.lock）
- SQLite（WAL）· 单 worker（SQLite 单写者）
- FastAPI + Pydantic v2 · 东方财富直连（requests）

## 快速开始

```bash
# 安装 uv
curl -LsSf https://astral.sh/uv/install.sh | sh

# 同步依赖
uv sync

# 手动采集（不依赖 Web；供 cron / 冒烟）
uv run quantchive-collect --target sector      # 行业+概念板块五档
uv run quantchive-collect --target stock       # 全市场个股 + 大盘求和(≥95%覆盖率门禁)
uv run quantchive-collect --target etf         # 全 ETF 价/量/规模
uv run quantchive-collect --target members     # 板块→成分个股归属（下钻数据，991板块约几分钟）

# 历史日线资金流回填（真历史，回溯约120交易日）
uv run quantchive-collect --target backfill --scope sector --days 60   # 板块历史(快)
uv run quantchive-collect --target backfill --scope stock  --days 60   # 个股历史(5535只,较慢)

# 起 Web（下钻可视化 + 只读 API）
uv run uvicorn quantchive.app:app --reload
# 打开 http://127.0.0.1:8000 —— 品种Tab → 大盘卡片 → 板块双榜 → 板块内个股 → 主体时序(日线/分钟切换)

# 全量测试
uv run pytest
```

## 两种时序数据（重要）

| | 来源 | 深度 | 前端 |
|---|---|---|---|
| **日线历史** | 东财 fflow/daykline（`--target backfill`）| 回溯约 120 交易日的每日五档净额 | series 视图默认「日线历史」|
| **盘中分钟** | clist 快照轮询（调度器/反复 collect）| 只能**向前积累**——运行采集器之后才有 | series 视图「当日分钟」|

> 东财只提供**历史日线**，不提供**历史分钟资金流**。所以：过去几十天的日线曲线可回填得到；
> 某天盘中每分钟的资金流只能从今天开始采、明天才有"昨天全天"。

## 自动采集调度

生产环境设 `QUANTCHIVE_SCHEDULER_ENABLED=1`（或 settings `scheduler_enabled=True`），启动时拉起后台**单 worker** 三频率错峰采集（仅交易日 09:30–15:00）：

| JOB | 相位 | 频率 | 内容 |
|---|---|---|---|
| minute_1min | 0s | 1min | 板块 → intraday_snapshot |
| stock_5min | 20s | 5min | 全市场个股 → LATEST 覆盖 + 触发大盘求和 |
| etf_5min | 40s | 5min | ETF → intraday_snapshot |

盘后：15:05 EOD 补全、15:30 保留降采（7天前分钟→小时）+ 清理（删 30 天前）。

## 数值与时间铁律（宪章）

- **III 精度**：金额 INTEGER 分、价格 ×1e6 微元、百分比 ×100 基点、净值 ×1e6；求和用 Python int；**禁 float 参与货币计算**。JSON 金额序列化为字符串，前端零 float 排序。
- **II 无前视**：历史成分带生效日期（as-of 区间 `[from,to)`）；大盘 `observed_at` 如实记底层时刻；series 排除 LATEST 覆盖行；超保留窗返 `OUT_OF_WINDOW`。
- **I 可复现**：`_cal_today` 取真实自然日（非日历末点）；采集运行三态审计；大盘求和记成分哈希。
- **V 审计**：每次采集一条 `ingestion_run`（success/partial/failed/interrupted）。

## 只读 API

| 端点 | 说明 |
|---|---|
| `GET /api/market/overview` | 大盘资金全景（个股求和，覆盖率≥95%才有值） |
| `GET /api/sectors/ranking` | 板块资金流双榜（能力隔离：板块拒价格排序→422） |
| `GET /api/sectors/{id}/stocks` | 板块内个股下钻（as_of 历史无成分→422 无前视） |
| `GET /api/etf/ranking` | ETF 单榜（无五档，按涨跌幅） |
| `GET /api/funds/{code}/nav` | 开放式基金净值（旁路，不入库，note="not persisted"） |
| `GET /api/subjects/{id}/series` | 单主体时序（断点 null 不连线，超窗 422） |
| `GET /api/meta/capability` · `/api/meta/subjects` | 能力自描述 / 主体清单 |
| `GET /api/health` | 最新交易日 + 最近采集运行 + 各源状态 |

## 约束

- AI 约束：spec-kit（`.specify/` 宪章 + specs/）
- 换源不改上层（SC-008）：统一 `ObservationSource` Protocol + `RawObservation` 形状收口 + capability 自描述。
