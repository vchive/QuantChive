# Research: 多源观测数据模型（spec004 决策依据）

依据：spec003 落地 + 两轮实测探测（数据源调研 Workflow + 资金流源探测 Workflow，均一手联网核实）。

## D1 · source_code 进业务键（根因修复）

- **Decision**：`observation` 唯一键加 `source_code`。
- **Rationale**：实测同股同日 baostock 价与 sina 流撞键、后写覆盖前者（140,799 行受影响）。source_code 本是观测身份维度。
- **Alternatives**：用不同 minute_slot 隔离（治标、非干净多源模型，否决）；跨源列合并（复杂、series 单指标无需，否决）。

## D2 · 按指标解析权威源（查询层）

- **Decision**：`metric_source.py` 提供 SortField→有序 source_code 优先级；series_daily 逐日取首个非空权威源行。
- **Rationale**：series API 单指标，无需列合并。资金流 sina 优先（实测 8 年五档不限流），价 baostock 优先（复权深）。
- **源能力实测**（探测 Workflow 一手核实）：

| source_code | 资金流五档 | 价/涨跌 | 量/额 | 历史深 | 限流 |
|---|---|---|---|---|---|
| sina_flow | ✅ r0+r1=主力 | ✅ | — | ~8年 | 无 |
| baostock | ❌ | ✅ 复权 | ✅ | 至1990 | 无 |
| eastmoney | ✅ | ✅ | ✅ | ~120日 | 有(2025-04起) |
| ths_flow | 仅主力(当日快照) | ✅ | — | 当日 | 独立后端 |

- **关键否证**（探测确认）：个股逐日历史资金流，akshare 封装内**唯一**源是东财 push2his（限流）；同花顺只当日横截面快照；**新浪 `MoneyFlow.ssl_qsfx_lscjfb` 是唯一独立于东财、能返个股逐日历史资金流的免费源**（akshare 未封装，直连 HTTP）。

## D3 · 排行/大盘限定实时源

- **Decision**：ranking_snapshot/series/stock_rows_for/latest_slot/get_point/latest_market_point 加 `source_code` 参数；实时路径限 `REALTIME_SOURCE='eastmoney'`，历史下钻限指标主源。
- **Rationale**：多源共存后未过滤源的查询一股多行 → 排行重复、大盘重复求和、component_hash 含重复符号。research 探测逐方法核实了 6 处「必须改」。

## D4 · 命名空间对齐（易错点）

- **Decision**：metric_source 优先级用**存储层实际 `observation.source_code` 值**（`eastmoney`/`baostock`/`sina_flow`/`ths_flow`），非 routing 键（`money_flow`/`price_hist`/`realtime`）或 registry 键（`eastmoney_stock` 等）。
- **Rationale**：三套命名不同（探测 agent 明确警示）；查询解析须匹配 upsert 时写入的 source_code。

## D5 · 无向后兼容迁移（全新项目）

- **Decision**：重建 schema、复制维表（subject/membership/calendar）、重跑 baostock+sina 回填。
- **Rationale**：用户明确"全新项目不需兼容"；重灌 baostock~15min、sina~25min，比写迁移代码更干净。

## 保留 spec003 教训产物

- 采集互斥锁（`cli/collect_lock.py`）：fcntl 排他锁防并发写坏 SQLite 单写者——实测第二个 collect 被拒。
- 回填读回校验：写落库确认后才记 coverage_range，防孤儿区间→永久跳过（本轮真实 bug）。
