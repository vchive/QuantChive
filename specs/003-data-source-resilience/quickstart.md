# Quickstart: 数据层韧性重构 — 验证指南

**Feature**: 003-data-source-resilience | 契约见 [contracts/](./contracts/)

端到端验证场景，证明四层能力落地。全部**可 mock 不联网**（宪章 IV），真实联网验证单列。

## 前置

```bash
uv sync                      # 含新增 baostock / requests-cache
uv run pytest                # 基线全绿（现 144 + 新增）
```

## 场景 1 · P0 限流治理（US1，可 mock）

```bash
uv run pytest tests/unit/test_http_client.py -q
```
**预期**：
- 翻页采集相邻请求间有 sleep（注入计数 sleep 断言被调用、次数=页数-1）。
- 退避含随机抖动（注入固定 rng 断言退避 = 基数 + 抖动项）。
- historical 端点二次请求命中缓存（注入内存 CachedSession + 计数 http_get，断言二次不打真实请求）。
- **零静默降级**：`is_degraded(got=1, expected_min=30)` == True → 采集拒绝落库、ingestion_run.degraded=1。

## 场景 2 · P1 历史回填 + 增量补缺（US2，可 mock）

```bash
uv run pytest tests/integration/test_baostock_backfill.py -q
```
**预期**：
- 注入 fake baostock 返回 60 日 → 写 daily_final 观测 + coverage_range 记 [start,end]。
- 二次回填同区间 → 缺口为空 → 零请求、无重复行（SC-003）。
- 扩窗回填 → 只请求新缺口天。
- 单主体失败隔离（SC 断点=缺行）。

## 场景 3 · P2 多源主备切换 + 换源不改上层（US3，可 mock）

```bash
uv run pytest tests/integration/test_source_failover.py tests/contract/test_source_registry.py -q
```
**预期**：
- 主源（fake 抛 rate_limited）→ 自动切备源产出 → ingestion_run.used_source_code=备源（US3 AC1）。
- 全新 fake 源仅实现 Protocol → 上层排行/采集零改动跑通（SC-005，换源不改上层）。
- 缺失源 → 工厂返回 no-op 兜底、不崩、记 warning（US3 AC3）。
- 归一层：两个源同主体同指标 → 金额标度一致（SC-007）。

## 场景 4 · P3 采集纪律（US4，可 mock）

```bash
uv run pytest tests/unit/test_collector_base.py -q
```
**预期**：失败标的分轮重取、过短数据判未取全下轮重取、max_workers=1 单写者。

## 真实联网验证（限流冷却后手动，非 CI）

> ⚠️ 先确认东财 IP 已冷却（歇够时间），且小流量试探。

```bash
# 1) 同花顺 hexin-v 头验证（Phase C 首步，D6）——通过才启用备源
uv run python -c "import akshare as ak; print(ak.stock_fund_flow_individual(symbol='即时').head())"

# 2) baostock 历史回填（独立于东财限流，应稳定）
uv run quantchive-collect --target backfill --source baostock --scope stock --days 60

# 3) 起服务看历史时序有深度
uv run uvicorn quantchive.app:app --reload
# 个股时序「日线历史」→ 60 天真曲线（不再只有今天）
```

**通过标准**：
- baostock 回填不受东财限流影响、时序图有真实深度（SC-002）。
- 二次 collect 网络请求量大幅下降（仅补缺，SC-003）。
- 高频采集不再打东财限流、无静默降级（SC-001）。
