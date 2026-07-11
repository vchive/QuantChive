"""QuantChive MCP Server — 把 service 能力标准化为 MCP tools（stdio）。

任何 MCP 客户端(Claude Code / Desktop / Hermes / 自研 agent)可零胶水接入。
只读:tools 只查不写(采集/回填是写权限,不在此暴露)。每 tool 开只读连接→建 service→
调方法→返回 DTO.model_dump(JSON 安全,金额已是字符串不丢精度)。

启动:`uv run quantchive-mcp`(客户端按需 spawn,不常驻;只读同一 SQLite,WAL 不阻塞采集)。
注:tool 写成模块级函数(FastMCP 装饰器不支持实例方法),显式注册,便于单测直接调用。
"""

from __future__ import annotations

import contextlib
import sys
from typing import Any

# MCP stdio：stdout 是 JSON-RPC 协议通道，日志必须走 stderr，否则污染协议流。
# 必须在任何 get_logger()（会触发 configure_logging 用 stdout）之前配置。
from quantchive.core.logging import configure_logging

configure_logging(stream=sys.stderr)

from mcp.server.fastmcp import FastMCP

from quantchive.core.db import connect
from quantchive.core.settings import get_settings
from quantchive.models.enums import AssetClass, Caliber, SectorType, SortField, SubjectKind, SubjectLevel
from quantchive.service.errors import QueryError
from quantchive.service.flow_query_service import FlowQueryService
from quantchive.service.flow_topology_service import FlowTopologyService
from quantchive.service.fund_lookup_service import FundLookupService
from quantchive.service.query_service import QueryService
from quantchive.service.backtest_service import BacktestService
from quantchive.service.signal_lib import SIGNAL_KINDS
from quantchive.service.fundamental_service import FundamentalService
mcp = FastMCP("quantchive")


@contextlib.contextmanager
def _readonly_conn():
    """每 tool 调用开→用→关一个连接（WAL 读,不阻塞采集写；不复用避免 stdio 长驻泄漏）。"""
    conn = connect(get_settings().db_path)
    try:
        yield conn
    finally:
        conn.close()


def _qs(conn) -> QueryService:
    return QueryService(conn, retention_trade_days=get_settings().retention_trade_days)


def _err(exc: QueryError) -> dict:
    """结构化错误透传（不静默）：{error:{code,message,detail}}。"""
    return {"error": {"code": getattr(exc, "code", "ERROR"),
                      "message": str(exc), "detail": getattr(exc, "detail", None)}}


def _dump(result: Any) -> dict:
    return result.model_dump(mode="json")


# ─────────── 寻址 / 自省 ───────────

def search_subject(query: str, limit: int = 10) -> list[dict] | dict:
    """按名称或代码搜主体，拿 subject_id(名→ID 解析)。查具体股/板块**先用这个**,
    别用 list_subjects 翻全表。例:search_subject("东山精密") → [{subject_id, display_name, ...}]。"""
    from quantchive.dao.subject_dao import SubjectDao
    with _readonly_conn() as conn:
        return SubjectDao(conn).search_by_name(query=query, limit=limit)


def list_subjects(asset_class: str = "a_share", level: str | None = None,
                  subject_kind: str | None = None) -> list[dict] | dict:
    """列某类主体全清单(如"全部行业板块")。**找具体某只股/板块请用 search_subject**,
    此工具返回量大。asset_class: a_share|fund_etf；level: market|sector|instrument。"""
    with _readonly_conn() as conn:
        try:
            refs = _qs(conn).get_subjects(
                asset_class=AssetClass(asset_class),
                level=SubjectLevel(level) if level else None,
                subject_kind=SubjectKind(subject_kind) if subject_kind else None)
            return [r.model_dump(mode="json") for r in refs]
        except QueryError as e:
            return _err(e)


def describe_capability(asset_class: str, subject_kind: str) -> dict:
    """查某主体类型的能力自省(有无五档、支持哪些指标/排序、序列粒度)。选工具/指标前先问这个。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_capability(
                asset_class=AssetClass(asset_class), subject_kind=SubjectKind(subject_kind)))
        except QueryError as e:
            return _err(e)


# ─────────── 快照 / 排行 ───────────

def market_overview(trade_date: str | None = None) -> dict:
    """A股大盘五档净额(个股求和,盘中门禁0.90/历史0.95)。问'今天大盘主力净流入多少'用这个。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_market_overview(
                asset_class=AssetClass.A_SHARE, trade_date=trade_date))
        except QueryError as e:
            return _err(e)


