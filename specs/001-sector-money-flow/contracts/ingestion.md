# Contract: 采集调度

**Feature**: 001-sector-money-flow | 决策 D3/D4/D5/D6 见 [research.md](../research.md)

## collect_once 签名 (`service/ingest_service.py`)

```python
async def collect_once(req: CollectRequest, *, source, flow_dao, run_dao,
                       sector_dao, clock) -> CollectResult
```

- **CollectRequest**：`provider, sector_types(默认 industry+concept, 不含 region), kind(intraday/eod), as_of=None(注入 clock), trade_date=None, target_interval_s, max_retries`
- **CollectResult**：`run_id, provider, trade_date, batch_slot, kind, status(success/partial/failed), rows_written, sectors_requested, achieved_interval_s, failures[], error|None`
- **函数体零 `fastapi`/`akshare` 符号**，全依赖注入（D4）。A 方案（调度器）与 B 方案（`cli/collect.py`）调同一函数。

### 批次与幂等

- **批次时间戳**：每次 `collect_once` 生成**一个** batch `minute_slot`（对齐到分钟，Asia/Shanghai），本批所有板块共用 → 跨板块同一时间轴对齐。
- **幂等**：写入 `INSERT ... ON CONFLICT(uq_flow) DO UPDATE`，同分钟重试/重跑不产生重复行。
- **Sector 身份解析**：按 `(source, sector_type, source_symbol)` upsert 得 `sector_id`，新板块记 `NEW_SECTOR` 审计。

## 调度触发 (`scheduler/loop.py`, D3)

- 随 lifespan 起 `create_task`；东财、同花顺**各一独立 Task 各持独立 AdaptiveInterval**。
- 优雅停止 `request_stop()` + `wait_for(15s)`，中断中的 run 标 `interrupted`。
- 每日 POST_CLOSE 分支：EOD 补全（当日一次幂等闸）→ 保留窗口清理（`retention_cleanup` 审计）。

## 交易时段判断 (`core/trading_calendar`)

- `SESSIONS = [(09:30,11:30),(13:00,15:00)]`（Asia/Shanghai），午休自动落 sleep。
- 交易日由 `trade_calendar` 表判定（**非「数据源返回空」猜测**）；`Clock` 契约返回 aware datetime。

## 重试/降频参数（per-provider）

| 参数 | 东财 | 同花顺 |
|---|---|---|
| read/connect 超时 | 8s / 4s | 10s / 4s |
| 重试次数 | 3 | 2 |
| 退避 base/cap | 3s/20s +0-1s 抖动 | 5s/30s +0-2s 抖动 |
| AdaptiveInterval MIN/TARGET/MAX | 60/60/600 | 120/120/600 |
| 限流退避 | 乘性放宽 ≤600s | 触发即跳 ≥240s |

- **AIMD**：`on_success` 加性收紧、`on_rate_limited` 乘性放宽（封顶 600s=10min 硬上限）、`on_soft_fail` 温和放宽。`achieved_interval_s` 落审计。
- akshare 同步调用走 `asyncio.to_thread`。退避随机源接受注入 RNG（可测）。

## 收盘补全流程（D5）

- **东财**：15:40（结算后）后调一次 `rank(indicator='今日')` 全量 → `value_type='daily_final'`, `minute_slot='EOD'`。
- **同花顺**：末次即时快照 → `daily_final` + provenance `is_approximate_final=True`。
- hist 当日行按 `日期==trade_date` **精确匹配**（未来历史回补用），取不到记 `daily_backfill_pending` 次日重试，**绝不 `iloc[-1]`**（无前视）。

## 断点策略（D6）

- 盘中单次失败 → 有限次重试 + 退避；仍失败**不写任何行**（断点=缺行）。
- 缺口可感知由 `ingestion_run`（`status='partial'/'failed'` + `failures` + 覆盖的 `minute_slot`）承载。
- 日终补全值走独立 `'EOD'` 行，**永不 update 盘中行**。

## 承诺按口径分别声明

- **SC-003（1-3min）**：东财主口径承诺；同花顺副口径尽力而为、颗粒更粗。
- **SC-009（每日日终确定值）**：东财保证；同花顺为末次快照近似（诚实标注）；地域本期不采不承诺。
