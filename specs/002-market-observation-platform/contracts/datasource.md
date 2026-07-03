# Contract: DataSource 抽象（多品种多主体）

**Feature**: 002 | 决策 D5/D7/D8 见 [research.md](../research.md)

## ObservationSource Protocol (`datasource/base.py`)

```python
@dataclass(frozen=True)
class FetchSpec:
    fs: str                              # 东财 clist fs 参数
    level: SubjectLevel                  # market/sector/instrument
    asset_class: AssetClass              # a_share/fund_etf
    sector_type: SectorType | None = None
    fields: tuple[str, ...] = _EM_FIELDS_DEFAULT
    page_size: int = 100                 # 东财单页硬顶

@runtime_checkable
class ObservationSource(Protocol):
    source_id: str
    def capability(self, spec: FetchSpec) -> SubjectCapability: ...
    def fetch_subjects(self, spec: FetchSpec) -> Sequence[SubjectRef]: ...       # 维表刷新
    def fetch_observations(self, spec: FetchSpec) -> Sequence[RawObservation]: ...  # 指标快照
    def fetch_members(self, parent: SubjectRef) -> Sequence[RawObservation]: ...    # 板块→个股 fs=b:BK{code}
    def fetch_daily_final(self, spec: FetchSpec, trade_date: date) -> Sequence[RawObservation]: ...

class SubjectCapability(_Base):
    asset_class: AssetClass; subject_kind: SubjectKind
    has_five_tier: bool
    supported_metrics: list[MetricName]
    available_sort_fields: list[SortField]    # 按主体隔离, 阻断非法字段(板块拒价格排序)
    series_granularity: str                   # '1min'|'5min'|'daily'
    notes: str | None = None
```

## 东财 clist fs 参数映射（实测）

| 主体 | fs | total | granularity |
|---|---|---|---|
| 全市场个股 | m:0+t:6,m:0+t:80,m:1+t:2,m:1+t:23 | 5535 | 5min |
| 行业板块 | m:90 t:2 | 496 | 1min |
| 概念板块 | m:90 t:3 | 495 | 1min |
| ETF | b:MK0021,b:MK0022,b:MK0023,b:MK0024 | 1521 | 5min |
| 板块成员 | b:BK{code} | — | 下钻 |

字段：f14名/f62主力/f66超大/f72大/f78中/f84小(元)/f2价/f3涨跌幅/f5量/f6额/f21流通市值。**翻页上限动态 ceil(total/100)**（个股 56 页，不可静默截 100）。带 UA + Referer header。

## 保留旧 SectorFlowSource 薄适配

迁移期保留旧 Protocol，`fetch_snapshot(caliber, sector_type)` 转调新接口，`test_eastmoney_adapter` 5 例不改仍绿。

## 开放式基金（旁路，不入 registry）

`fund_src.py`：`api.fund.eastmoney.com/f10/lsjz?fundCode=X`，**必带 Referer: fund.eastmoney.com**（否则 -999），返回 FSRQ/DWJZ/JZZZL，净值 Decimal 字符串。

## 换源保证（SC-008）

Protocol 依赖倒置 + RawObservation 形状收口 + capability 自描述。未来 EFinance（跨品种）、同花顺北向在此抽象下接入不改上层。
