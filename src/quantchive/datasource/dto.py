"""数据源规整后的形状（contracts/datasource.md）。

RawObservation：通用观测形状，承载任意主体的任意指标（资金流+价格+量+稀疏）。
源实现负责把东财原始列/单位转成此形状；上层不感知具体源。
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal

from quantchive.models.enums import (
    AmountUnit,
    AssetClass,
    Caliber,
    SubjectKind,
    SubjectLevel,
)


@dataclass(frozen=True)
class RawObservation:
    """通用观测（spec002）。指标缺失用 None，源按能力自描述填充。

    金额类字段归一到「元」Decimal（源单位见 source_unit）；价格/涨跌幅/量为原值 Decimal
    （由 DAO 层按 metric_def 标度换算为整数存储，禁 float）。稀疏指标进 metrics dict。
    """

    source_symbol: str                 # '600519' / 'BK0475' / '512880' / '__MARKET__'
    display_name: str
    asset_class: AssetClass
    level: SubjectLevel
    subject_kind: SubjectKind
    caliber: Caliber = Caliber.EASTMONEY
    # 资金流五档（元；无此指标的主体全 None）
    main_net: Decimal | None = None
    super_large_net: Decimal | None = None
    large_net: Decimal | None = None
    medium_net: Decimal | None = None
    small_net: Decimal | None = None
    # 价格/量指标（原值 Decimal；无则 None）
    price: Decimal | None = None            # 现价（元）
    change_pct: Decimal | None = None       # 涨跌幅（百分数，如 2.35 表示 2.35%）
    volume: Decimal | None = None           # 成交量
    turnover: Decimal | None = None         # 成交额（元）
    turnover_pct: Decimal | None = None     # 换手率（百分数）
    # 稀疏指标 metric_name -> 原值 Decimal（如 circ_mktcap 流通市值）
    metrics: dict[str, Decimal] = field(default_factory=dict)
    # 审计
    exchange: str | None = None
    em_board_code: str | None = None
    source_unit: AmountUnit = AmountUnit.YUAN
    raw_value: str | None = None
    # 历史行情用：该观测所属真实交易日（实时快照为 None，由采集层用批次日期）
    trade_date: str | None = None
