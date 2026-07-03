# Research & Architecture Decisions: 通用市场观测平台

**Feature**: 002-market-observation-platform | **Date**: 2026-07-03

本文档由 9 智能体并行设计（数据模型/迁移/多品种适配/分层存储）+ 对抗审查收敛。对抗审查抓出并裁决的高危项：大盘频率悖论、板块回退风险、翻页少算大盘、聚合部分覆盖冒充、series 混 LATEST 假点、RunType 跨枚举崩、net_amount 恒等陷阱、parent_subject_id 过度设计、空壳 ActionService、provenance 谎报。

## 关键架构决策（D1-D10）

### D1 · 通用模型结构：subject + observation + observation_metric 三表混合
- **Decision**：`sector`→`subject`（统一大盘/板块/个股/ETF/基金）、`sector_money_flow`→`observation`（核心指标固定列）、新增 `observation_metric`（稀疏指标 KV 挂 observation_id）。**删 parent_subject_id 自引用列**，归属走 subject_membership。
- **Rationale**：混合模型是 FR-004 要求；parent_subject_id 被四审一致判过度设计（大盘=个股求和非板块求和，单值父链诱导错误聚合；个股属多概念板块无法单值父表达）。层级用 subject.level 枚举 + Service 规则表达。

### D2 · 大盘求和：采集层聚合 + 95% 覆盖率门禁
- **Decision**：大盘总量在 ingest 采集层求和（非查询时算），写一条 is_derived=1 observation。必须：①复用个股那次采集的 batch_slot（禁重取 clock.now()）；②记 constituent_count/expected_count 真列；③覆盖率<95% 该点**不落**（宁缺勿假，不冒充全市场）；④成分集合哈希落审计供重放。
- **Rationale**：查询时 SUM 依赖"当次库里恰好有哪些个股"，断点静默少算破坏 SC-007 可复现。写残缺大盘点污染 US1 首屏方向判断。

### D3 · 频率对齐：大盘=个股求和粒度=5min，板块独立源生 1min
- **Decision**：板块（991）走东财板级直给 f62 **独立 1min**；大盘（个股求和）粒度 **=个股 5min**，observed_at 如实记底层时刻，**无"1min 大盘"**（否则空档分钟用 5min 前值=前视）。
- **Rationale**："大盘=个股求和"与"个股 5min"物理上不可兼得 1min 大盘。裁定大盘随个股粒度，诚实反映底层时刻。

### D4 · 存储量分层策略【核心必解，含用户分级保留决策】

**采集频率分层**（`subject.collect_tier` 列驱动）：

| tier | 主体 | 数量 | 盘中频率 |
|---|---|---|---|
| minute | 板块(行业+概念) | 991 | 1min |
| minute | 热点股 Top-N(默认100) | ~100 | 1min |
| coarse | 全市场个股 | 5535 | 5min + LATEST覆盖 + EOD |
| coarse | ETF | 1521 | 5min + EOD |
| derived | 大盘 | 1 | 5min(对齐个股求和) |
| on_demand | 开放式基金 | — | 不采不入库,按需查 |

**个股三管齐下压缩**：①LATEST 覆盖写（minute_slot='LATEST' 当日每只 1 行被覆盖，仅 5535 行/日不累积，供大盘求和+当前排行）；②5min 稀疏历史（供个股趋势曲线）；③热点 Top-N 提频 1min。

**保留期分级降采（用户决策，叠加在采集频率之上）**：
- **近 7 天**：全主体保留原始分钟/5min 粒度（细节全在）。
- **8-30 天**：降采为**小时级**——每天由后台 retention_downsample 任务把 7 天前的分钟数据聚合成小时点、删原细粒度行。降采口径：资金流类取该小时**末值**（当日累计快照，取最后一点）、价格取小时**收盘价**、成交量取小时**累计**。
- **效果实测估算**：全 1min 30天 14.2GB → 采集频率分层 2.5-4GB → **叠加保留降采后 30天窗口 ~1.5-2.5GB**。近 7 天细、远 30 天粗，兼顾细节与容量。

**FR-011 显式再解释**（已回写 spec）：「目标 1-3min」按主体分层——大盘/板块/热点股达标；全市场个股/ETF 为 5min（≤10min 上限）。这是存储约束下的自觉工程取舍，非隐性回退。SC-005「不降采」限定为"近 7 天核心主体"。

