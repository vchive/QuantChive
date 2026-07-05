# Research: 数据层韧性重构（Phase 0 技术决策）

**Feature**: 003-data-source-resilience | 依据 [spec.md](./spec.md) Clarifications + 数据源调研（29 智能体，22/24 一手来源复核）

格式：Decision / Rationale / Alternatives。所有决策已在 spec Clarifications 锁定或本文档裁定。

---

## D1 · 限流治理：页间 sleep + 退避 jitter（P0 直接病根）

- **Decision**：`_http_client` 翻页 while 循环内加 `sleep(random.uniform(page_sleep_min, page_sleep_max))`（默认 0.5~1.5s）；退避改 `min(base*2**attempt, cap) + random.uniform(0, jitter)`。参数经 settings 配置、`sleep` 可注入。
- **Rationale**：agent 实查确认现 `_em_client._fetch_all_pages` 翻页 2→56 页**页间零 sleep**、退避 `min(3*2**attempt,20)` 无 jitter——这是把东财打限流的直接原因。akshare `fetch_paginated_data`（utils/func.py L46-50）每翻页无条件 sleep(0.5~1.5)，`request_with_retry` 退避带 `random.uniform(0.5,1.5)`。代价约 +40s/全量，显著降封率。
- **Alternatives**：只加重试退避不加页间 sleep（不够——封禁在翻页密度）；固定 sleep 无随机（指纹易识别）。

## D2 · 透明 HTTP 缓存：requests-cache 注入 CachedSession（已定）

- **Decision**：`uv add requests-cache`；经现有可注入 `http_get` 注入 `CachedSession`，按端点 glob 差异化 TTL——`push2his`/`baostock` 历史&日终长 TTL（收盘不变，可 NEVER_EXPIRE 当日收盘后）、`push2delay` 实时快照短 TTL 或 `DO_NOT_CACHE`。测试注入 `backend='memory'` 不落盘。
- **Rationale**：客户端 `http_get` 天然可注入（宪章 IV），零侵入。历史/日终重复采集（多指标复用同一 clist）不再重复回源。requests-cache v1.3.2 成熟、`urls_expire_after` 支持 glob。
- **Alternatives**：自写文件/内存缓存（少依赖但要自理 TTL/失效/并发，重复造轮子）；全局 `install_cache()`（会与 akshare 争抢 requests.Session——**否决**，用显式 CachedSession 实例）。
- **坑**：改 TTL 不回溯已有条目（issue #762）→ 需 `reset_expiration` 或 clear；当日盘中数据别设长 TTL（Edge Case）。

## D3 · 限流降级识别：零静默降级（硬门 SC-001）

- **Decision**：采集/回填对「请求全量却只回极少条/过短」显式识别为限流降级——沿用 spec002 已实现的 `min_days_guard`（backfill）思路，泛化为**预期条数/长度校验**：实得 < 预期阈值时判降级，`written=False`、计入 `ingestion_run` 的降级标记、**拒绝落残缺数据**。
- **Rationale**：本项目已实测东财限流表现=静默返当日 1 条；spec002 backfill 已有 `min_days_guard` 守卫。零静默降级用 mock 精确可测（不依赖真实限流阈值），是 SC-001 唯一可复现的硬指标。
- **Alternatives**：设触发率百分比门（无官方基线、需真实联网压测、不可复现——否决）。

## D4 · 历史行情源：baostock（已定，冷热分离核心）

- **Decision**：`uv add baostock`；新 `baostock_src.py` 实现历史行情源（日/周/月/分钟 K + 复权），`bs.login()/logout()` 生命周期管理（空闲超时需重连）。**仅历史行情**——无资金流。
- **Rationale**：baostock 自有专用 API（非爬东财前端）、无已知 IP 限流、深至 1990、有前后复权、免 token。让历史彻底离开东财高频路径（调研一致确认，稳定性评「高」）。
- **Alternatives**：只用东财 fflow 历史（同限流，已被打冷却）；akshare stock_zh_a_hist（底层仍 push2his，不绕限流）。
- **边界**：baostock 无资金流→资金流历史仍走东财 fflow（缓存+节流缓解）或同花顺（D6）。login 态非线程安全→单 worker 下串行 OK。

## D5 · 已存区间表：coverage_range 驱动增量补缺（仿 vnpy BarOverview）

