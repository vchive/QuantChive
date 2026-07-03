# Contract: 采集调度

**Feature**: 002 | 决策 D2/D3/D4 见 [research.md](../research.md)

## 三频率错峰 JOB（相位 0s/20s/40s，仅交易日 09:30-15:00）

```
minute_1min(相位0):  板块991 + 热点股Top-N → intraday_snapshot, granularity=1min
stock_5min(相位20):  全市场个股5535 → intraday_latest(LATEST覆盖,喂大盘) + 5min边界额外intraday_snapshot
etf_5min(相位40):    ETF1521 → intraday_snapshot, granularity=5min
```

## collect 流程

- 每 JOB 一 ingestion_run 三态审计；批次统一 batch_slot（**禁重取 clock.now()**）；逐主体 try/except 单点失败不中断，断点=缺行不造假。
- 幂等写入 `INSERT ... ON CONFLICT(uq_observation) DO UPDATE`。
- Sector/subject 身份解析：按 (asset_class, subject_kind, source, source_symbol) upsert 得 subject_id，新主体记审计。

## 大盘求和子步（D2）

`stock_5min` 采完后 `market_aggregate.compute`：用 **Python int 求和**个股 main_net（禁 float），覆盖率 `constituent_count/expected ≥ 95%` 写大盘 observation（is_derived=1, run_type=market_aggregate, 记 constituent_count/expected_count/成分哈希）；<95% **不写行**（宁缺勿假）。复用个股那次的 batch_slot。

## 翻页限流

- page_size=100（东财硬顶），翻页上限 **ceil(total/100)+缓冲**（个股 56 页），页间 sleep(0.1-0.3s) 抖动。
- 双域名 push2delay → push2 fallback；指数退避 backoff_base=3 cap=20（沿用 spec001）；200+空/`-` 不重试。串行/低并发 ≤2。

## 失败断点 / 收盘补全

- DataSourceError(rate_limited) → 该 JOB 指数退避（上限 10min），不阻塞其他 JOB。
- 15:05 触发 eod_backfill 全 subject 拉 daily_final(minute_slot='EOD')；**斩断 RunType(value_type.value) 跨枚举直转，改显式映射**（daily_final→eod_backfill，intraday_latest→无 RunType）。

## 保留：清理 + 分级降采（用户决策）

- **retention_downsample**（每日）：把 7 天前的分钟数据聚合成小时点（granularity='hourly', value_type='hourly_rollup', minute_slot='HH:00'），删原细粒度行（CASCADE 带走 observation_metric）。口径：资金流取小时末累计值、价格取小时收盘、量取小时累计。
- **retention_cleanup**（每日）：删 trade_date < today-30（配置化）。
- LATEST 行靠 trade_date 清理（跨 trade_date 不覆盖）。

## 时效承诺按主体分层（FR-011 再解释）

大盘/板块/热点股 1-3min 达标；全市场个股/ETF 5min（≤10min 上限）。存储约束下的自觉工程取舍，非隐性回退。
