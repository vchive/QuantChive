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

# 历史行情回填（baostock，独立于东财限流，回溯多年）
uv run quantchive-collect --target backfill --source baostock --scope stock --days 60  # 价/量/额历史
# 历史资金流回填（东财 fflow，受限流；节流缓解）
uv run quantchive-collect --target backfill --source eastmoney --metric money_flow --scope stock --days 60

# 起 Web（下钻可视化 + 只读 API）
uv run uvicorn quantchive.app:app --reload
# 打开 http://127.0.0.1:8000 —— 品种Tab → 大盘卡片 → 板块双榜 → 板块内个股 → 主体时序(日线/分钟切换)

# 全量测试
uv run pytest
```

## 数据层韧性（spec003 · 多源可插拔 + 限流治理 + 冷热分离 + 缓存）

东财自 2025-04 对 IP 限频；**换库不解决**（akshare/efinance 底层同为 push2his）。出路是治理 + 分流：

| 能力 | 做法 |
|---|---|
| **限流治理** | 翻页页间随机节流（0.5~1.5s）+ 退避加随机抖动 + requests-cache 透明缓存（历史长TTL/实时不缓存）|
| **零静默降级** | 请求全量却只回极少条 → 识别为限流降级、记 `ingestion_run.degraded`、拒绝落残缺数据（宁缺勿假）|
| **冷热分离** | 历史行情走 **baostock**（自有 API、无 IP 限流、有复权、深至 1990）——彻底离开东财高频路径 |
| **增量补缺** | `coverage_range` 记「主体×指标已存区间」，二次回填仅补缺口、零冗余请求 |
| **多源故障切换** | 资金流 东财(主)/**同花顺 10jqka**(备，独立后端) 双活；主源限流自动切备源、审计实际用源 |
| **采集纪律** | 单写者 + 请求间节流 + 失败标的分轮重试 + 完整性校验重取 |

> 换源/加源不改上层（`ObservationSource`/`HistorySource` Protocol + 选源工厂）。同花顺 `hexin-v` 头依赖 JS 生成，首用需小流量验证（验不过降级预留，不阻塞）。

## 两种时序数据（重要）

| | 来源 | 深度 | 前端 |
|---|---|---|---|
| **日线历史** | baostock 价量历史 / 东财 fflow 资金流（`--target backfill`）| baostock 回溯多年、东财约 120 交易日 | series 视图默认「日线历史」|
| **盘中分钟** | clist 快照轮询（调度器/反复 collect）| 只能**向前积累**——运行采集器之后才有 | series 视图「当日分钟」|

> baostock 提供历史**价/量/额**（不受东财限流）；历史**资金流**由东财 fflow 或同花顺提供。
> 盘中每分钟资金流只能从今天开始采、明天才有"昨天全天"。

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
