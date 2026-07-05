# Research: 资金档位博弈可视化 + 多源数据层（Phase 0 决策）

**Feature**: 005 | 依据 spec Clarifications + 三轮调研（可视化设计40条、GitHub先例16条核实、数据源实测13条核实）+ 5轮数据层讨论。格式 Decision/Rationale/Alternatives。

## D1 · gross 存储：4 档成交额列（个股）

- **Decision**：observation 加 `super_large_gross_cents / large_gross_cents / medium_gross_cents / small_gross_cents`（整数分）。不加主力 gross。加 CHECK"四档 gross 全有或全无"。
- **Rationale**：4档强共现（新浪总一起给）→ 宽列优于 EAV（与现有五档 net 一致，zvt 4.2k star 也宽列）。主力 gross=超大+大可推。gross+net 是新浪原生（入库零变换、可对账）。
- **Alternatives**：存流入流出8列（冗余，且非源原生形态）；EAV（4档共现不该稀疏化）。

## D2 · 流入流出 + 累计：后端算，整除恒整

- **Decision**：流入=`(gross+net)//2`、流出=`(gross-net)//2`（Python 整除）。累计主力净额=**全程绝对累计**（从主体最早日累加，后端 Decimal/int 求和算一次）。均查询时算、不落库。
- **Rationale**：数学上 gross+net=2×买入必偶、整除恒精确（宪章III）。全程累计=A/D线/CVD标准，线形稳定不随缩放变，背离识别基于它。前端只画不算（宪章III铁律）。
- **Alternatives**：窗口相对累计（随缩放重算、线形变，不如全程稳定，已由 clarify 否决）。

## D3 · 多源能力路由（复用现有 seam）

- **Decision**：复用 `ObservationSource` Protocol + 选源工厂 registry + `metric_source.py`。源声明能力（有无gross/历史深度/独立后端/is_active）。资金流四档gross：新浪(主)、百度(校验探针)、Tushare(预留is_active=0)；盘中净额：东财clist；净额兜底：东财。
- **Rationale**：spec003/004 已建可插拔 seam，扩展而非重造。"以能力路由"是调研架构建议，也与 metric_source 一脉相承。
- **Alternatives**：为每源硬编码（散乱，违 SC-008 换源不改上层）。

## D4 · 百度无头浏览器校验探针

- **Decision**：`baidu_flow_src.py`——Playwright 开一次浏览器打开 gushitong 页 → `page.evaluate` 内连续 `fetch` 多股（百度JS自动带 Acs-Token）→ 解析 fundFlowSpread 四档 turnoverIn/Out/net。**page 可注入**（测试传 fake page 返固定 JSON，不联网）。定位=校验探针：收盘后随机抽样几十只+轮换。
- **Rationale**：实测（scripts/probe_baidu.py）4/4成功；page.evaluate 绕签名（百度自己JS算token，签名变了也不影响）。逆向 Acs-Token 不划算（会变）。只当日快照+浏览器重→只做校验不做主力。
- **Alternatives**：逆向签名（脆、易失效）；requests直连（403，token不可复用）；富途（需本地网关，更重）。

## D5 · 三层存储降采

- **Decision**：分钟(1min,留7天) → 小时(hourly,7天~3月,**降采取该小时最后一个分钟点的累计快照**) → 日线(daily,放宽>3月)。降采/清理盘后调度器每天一次。
- **Rationale**：净额/gross 是**当日累计值**（非增量）→ 降采取小时末=该小时结束时的累计，求和/平均会错。分钟占大头卡3月、日线极省放宽（看长期趋势）。复用 spec003 retention 机制（downsample_after_days/retention_trade_days）。
- **Alternatives**：降采求和（累计值求和错）；实时降采（无意义、耗性能）。

## D6 · 三级校验（只标记不改数）

- **Decision**：`validate.py`——①恒等式自检(流入-流出==净额、流入+流出==成交额)入库前，不成立=数据坏拒入库记审计；②跨源方向+量级(新浪vs百度当日抽样，方向反 或 量级差>阈值默认3倍→标记分歧)；③分歧只标记+记审计(ingestion_run.degraded/专门校验记录)+前端来源角标，绝不自动改数。
- **Rationale**：各源档位阈值定义不同→数值本就有差，要求相等必误报。校验=抓真异常(方向反/量级差/恒等式坏)，不求相等。宪章V审计诚实、不掩盖。
- **Alternatives**：要求跨源相等（口径差异必假阳性）；自动"修正"（替用户误判、改坏数据）。

## D7 · 背离识别：简单方向背离

- **Decision**：`flow_query_service` 算——给定展示窗口，价格净变化(末-首)与主力累计净额净变化(末-首)方向相反 → 价跌+累计升=**吸筹**、价升+累计降=**派发**，打标记随时序下发。
- **Rationale**：确定性、可测（给定序列判定唯一）、MVP够用、直接命中SC-002"一眼看出吸筹"。调研的量化背离度(0-100)更强但复杂，留后续升级。
- **Alternatives**：量化背离度评分（阈值难定，MVP过重）；不自动识别（弱化SC-002）。

## D8 · 前端 ECharts 单库

- **Decision**：引入 ECharts（全量CDN/vendored ~330KB gzip，无打包）。复合主图=多grid竖排(价格/四档双向堆叠柱/累计线) + `dataZoom(inside+slider,跨grid)` + `axisPointer:{link}`十字光标同步；热力=heatmap；回放=timeline外壳+markLine随帧；对抗=镜像area+scatter象限。红涨绿跌、四档同色系明度递进、主力/散户分色组。
- **Rationale**：单库覆盖全部四档图形语汇（双向柱/热力/镜像面积/象限散点/时间轴），贴合原生JS（echarts.init+setOption，零框架），性能远够（调研结论）。
- **Alternatives**：lightweight-charts(无原生双向堆叠柱/热力，为省体积拆双库不划算)；D3(自绘成本高，仅未来footprint再上)。

## 依赖（走 uv add / 前端引入）

| 依赖 | 用途 | 说明 |
|---|---|---|
| playwright（已装）| 百度校验探针无头浏览器 | page 可注入 mock |
| ECharts（前端引入）| 四档博弈可视化 | CDN/vendored 全量，无打包步骤 |
| Tushare | 深历史备源 | **仅预留适配器，不装不实现** |

## 诚实边界（写入 spec，实现守住）

- 盘中只净额（东财实时无gross）——物理天花板，非缺陷；待下周探新浪实时(记忆 todo)。
- 各源口径差异正常，校验抓异常不求相等。
- 板块四档=成分求和派生（无干净板块gross源，且成分求和内部自洽）。
