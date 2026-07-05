# Quickstart: 板块资金流验证指南

**Feature**: 001-sector-money-flow | 前置 [plan.md](./plan.md) · [contracts/](./contracts/)

> 本文档是**验证/运行指南**，不含实现代码。实现细节在 tasks.md 与实现阶段。

## 前置

```bash
uv add akshare uvicorn pydantic-settings
uv add --dev pytest httpx
```

- Python 3.12（`.python-version` 已固定）
- 装完 akshare 后**首要动作**：跑一次实测锁定东财 rank 接口真实列名（见 research.md 待验证项 1），写入 `COLUMN_ALIASES`。

## 验证场景（对应 spec 用户故事与 SC）

### 场景 1 — 排行（US1 / SC-001）

```bash
uv run uvicorn quantchive.app:app        # 单 worker
curl 'http://localhost:8000/api/sectors/ranking?caliber=eastmoney&sector_type=industry&sort_by=main_net&top_n=10'
```

**预期**：返回 `SectorRankingResult`；`top_inflow`/`top_outflow` 各 ≤10 项，按主力净额分正负；金额字段是**字符串**；`total_sectors` 反映全量；打开 `http://localhost:8000/` 能在 5 秒内看到排行页。

### 场景 2 — 双口径切换与能力自描述（US2 / FR-003）

```bash
curl 'http://localhost:8000/api/calibers/ths'
curl 'http://localhost:8000/api/sectors/ranking?caliber=ths&sector_type=industry&sort_by=super_large_net'
```

**预期**：`CaliberMeta` 中 ths 的 `has_five_tier=false`、`available_sort_fields` 不含各单档；对 ths 请求 `super_large_net` 排序 → **422 `METRIC_NOT_SUPPORTED`**（不返回伪造 0 值）。

### 场景 3 — 分钟序列回看与断点（US3 / SC-004 / FR-017）

```bash
curl 'http://localhost:8000/api/sectors/半导体/series?caliber=eastmoney&trade_date=<保留窗口内日期>'
curl 'http://localhost:8000/api/sectors/半导体/series?caliber=eastmoney&trade_date=<8天前>'
```

**预期**：窗口内返回 `points[]`，采集失败时点 `net_amount=null`（断点，`gap_count>0`），前端曲线该处断开不连线；超 7 天窗口 → **422 `OUT_OF_RETENTION_WINDOW`**。

### 场景 4 — 采集幂等与审计（宪章 V / SC-005）

```bash
uv run quantchive-collect --provider eastmoney --kind intraday   # B 方案 CLI 入口
```

**预期**：同一分钟重跑不产生重复行（`uq_flow` 幂等）；`ingestion_run` 落一条记录含 `status`、`achieved_interval_sec`、`sectors_ok/failed`；单板块失败不影响其他板块入库。

### 场景 5 — 换源不改上层（SC-007）

用测试内的 fake source（实现 `SectorFlowSource` Protocol）替换 akshare，**不改 service/api** 跑通场景 1 排行查询。

### 场景 6 — 休市回退（FR-017）

非交易时段访问排行 → `provenance.is_stale=true` 且标注最近交易日，不冒充实时。

## 测试

```bash
uv run pytest tests/unit          # 单位换算(东财元/同花顺亿)/排序稳定性/断点/生效区间 as-of
uv run pytest tests/contract      # datasource 形状/DTO schema/Decimal 序列化为字符串/错误码
uv run pytest tests/integration   # api 端到端/collect_once 幂等/休市 is_stale
```

**关键断言**：
- 东财 `-1.411231e+08` 元 → `-14112310000` 分；同花顺 `1.23` 亿 → `12300000000` 分（单位标度按源，D1）。
- JSON 中金额是字符串。
- 排序结果稳定可复现（tiebreaker `sector_id ASC`）。
- as-of 成分查询不引入前视（宪章 II）。