def rank_sectors(sector_type: str = "industry", sort_by: str = "main_net",
                 top_n: int = 10, trade_date: str | None = None) -> dict:
    """板块资金流双榜(流入/流出 TopN)。sector_type: industry|concept|region。
    问'今天哪个行业主力流入最多'→ sort_by=main_net 取 top_inflow[0]。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_sector_ranking(
                caliber=Caliber.EASTMONEY, sector_type=SectorType(sector_type),
                sort_by=SortField(sort_by), top_n=top_n, trade_date=trade_date))
        except QueryError as e:
            return _err(e)


def rank_stocks_in_sector(sector_id: int, sort_by: str = "main_net", top_n: int = 20,
                          trade_date: str | None = None, as_of: str | None = None) -> dict:
    """板块内成分股排行(下钻选股)。as_of 给历史日则无前视取该日成分。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_stocks_in_sector(
                sector_subject_id=sector_id, sort_by=SortField(sort_by), top_n=top_n,
                trade_date=trade_date, as_of=as_of))
        except QueryError as e:
            return _err(e)


def scan_market_stocks(sort_by: str = "main_net", top_n: int = 50,
                       trade_date: str | None = None, as_of: str | None = None) -> dict:
    """全市场个股平铺横截面排行(跨行业统一榜)。问'今天全A股主力净流入前50'用这个。
    bipolar(main_net等)返双榜、unipolar(price/volume)返单榜。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).scan_market_stocks(
                sort_by=SortField(sort_by), top_n=top_n, trade_date=trade_date, as_of=as_of))
        except QueryError as e:
            return _err(e)


def rank_etf(sort_by: str = "change_pct", top_n: int = 20, trade_date: str | None = None) -> dict:
    """ETF 排行(单榜,无资金流五档)。sort_by: change_pct|price|volume。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_etf_ranking(
                sort_by=SortField(sort_by), top_n=top_n, trade_date=trade_date))
        except QueryError as e:
            return _err(e)


# ─────────── 时序 / 资金博弈 ───────────

