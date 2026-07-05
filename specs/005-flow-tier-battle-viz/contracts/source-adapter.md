# Contract: 资金流源适配器（gross）+ 校验

**Feature**: 005 | 决策 D3/D4/D6 见 [research.md](../research.md)

## 资金流源统一形状（复用 ObservationSource + HistorySource）

各源产出归一到 RawObservation，四档带 **net + gross**（gross 可空）：
```python
RawObservation(
  main_net, super_large_net, large_net, medium_net, small_net,          # 已有
  super_large_gross, large_gross, medium_gross, small_gross,            # 新增（可空）
  price, change_pct, trade_date, source_unit, ...
)
```

## 新浪（主源，改造）— `sina_flow_src.py`
- 现有解析 r0_net-r3_net（net）→ 补解析 r0-r3（gross）→ 各档 gross。
- 归一：gross/net 均 to_cents（元→分）。super_large=r0, large=r1, medium=r2, small=r3。
- 契约：`fetch_flow_history` 返回带四档 gross+net 的 RawObservation；恒等式 `(gross+net)/2` 可推流入流出。

## 百度（校验探针，新）— `baidu_flow_src.py`
```python
class BaiduFlowSource:
    source_id = "baidu_flow"
    def __init__(self, *, page_factory=None): ...   # page_factory 可注入（测试传 fake）
    def fetch_today_tiers(self, codes: list[str]) -> list[RawObservation]:
        # 1) page_factory() 开无头浏览器（默认 Playwright chromium）打开 gushitong
        # 2) page.evaluate 内连续 fetch fundflow（百度JS自动带 Acs-Token）
        # 3) 解析 fundFlowSpread.result.{superGrp,largeGrp,mediumGrp,littleGrp}
        #    turnoverIn/turnoverOut/netTurnover(亿) → 各档 gross=in+out、net → to_cents
```
- 定位：**校验探针**，收盘后随机抽样几十只+轮换覆盖；不做数据主力。
- **可注入**：测试传 fake page（evaluate 返固定 JSON），不联网。
- 不可用（反爬/无浏览器）→ 降级跳过，不阻塞主链路。

## Tushare（预留，新）— `tushare_flow_src.py`
- 声明能力（gross双向、~15年、独立后端），`is_active=0`，`fetch_*` 抛 `NotImplementedError`/返回空 + 明确"未开通token"日志。路由跳过。

## 校验 — `validate.py`（D6，只标记不改数）
```python
def check_identity(obs) -> bool:
    # 流入-流出==净额、流入+流出==成交额；不成立=数据坏
def cross_source_verdict(a, b, *, magnitude_ratio=3.0) -> str:
    # 'ok' | 'divergence'(方向反 或 量级差>ratio) —— 数值有差但方向量级合理=ok(口径差异)
def record_validation(conn, *, subject_id, trade_date, verdict, detail): ...  # 记审计,不改数
```
**行为契约（mock 可测）**：
- 恒等式失败 → 拒入库（数据不进 observation）+ 记审计。
- 跨源方向反 或 量级差>3倍 → verdict='divergence' 标记+审计，**数据照进(新浪主源)不改**，前端 provenance.validation='divergence' 角标。
- 数值有差但方向一致量级相近 → 'ok'（不报，口径差异正常）。

## 能力路由（复用 metric_source / 选源工厂）
- money_flow_gross（历史）：`[sina_flow, tushare_flow(预留), baidu_flow(当日)]`
- money_flow_net（盘中）：`[eastmoney]`
- 源声明 `has_gross / history_depth / independent_backend / is_active`，编排按能力路由（主/备/校验/降级）。
