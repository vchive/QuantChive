---
description: "Task list for 004-multisource-observation"
---

# Tasks: 多源观测数据模型（source_code 进观测身份）

**Input**: [spec.md](./spec.md) · [plan.md](./plan.md) · [research.md](./research.md)

**Tests**: 含测试（宪章 IV）。全程每阶段 `uv run pytest` 全绿。

## Phase 1: source_code 进业务键

- [X] T001 `dao/schema.sql`：uq_observation 加 source_code；idx_obs_ranking 含 source_code
- [X] T002 `dao/observation_dao.py`：upsert ON CONFLICT + 幂等回查加 source_code
- [X] T003 `dao/observation_dao.py`：_OBS_COLS + ObservationRow 加 source_code 字段
- [X] T004 [P] `tests/unit/test_multisource_key.py`：同键不同源两行共存 + 同源同键幂等

## Phase 2: 按指标解析权威源

- [X] T005 `datasource/metric_source.py`（新）：METRIC_SOURCE_PRIORITY + resolve_metric_sources + metric_nonnull_column + REALTIME_SOURCE
- [X] T006 `dao/observation_dao.py`：series_daily 加 sources/nonnull_column，窗口函数逐日选权威源
- [X] T007 `service/query_service.py`：_daily_series 传 resolve_metric_sources(metric)
- [X] T008 [P] `tests/unit/test_series_source_resolution.py`：main_net→sina、price→baostock、回落、无重复日点

## Phase 3: 排行/盘中/大盘限定源

- [X] T009 `dao/observation_dao.py`：ranking_snapshot/series/stock_rows_for/latest_slot_for_subjects/get_point/latest_market_point 加 source_code 参数
- [X] T010 `service/query_service.py`：排行三处 + 盘中 series + 大盘点 传 REALTIME_SOURCE；历史下钻传指标主源
- [X] T011 `service/market_aggregate.py`：stock_rows_for 传 source_code（求和限定源）
- [X] T012 [P] `tests/unit/test_multisource_ranking.py`：一股多源 stock_rows_for/ranking 限定后不重复

## Phase 4: 接线 + 旧测适配

- [X] T013 旧测适配（test_market_aggregate ObservationRow 加 source_code）；全量 pytest 全绿

## Phase 5: 重建库 + 真实回填 + 验证

- [X] T014 备份旧库 + 重建 schema（新键）+ 复制维表（subject/membership/calendar）
- [X] T015 baostock 全量价格回填（锁保护，独立于东财限流）
- [X] T016 sina 全量资金流回填（baostock 完成后串行，锁保护）
- [X] T017 真跑验证：中国船舶 main_net 时序（sina 五档）+ price 时序（baostock）非空无串味；排行/大盘无重复

## Phase 6: 提交

- [ ] T018 提交 spec003 遗留（锁/读回校验/新浪源）+ spec004（多源模型）+ 全套 spec-kit 文档

## Notes

- 迁移铁律不适用（全新项目，重建库）。
- 每阶段 pytest 全绿；REALTIME_SOURCE='eastmoney' 与现有测试数据一致，旧测零改动（除 ObservationRow 位移）。
- source_code 参数默认 None（不过滤）保持 DAO 向后兼容，query_service 显式传源。