def get_series(subject_id: int, metric: str = "main_net",
               granularity: str = "daily", trade_date: str | None = None) -> dict:
    """单主体单指标时序。granularity: daily(跨日历史) | intraday(当日分钟,超保留窗报错)。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_subject_series(
                subject_id=subject_id, metric=SortField(metric),
                trade_date=trade_date, granularity=granularity))
        except QueryError as e:
            return _err(e)


def get_tiers_series(subject_id: int, granularity: str = "daily") -> dict:
    """单股四档资金博弈时序:四档(净额+流入+流出)+主力/散户+价+全程累计+背离段(吸筹/派发)。"""
    with _readonly_conn() as conn:
        try:
            svc = FlowQueryService(conn, retention_trade_days=get_settings().retention_trade_days)
            return _dump(svc.get_tiers_series(subject_id=subject_id, granularity=granularity))
        except QueryError as e:
            return _err(e)


def signal_backtest(subject_id: int, kinds: str | None = None, horizons: str = "1,3,5") -> dict:
    """某股资金流信号历史回测(历史统计,非预测)。

    返回各信号×各horizon:触发次数、胜率、Wilson 95%置信区间、平均/中位前向收益、
    无条件基准(全期日涨占比)、样本是否可靠(n<30标注不可靠)。
    信号=吸筹背离/派发背离/连续净流入/超大单异动;只用as-of前数据(防前视),
    标签用后复权价前向收益链式乘(防漂移)。用于回答"这类信号历史上后续涨的比例",
    绝不据此断言次日涨跌概率。
    """
    kind_list = (
        [k.strip() for k in kinds.split(",") if k.strip() in SIGNAL_KINDS]
        if kinds else list(SIGNAL_KINDS))
    hz = tuple(int(h) for h in horizons.split(",") if h.strip().isdigit()) or (1, 3, 5)
    with _readonly_conn() as conn:
        try:
            return _dump(BacktestService(conn).backtest_stock(
                subject_id=subject_id, kinds=kind_list, horizons=hz))
        except QueryError as e:
            return _err(e)


def describe_fundamentals(subject_id: int, as_of: str | None = None) -> dict:
    """某股基本面(业绩+三大报表:EPS/ROE/净利同比/营收/毛利率/总资产/负债率/经营现金流等)。

    按公告日 PIT(as_of 缺省=最新已披露报告期)。客观转述财报数字,不臆测估值/买卖。
    """
    with _readonly_conn() as conn:
        try:
            return _dump(FundamentalService(conn).latest(subject_id=subject_id, as_of=as_of))
        except QueryError as e:
            return _err(e)


def fundamental_signal_backtest(subject_id: int, kinds: str | None = None) -> dict:
    """某股基本面信号历史回测(净利增速转正/加速、ROE跳升、营收加速)。

    可见时点=法定披露截止日(保守 PIT,不用不可靠的公告日),前向 20/60 交易日胜率+
    Wilson置信区间+基准对照。基本面事件稀疏,样本常不足(n<30)必如实说不可靠。
    历史条件统计,非预测。
    """
    from quantchive.service.fundamental_backtest_service import FundamentalBacktestService
    from quantchive.service.fundamental_signal import FUND_SIGNAL_KINDS
    kl = ([k.strip() for k in kinds.split(",") if k.strip() in FUND_SIGNAL_KINDS]
          if kinds else list(FUND_SIGNAL_KINDS))
    with _readonly_conn() as conn:
        try:
            return _dump(FundamentalBacktestService(conn).backtest_stock(
                subject_id=subject_id, kinds=kl))
        except QueryError as e:
            return _err(e)


def market_fundamental_signal_stats() -> dict:
    """全市场基本面信号历史统计(净利转正/加速、ROE跳升、营收加速 × 前向20/60日)。

    单股事件稀疏样本不足时用这个:全市场近5年数千次事件聚合,Wilson CI 收窄到统计可信。
    beats_baseline=true 表示 CI 下界高于基准(统计显著优于随便买)。预计算秒读。
    历史条件统计非预测;财务数值为最新重述口径(数据源限制)。
    """
    from quantchive.service.market_signal_service import read_market_stats
    with _readonly_conn() as conn:
        try:
            return read_market_stats(conn, family="fundamental")
        except QueryError as e:
            return _err(e)


def market_flow_signal_stats() -> dict:
    """全市场资金流信号历史统计(吸筹/派发背离、连续净流入、超大单异动 × 前向1/5/20日)。

    回答"某资金流信号普遍有没有用":全市场5年事件聚合(同股非重叠去重),CI收窄可信。
    beats_baseline=true 表示 CI 下界高于基准(统计显著)。预计算秒读。
    注意:同日多股触发受共同市场因素影响,有效独立样本低于名义n(已在disclosure说明)。
    历史条件统计非预测。
    """
    from quantchive.service.market_signal_service import read_market_stats
    with _readonly_conn() as conn:
        try:
            return read_market_stats(conn, family="flow")
        except QueryError as e:
            return _err(e)


def flow_topology(trade_date: str | None = None, tier: str = "main",
                  top_sectors: int = 20) -> dict:
    """资金流向拓扑:大盘→行业(TopN)桑基。tier: main|super_large|large|medium|small。"""
    with _readonly_conn() as conn:
        try:
            return _dump(FlowTopologyService(conn).get_topology(
                trade_date=trade_date, tier=tier, top_sectors=top_sectors))
        except QueryError as e:
            return _err(e)


def sector_trends(tier: str = "main", days: int = 20, top_sectors: int = 10) -> dict:
    """各行业近N天净额趋势(多天对比,看行业轮动)。"""
    with _readonly_conn() as conn:
        try:
            return _dump(FlowTopologyService(conn).get_sector_trends(
                tier=tier, days=days, top_sectors=top_sectors))
        except QueryError as e:
            return _err(e)


# ─────────── 回测取数(策略) ───────────

def get_series_range(subject_id: int, start_date: str, end_date: str,
                     metric: str = "main_net") -> dict:
    """单主体日线区间取数(回测)。任意历史区间,不受实时保留窗限制。end_date 为 as-of 上界(防前视)。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_subject_series_range(
                subject_id=subject_id, metric=SortField(metric),
                start_date=start_date, end_date=end_date))
        except QueryError as e:
            return _err(e)


def get_series_batch(subject_ids: list[int], start_date: str, end_date: str,
                     metric: str = "main_net") -> dict:
    """多主体日线区间批量(回测组合取数,一次取全部)。"""
    with _readonly_conn() as conn:
        try:
            return _dump(_qs(conn).get_subjects_series_batch(
                subject_ids=subject_ids, metric=SortField(metric),
                start_date=start_date, end_date=end_date))
        except QueryError as e:
            return _err(e)


def fund_nav(fund_code: str, days: int = 60) -> dict:
    """开放式基金净值(旁路实时查,不入库)。"""
    from quantchive.datasource.fund_src import FundSource
    try:
        svc = FundLookupService(FundSource())
        return _dump(svc.get_fund_nav(fund_code=fund_code, days=days))
    except QueryError as e:
        return _err(e)


# 注册全部 tool（模块级函数,显式注册,便于单测直接调用）
_TOOLS = [
    search_subject, list_subjects, describe_capability, market_overview, rank_sectors,
    rank_stocks_in_sector, scan_market_stocks, rank_etf, get_series,
    get_tiers_series, flow_topology, sector_trends, get_series_range,
    get_series_batch, fund_nav, signal_backtest, describe_fundamentals,
    fundamental_signal_backtest, market_fundamental_signal_stats, market_flow_signal_stats,
]
for _fn in _TOOLS:
    mcp.tool()(_fn)


def main() -> None:
    """stdio 入口(quantchive-mcp)。客户端按需 spawn。"""
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
