# Contract: CLI（backfill / collect 扩展）

**Feature**: 003 | 决策 D4/D5/D8 见 [research.md](../research.md)

## `quantchive-collect` 扩展

沿用现有 `--target sector|stock|etf|members|backfill`，扩展 backfill 与新增源选项：

```
# 历史行情回填走 baostock（独立于东财限流，D4）
uv run quantchive-collect --target backfill --source baostock --scope stock --days 60
uv run quantchive-collect --target backfill --source baostock --scope sector --days 60

# 资金流历史回填走东财 fflow（现状）或同花顺备源（D6）
uv run quantchive-collect --target backfill --metric money_flow --scope stock --days 60
```

**参数契约**：
- `--source`：显式指定源 id（baostock/eastmoney/ths_flow）；缺省走路由主源。
- `--metric`：指标类别（price_hist / money_flow）；决定走 HistorySource 还是 fflow。
- `--days`：回填深度，默认对齐保留窗（30）。
- **增量**：回填前查 coverage_range 只补缺口（D5）；输出报告 `补缺 N 天 / 跳过已存 M 天`。
- **降级审计**：识别到限流降级 → 输出 `限流跳过 K`（沿用现状），degraded 标记入 ingestion_run。

## 行为契约（可 mock 测）

- `backfill --source baostock`：注入 fake baostock 模块 → 断言写 daily_final 观测 + 更新 coverage_range + 二次运行仅补缺（SC-002/003）。
- 单主体失败隔离（现状保持）：失败计入审计、其余正常（US2 AC4）。
- 主备切换：collect 时主源限流 → 审计 used_source_code=备源（US3 AC1）。

## 输出示例

```
backfill[baostock/stock]: 主体 5195/5535 · 日线行 311700 · 补缺 60 天 · 跳过已存 0 · 限流跳过 0 · 失败 340
```
