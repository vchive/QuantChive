"""领域枚举 —— 最内层，各层向内引用，禁反向依赖。

见 data-model.md / research.md D5/D7。
"""

from __future__ import annotations

from enum import Enum


class Caliber(str, Enum):
    """板块口径（数据源体系）。"""

    EASTMONEY = "eastmoney"  # 东方财富：主力/超大/大/中/小 五档
    THS = "ths"  # 同花顺：仅净额，无五档

    @property
    def display_name(self) -> str:
        return {"eastmoney": "东方财富", "ths": "同花顺"}[self.value]


class SectorType(str, Enum):
    """板块类型。region 本期不采集（research.md D8），保留供未来。"""

    INDUSTRY = "industry"
    CONCEPT = "concept"
    REGION = "region"


class SourceType(str, Enum):
    """资金流记录的数据来源类型（research.md D6）。

    spec002 扩展：+intraday_latest（个股 LATEST 覆盖行）、+hourly_rollup（降采小时点）。
    """

    INTRADAY_SNAPSHOT = "intraday_snapshot"  # 盘中累计快照
    INTRADAY_LATEST = "intraday_latest"  # 个股当日覆盖行(minute_slot='LATEST', spec002 D4)
    DAILY_FINAL = "daily_final"  # 收盘后日终确定值
    HOURLY_ROLLUP = "hourly_rollup"  # 8-30天降采小时点(spec002 分级保留)


# data-model.md 用 value_type 列名承载 SourceType；此别名保持术语一致
ValueType = SourceType


class SortField(str, Enum):
    """排行排序字段（contracts/service.md）。默认 MAIN_NET。

    注：东财口径 net_amount == main_net（恒等，research.md 裁定）。NET_AMOUNT 保留
    作 main_net 的别名以兼容 spec001，通用查询层按能力隔离可排序字段。
    """

    MAIN_NET = "main_net"  # 主力净额（默认）
    NET_AMOUNT = "net_amount"  # 东财口径 == main_net（别名，兼容 spec001）
    SUPER_LARGE_NET = "super_large_net"
    LARGE_NET = "large_net"
    MEDIUM_NET = "medium_net"
    SMALL_NET = "small_net"
    CHANGE_PCT = "change_pct"  # 涨跌幅(ETF/个股, spec002)
    PRICE = "price"  # 价格(unipolar, spec002)
    VOLUME = "volume"  # 成交量(unipolar, spec002)


class RunStatus(str, Enum):
    """采集运行三态 + 中断（research.md D6，宪章 V）。"""

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"
    INTERRUPTED = "interrupted"


class RunType(str, Enum):
    INTRADAY_SNAPSHOT = "intraday_snapshot"
    EOD_BACKFILL = "eod_backfill"
    RETENTION_CLEANUP = "retention_cleanup"
    MARKET_AGGREGATE = "market_aggregate"  # 大盘求和(spec002 D2)
    RETENTION_DOWNSAMPLE = "retention_downsample"  # 8-30天降采(spec002)


class AmountUnit(str, Enum):
    """数据源金额单位标度（research.md D1）。东财=元，同花顺=亿元。"""

    YUAN = "yuan"  # ×100 → 分
    YI = "yi"  # ×10_000_000_000 → 分


# ---- spec002 通用市场观测平台枚举（data-model.md）----


class AssetClass(str, Enum):
    """可交易品种。本期 A股/基金ETF 真实，其余预留。"""

    A_SHARE = "a_share"
    FUND_ETF = "fund_etf"
    BOND = "bond"  # 预留
    FUTURES = "futures"  # 预留
    COMMODITY = "commodity"  # 预留
    FX = "fx"  # 预留


class SubjectLevel(str, Enum):
    """主体层级（下钻）：大盘 → 板块 → 个体。"""

    MARKET = "market"
    SECTOR = "sector"
    INSTRUMENT = "instrument"


class SubjectKind(str, Enum):
    """主体细类。"""

    MARKET_TOTAL = "market_total"
    INDUSTRY = "industry"
    CONCEPT = "concept"
    REGION = "region"
    STOCK = "stock"
    ETF = "etf"
    OPEN_FUND = "open_fund"


class CollectTier(str, Enum):
    """采集频率分层（subject.collect_tier，spec002 D4 存储量策略）。"""

    MINUTE = "minute"  # 板块/热点股 1min
    COARSE = "coarse"  # 个股/ETF 5min
    DERIVED = "derived"  # 大盘(对齐个股求和)
    ON_DEMAND = "on_demand"  # 开放式基金(不采,按需查)
    DAILY = "daily"  # 默认


class MetricValueKind(str, Enum):
    """指标值类型与标度（metric_def.value_kind，宪章 III）。"""

    MONEY_CENTS = "money_cents"  # ×100 整数分
    PRICE_MICRO = "price_micro"  # ×1e6 微元
    PERCENT_BP = "percent_bp"  # ×1e4 基点
    COUNT = "count"  # 计数(量)
    NAV_MICRO = "nav_micro"  # ×1e6 净值
