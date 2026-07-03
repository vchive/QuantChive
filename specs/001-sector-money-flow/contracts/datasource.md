# Contract: DataSource 抽象

**Feature**: 001-sector-money-flow | 决策 D5 见 [research.md](../research.md)

## SectorFlowSource Protocol (`datasource/base.py`)

```python
class SectorFlowSource(Protocol):
    source_id: str  # "akshare"; 落审计与可复现

    def capabilities(self, caliber: Caliber) -> CaliberCapability: ...
        # has_five_tier / has_daily_final / supported_sector_types
        # / provided_metrics / available_sort_fields

    def fetch_snapshot(self, caliber: Caliber, sector_type: SectorType) -> Sequence[RawSectorFlow]:
        """当日累计快照(盘中可轮询)。
        东财 = stock_sector_fund_flow_rank(indicator='今日', sector_type)
        同花顺 = stock_fund_flow_industry/concept(symbol='即时')
        单板块空/新板块跳过并计数; 限流/失败有限次重试+退避; 仍失败抛 DataSourceError。"""

    def fetch_daily_final(self, caliber: Caliber, sector_type: SectorType,
                          trade_date: date) -> Sequence[RawSectorFlow]:
        """日终确定值。
        东财 = 收盘后(约15:40)再调一次 rank(indicator='今日') 单次全量(非逐板块 hist, D5)
        同花顺 has_daily_final=False → 末次即时快照近似
        地域无日终(本期不采)。"""
```

## RawSectorFlow (`datasource/dto.py`)

dataclass，单位/量纲解析在源实现内完成：

| 字段 | 类型 | 说明 |
|---|---|---|
| `source_symbol` | str | akshare 板块名称 |
| `caliber` | Caliber | eastmoney / ths |
| `sector_type` | SectorType | industry / concept |
| `net_amount` | Decimal | **归一后单位=元**，可排序非空 |
| `main_net / super_large / large / medium / small` | Decimal \| None | 五档，同花顺全 None |
| `inflow / outflow` | Decimal \| None | 同花顺辅助，东财 None |
| `source_unit` | str | 'yuan' / 'yi'，审计 |
| `raw_value` | str | 原始值 JSON，审计回溯 |

## 换源三保证（FR-011 / SC-007）

1. **依赖倒置**：上层仅依赖 Protocol，不依赖 akshare 实现。
2. **形状收口**：所有源输出统一为 `RawSectorFlow`（已 Decimal、单位归一到元、五档缺失用 None）。
3. **能力自描述**：上层通过 `capabilities()` 问能力，不问身份——代码中**无 `if source == "akshare"`** 分支。

## 契约测试要点

- 新增一个 fake source 实现 Protocol，不改 service/api 即可跑通排行查询（验证 SC-007）。
- 单位换算：东财 `-1.411231e+08` 元 → `-14112310000` 分；同花顺 `1.23` 亿 → `12300000000` 分。
- `Decimal(str(x))` 路径，禁 `Decimal(float)`；0/负/NaN/亿元 2 位小数边界。