- **Decision**：新表 `coverage_range(subject_id, metric_kind, granularity, source_code, start_date, end_date, updated_at)`——记「主体×指标×粒度×源」已入库的连续区间。回填前查区间→只请求缺口；回填后合并更新区间。
- **Rationale**：spec FR-007/008 要求增量补缺、历史入库即权威。vnpy BarOverview 是成熟范本。二次回填仅补缺（SC-003）靠此表。
- **Alternatives**：每次全量回填（浪费请求、易触限流）；靠 observation 表 MIN/MAX(trade_date) 推断（无法区分「区间内有洞」vs「连续」，且跨 metric 混淆）。
- **无前视**：区间端点含左不含右；补缺只填缺口不覆盖已存（幂等）。

## D6 · 同花顺 10jqka 资金流备源（本期接入，风险前置）

- **Decision**：新 `ths_flow_src.py`，走 akshare `stock_fund_flow.py` 那组（`stock_fund_flow_individual/concept/industry`，打 `data.10jqka.com.cn`，独立于东财后端）。**接入第一步 = 小流量验证 hexin-v 头能否稳定生成**；验证通过则注册为资金流备源、否则降级为「预留接口不启用」并记审计。
- **Rationale**：同花顺是调研确认的**唯一真正独立于东财的资金流后端**，能与东财分流（本项目记忆「同花顺北向分钟接口已验证可用」是正向信号）。作备源而非主源，风险可控。
- **Alternatives**：不接、只东财（单点命运，spec 明确否决）；接为主源（hexin-v 稳定性未证，风险过高）。
- **风险缓解**：验证不过不阻塞其余能力（优雅降级）；小流量验证是 Phase C 第一个 task。

## D7 · 选源工厂 + 路由 + 归一（多源可插拔，仿 vnpy/adata）

- **Decision**：
  - `registry.py`：`get_source(source_id)` 工厂，未注册/缺失时返回 no-op 兜底源（不崩、记警告），仿 vnpy `get_datafeed`（鸭子类型基类兜底，非强制 ABC）。
  - `routing.py`：`{指标类型 → [优先源, 备用源]}` 配置（本期：资金流→[东财, 同花顺]、历史行情→[baostock]、实时→[东财]）；主源抛限流/失败→切备源，审计记实际用源。
  - `normalize.py`：把各源异构字段/单位/复权口径统一到内部约定（金额→整数分、量→手/股统一、复权口径标注）。
- **Rationale**：现有 `ObservationSource` Protocol + `source_id` seam 已就位，成本低。归一层是跨源拼接数值正确（SC-007）的保证。
- **Alternatives**：每源硬编码于 ingest（散乱、换源改上层，违 SC-008）；强制 ABC（misconfig 崩溃，vnpy 反例）。

## D8 · 采集基类固化限流纪律（仿 qlib BaseCollector，P3）

- **Decision**：`_collector_base.py` 抽象：`max_workers=1`（SQLite 单写者）、请求间 `delay`、失败标的进 `retry_map` 分轮有限重试（`max_rounds`）、`check_min_length` 完整性校验（过短判未取全下轮重取）。各源子类实现 `fetch_one(subject)`。
- **Rationale**：把散落的节流/重试/校验纪律沉淀成可复用基类，避免每源各写一套、某源写糙触发封禁。qlib BaseCollector 是成熟范本（文档明确采集设 max_workers=1）。
- **Alternatives**：每源自实现（重复、不一致）；用线程池高并发（SQLite 单写者冲突 + 加剧限流）。

---

## 依赖引入（走 uv add，宪章约束）

| 依赖 | 用途 | 免 token | 备注 |
|---|---|---|---|
| `baostock` | 历史行情回填（D4）| ✅ | login/logout 生命周期；无资金流 |
| `requests-cache` | 透明 HTTP 缓存（D2）| ✅ | 注入 CachedSession，勿全局 install |
| 同花顺 | 经现有 akshare，无新依赖（D6）| ✅ | 需验 hexin-v 头 |

## 诚实风险（写入 spec Assumptions/Edge Cases，实现时守住）

- 换库不绕限流已证——baostock 之所以有用是**独立后端**，非「换个封装」。
- 限流阈值无官方数字——节流预算按保守下界，SC-001 用零静默降级（可复现）而非触发率。
- 同花顺 hexin-v 依赖 JS 生成——Phase C 第一 task 小流量验证，验不过降级预留。
- baostock login 态/空闲超时——单 worker 串行 + 用前重连。
