# Feature Specification: 多源观测数据模型（source_code 进观测身份）

**Feature Branch**: `004-multisource-observation`

**Created**: 2026-07-04

**Status**: Implemented

**Input**: spec003 多源落地后暴露的核心数据模型缺陷（见 Context）。用户要求架构完备、不考虑向后兼容（全新项目）。

## 背景

QuantChive 通用市场观测平台（主体×指标×时序）。spec003 引入多源（baostock 价量、新浪资金流、同花顺、东财）后，`observation` 表的唯一业务键 `(subject_id, trade_date, value_type, minute_slot)` **不含 `source_code`**。后果：同股、同日、同 `daily_final/EOD`，baostock 价格行与新浪资金流行**撞同一键**，后写 upsert 覆盖前者、只留一个源。实测 140,799 行 baostock 数据会被同股同日的新浪资金流全部撞键覆盖——**同一主体同一天的多源观测无法共存**。

`source_code` 本应是观测身份的一部分（"哪个源在何时对哪个主体的哪个指标的观测"）。本 spec 把 `source_code` 提升为观测的一等身份维度，并在查询层引入「按指标解析权威源」，让多源真正共存。

## Clarifications

### Session 2026-07-04

- Q: 资金流历史（main_net 等）默认优先取哪个源？ → A: **新浪优先**（8年深、五档齐、不限流；东财备、同花顺再备）。
- Q: 改数据模型时现有已回填数据怎么处理？ → A: **实现完重建库**（全新项目，无向后兼容迁移，重跑回填）。

## User Scenarios & Testing

### User Story 1 - 多源观测共存 (Priority: P1)

作为平台，同一只股同一交易日应能同时保存来自不同数据源的观测（baostock 的复权价 + 新浪的五档资金流 + 东财的实时），互不覆盖。

**Independent Test**: 同 `(subject, trade_date, value_type, minute_slot)` 用 baostock 与 sina_flow 两次 upsert → 两行共存、各自数据完整、observation_id 不同。

**Acceptance Scenarios**:
1. **Given** 中国船舶某日, **When** baostock 写价格行 + sina_flow 写资金流行, **Then** 两行共存不覆盖。
2. **Given** 同源同键重写, **When** 再次 upsert, **Then** 幂等 UPDATE（仍一行）。

### User Story 2 - 按指标解析权威源的历史时序 (Priority: P1)

作为用户，查某主体某指标的历史时序，系统应按指标优先级逐日取权威源的值——查 `main_net` 得新浪五档、查 `price` 得 baostock 复权价，无重复日点、无源串味。

**Independent Test**: 一股多源同日 → `series_daily(main_net sources)` 逐日取 sina 行、`(price sources)` 取 baostock 行，天数不翻倍。

**Acceptance Scenarios**:
1. **Given** 一股多源同 3 日, **When** 查 main_net 日线, **Then** 3 点、均来自 sina_flow、main_net 非空。
2. **Given** 首选源某日缺, **When** 查该指标, **Then** 回落次优源的非空值。

### User Story 3 - 排行/大盘不因多源重复 (Priority: P2)

作为平台，排行与大盘求和是实时量，多源共存后必须显式限定源，避免一股多源行被重复排入榜或重复求和。

**Independent Test**: 一股在 LATEST 有东财+另一源两行 → 排行/求和限定实时源后每股只计一次。

**Acceptance Scenarios**:
1. **Given** 一股 LATEST 双源, **When** 大盘求和限定 eastmoney, **Then** 该股只计一次、component_hash 无重复符号。
2. **Given** 排行限定实时源, **When** 出榜, **Then** 每主体一次、无重复。

### Edge Cases

- 首选源缺某日 → 逐日回落次优源（非整源回落）。
- 某源只填部分列（baostock 无资金流）→ 查资金流时该源行的资金流列为 NULL，逐日选源跳过它、取有值的源。
- 大盘 subject 按源建 → 求和限定源，防跨源重复。

## Requirements

- **FR-001**: `observation` 唯一业务键必须含 `source_code`：`(subject_id, source_code, trade_date, value_type, minute_slot)`。
- **FR-002**: upsert 的 ON CONFLICT 目标与幂等回查必须含 `source_code`。
- **FR-003**: `ObservationRow`/`_OBS_COLS` 必须携带 `source_code`（供解析/审计/展示）。
- **FR-004**: 必须提供「指标 → 有序源优先级」解析（`metric_source.py`），用存储层实际 source_code 值。
- **FR-005**: 历史日线 series 必须逐 `trade_date` 取首个「该指标列非空」的权威源行（无重复日点）。
- **FR-006**: 排行/盘中/大盘求和/大盘点查询必须可限定 `source_code`；实时路径限定实时源。
- **FR-007**: 多源金额归一后保持整数最小单位（禁 float，宪章 III）。
- **FR-008**: 无向后兼容迁移；重建库重跑回填。

### Key Entities

- **观测（observation）**：现新增 `source_code` 为身份维度。一 (subject×date×metric-slot) 可多行（每源一行）。
- **源优先级（metric_source）**：指标 → 有序 source_code 列表 + 判非空列 + 实时源常量。

## Success Criteria

- **SC-001**: 同键不同源两次写 → 两行共存不覆盖。
- **SC-002**: 一股多源同日 → 按指标取权威源、天数不翻倍。
- **SC-003**: 排行/大盘一股多源 → 不重复计数、hash 无重复符号。
- **SC-004**: 中国船舶回填 baostock 价 + 新浪流 → main_net 时序出新浪五档非空、price 出 baostock 价，无串味。
- **SC-005**: 全量 `uv run pytest` 全绿。

## Assumptions

- 存储层实际 `observation.source_code` 值为 `eastmoney`/`baostock`/`sina_flow`/`ths_flow`（非 routing 键）。
- 资金流历史 sina 优先（实测 8 年、五档、不限流）。
- series API 单指标 → 无需跨源列合并，逐指标解析已足。
- 排行/盘中/大盘现仅东财写实时 → 限定 `REALTIME_SOURCE='eastmoney'`。

## 范围外

- 跨源列级合并（一行混多源列）。
- 新增数据源（tushare/代理池）。
- 向后兼容迁移代码。