### D5 · 通用 Source：ObservationSource Protocol + 旧 SectorFlowSource 薄适配保留
- **Decision**：泛化 Protocol，参数化 fs（FetchSpec）；保留旧 Protocol 作 board 特例薄适配（迁移期不破坏适配器测试）。本期只覆盖 equity/fund/etf 已实测能力，不预写债/期编排（YAGNI，枚举预留即可）。

### D6 · spec001 迁移：扩展而非改名，保 sector_money_flow 表名列名
- **Decision**：新增 subject/observation/observation_metric 通用表承载 stock/etf/market；**保留 sector_money_flow/sector/sector_constituent** 给 board 特例继续用，现有测试不动；最后一步(Step7)一次性收敛 drop 旧表。旧业务数据 drop 重采（FR-024），参考表 keep。**不做 INSERT...SELECT 搬数**。
- **Rationale**："改列 vs 不破坏测试"不可兼得且宪章 IV 测试优先。扩展路线让 SC-010 板块不回退与宪章 IV 双保。

### D7 · 个股下钻：正向 clist 直连，as-of 历史成分架构预留
- **Decision**：板块内个股下钻用东财 clist `fs=b:BK{code}`（直连 push2delay，实测板块成员五档齐全）。subject_membership 的 as-of 历史成分**本期无真实数据**（个股→板块 slist 未拿通）。get_stocks_in_sector 对 as_of<today 且无成分时**抛结构化错误**（不静默用当前成分，否则前视）。US3 本期只支持当日/最近交易日下钻。

### D8 · ETF/基金适配
- **ETF**：clist fs=b:MK0021..0024 全采 1521，money_flow=NULL（无五档，能力自描述），价/量走固定列。**规模用 f21 流通市值入 KV 附表，命名 circ_mktcap 不叫 aum**（f21≠资产净值，不冒充）；真实 AUM 待补采份额字段。
- **开放式基金**：独立 FundLookupService（物理隔离，不持 dao 写句柄，不进调度），按需查 lsjz（**必带 Referer: fund.eastmoney.com**），净值 Decimal 字符串，返回带 note="not persisted"。

### D9 · 可视化：静态 HTML + 原生 fetch，能力驱动渲染
- **Decision**：单页栈式下钻（品种Tab→大盘卡片→板块双榜→板块内个股→个股详情），无框架无构建。能力驱动：读 capability.supported_metrics，不支持的指标不画列（灰字告知，FR-004/SC-004）；provenance 徽章；断点留空不连线；金额用后端 Decimal 字符串，前端零 float。

### D10 · 目录结构：见 plan.md。

## 已验证数据源事实（对抗审查期间 web 实测）

- 东财 push2delay clist 单接口按 fs 覆盖：全市场个股(5535)、行业(496)、概念(495)、ETF(1521)、板块成员(fs=b:BK{code} 五档齐全)。**单页硬顶 100**（需翻页，个股 56 页）。字段 f14名/f62主力/f66超大/f72大/f78中/f84小(元)/f2价/f3涨跌/f5量/f6额/f21流通市值。
- 开放式基金 api.fund.eastmoney.com/f10/lsjz（FSRQ/DWJZ/JZZZL），必带 Referer。
- 个股→板块归属 slist 未拿通 → 历史 as-of 下钻本期预留。

## 遗留风险与实现期需验证项

| # | 项 | 验证 |
|---|---|---|
| 1 | 个股→板块归属 slist 未通 | 本期 US3 走正向 clist 成员，as-of 历史预留 |
| 2 | 5535 只全采限流（56页×~0.4s串行≈33-90s/批） | 实测单批耗时；逼近 5min 窗口需调并发/页大小 |
| 3 | ETF 规模 f21=流通市值≠AUM | 实测是否返回份额字段；否则 circ_mktcap 不冒充 |
| 4 | 存储量 30 天实测（含降采后） | 采一周真数据实测行数/GB + VACUUM/checkpoint 耗时 |
| 5 | 东财单页真实上限（pz=500 可能被静默截 100） | 实测个股 pz=100 需 56 页；翻页上限动态化 |
| 6 | WAL 写并发（5min 批 7000+ upsert 单写者） | 分页每 100 只一 commit、busy_timeout、只读独立连接 |
| 7 | change_pct_bp 精度（东财 f3 小数位） | 实测 f3/换手率有效位，确认 ×1e4 基点无损 |
| 8 | 保留降采任务（用户决策）| 实测小时聚合口径正确性、降采后回看仍连续 |
