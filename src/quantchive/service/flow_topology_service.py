"""资金流向拓扑聚合（spec006）。大盘→行业→个股，单日快照，四档可切换。

守恒链路：大盘=Σ全股净额、行业=Σ该L1成分股净额（每股唯一归属 build_l1_map）、个股直读。
金额整数分求和（禁 float，宪章III）；TopN 截断后其余归「其他」聚合节点（否则子和<父，钱消失）。
负净额：abs 定线宽、方向红绿——前端画；后端只出带符号字符串。覆盖率诚实标注（宪章I）。
只做日线历史（盘中无 gross、板块实时净额不守恒）。
"""

from __future__ import annotations

import sqlite3

from quantchive.core.money import cents_to_yuan_str
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.models.enums import SourceType, ValueType
from quantchive.service.dto import (
    DataProvenance,
    FlowLink,
    FlowNode,
    FlowTopologyResult,
    FlowTreeNode,
    FlowTreeResult,
)
from quantchive.service.errors import NoDataForDate
from quantchive.service.l1_industry import build_l1_map

_TIER_COL = {
    "main": "main_net_cents", "super_large": "super_large_net_cents",
    "large": "large_net_cents", "medium": "medium_net_cents", "small": "small_net_cents",
}
_TIER_GROSS = {
    "super_large": "super_large_gross_cents", "large": "large_gross_cents",
    "medium": "medium_gross_cents", "small": "small_gross_cents",
}


def _direction(net: int) -> str:
    return "in" if net > 0 else "out" if net < 0 else "flat"


class FlowTopologyService:
    def __init__(self, conn: sqlite3.Connection, *, source_code: str = "sina_flow") -> None:
        self._conn = conn
        self._obs = ObservationDao(conn)
        self._subject = SubjectDao(conn)
        self._source = source_code

    def available_dates(self, *, limit: int = 60) -> list[str]:
        """可选交易日：有 sina 日线四档 或 有盘中个股快照 的日期（降序）。

        当天盘中(还没收盘日线)也应可选——供天内回放。"""
        rows = self._conn.execute(
            """SELECT trade_date FROM (
                   SELECT DISTINCT trade_date FROM observation
                   WHERE source_code=? AND value_type='daily_final' AND main_net_cents IS NOT NULL
                   UNION
                   SELECT DISTINCT o.trade_date FROM observation o
                   JOIN subject s ON s.subject_id=o.subject_id
                   WHERE o.value_type='intraday_snapshot' AND s.subject_kind='stock'
                     AND o.minute_slot!='LATEST' AND o.main_net_cents IS NOT NULL
               ) ORDER BY trade_date DESC LIMIT ?""",
            (self._source, limit)).fetchall()
        return [r[0] for r in rows]

    def intraday_points(self, *, trade_date: str) -> dict:
        """某日「天内时点」列表（供天内回放）：个股盘中快照的 minute_slot 升序去重。

        天内拓扑靠个股分钟净额求和派生，故只取个股(stock)的时点。近期分钟级、久远小时级
        由归档层决定。返回 {trade_date, granularity, slots:[...]}（升序、唯一）。
        """
        rows = self._conn.execute(
            """SELECT DISTINCT o.minute_slot FROM observation o
               JOIN subject s ON s.subject_id = o.subject_id
               WHERE o.value_type='intraday_snapshot' AND o.trade_date=?
                 AND o.minute_slot NOT IN ('LATEST') AND o.main_net_cents IS NOT NULL
                 AND s.subject_kind='stock'
               ORDER BY o.minute_slot ASC""",
            (trade_date,)).fetchall()
        slots = [r[0] for r in rows]
        # 粒度：小时冒号后为 '00' 视作小时级，否则分钟级
        gran = "eod" if not slots else ("hourly" if all(s.endswith(":00") for s in slots) else "1min")
        return {"trade_date": trade_date, "granularity": gran, "slots": slots}

    def get_sector_trends(self, *, tier: str = "main", days: int = 20, top_sectors: int = 10):
        """各行业近 N 交易日净额趋势（多天对比折线）。整数分求和，只出字符串。

        返回 {dates:[...], series:[{sector, sid, values:[净额元字符串,按 dates 对齐]}]}。
        取 |最新日净额| Top-N 行业。"""
        from quantchive.service.dto import SectorTrendsResult, SectorTrendSeries
        net_col = _TIER_COL.get(tier)
        if net_col is None:
            raise NoDataForDate(f"未知档位 {tier}", detail={"tier": tier})
        dates = list(reversed(self.available_dates(limit=days)))   # 升序
        if not dates:
            raise NoDataForDate("无 sina 日线数据", detail={})
        l1_map = build_l1_map(self._conn)   # 当前成分（缓变）
        ind_name = {r[0]: r[1] for r in self._conn.execute(
            "SELECT subject_id, display_name FROM subject WHERE subject_kind='industry'").fetchall()}
        # 逐日 × 行业 净额累加（整数分）
        placeholders = ",".join("?" * len(dates))
        rows = self._conn.execute(
            f"""SELECT trade_date, subject_id, {net_col} FROM observation
                WHERE source_code=? AND value_type='daily_final' AND {net_col} IS NOT NULL
                  AND trade_date IN ({placeholders})""",
            (self._source, *dates)).fetchall()
        # {sid: {date: net_cents}}
        agg: dict[int, dict[str, int]] = {}
        for td, sub_id, net in rows:
            sid = l1_map.get(sub_id)
            if sid is None:
                continue
            agg.setdefault(sid, {}).setdefault(td, 0)
            agg[sid][td] += net
        # 按最新日 |净额| 取 TopN
        last = dates[-1]
        ranked = sorted(agg.items(), key=lambda kv: abs(kv[1].get(last, 0)), reverse=True)[:top_sectors]
        series = [
            SectorTrendSeries(
                sector=ind_name.get(sid, f"行业{sid}"), sid=sid,
                values=[cents_to_yuan_str(perday.get(d, 0)) for d in dates])
            for sid, perday in ranked
        ]
        prov = DataProvenance(
            trade_date=last, source_type=SourceType.DAILY_FINAL, source_id=self._source,
            captured_at="EOD", is_stale=False)
        return SectorTrendsResult(tier=tier, dates=dates, provenance=prov, series=series)

    def get_topology(
        self, *, trade_date: str | None = None, tier: str = "main",
        top_sectors: int = 20, top_stocks_per_sector: int = 10,
        minute_slot: str | None = None,
    ) -> FlowTopologyResult:
        if tier not in _TIER_COL:
            raise NoDataForDate(f"未知档位 {tier}", detail={"tier": tier})
        net_col = _TIER_COL[tier]
        gross_col = _TIER_GROSS.get(tier)   # main 无独立 gross

        # 天内某时点：读盘中 intraday_snapshot（东财，无 gross）；否则日终 sina 日线
        if minute_slot:
            gross_col = None   # 盘中无 gross
            rows = self._obs.stock_rows_for(
                trade_date=trade_date, value_type=ValueType.INTRADAY_SNAPSHOT,
                minute_slot=minute_slot, source_code="eastmoney")
            captured, src, stype = minute_slot, "eastmoney", SourceType.INTRADAY_SNAPSHOT
        else:
            if trade_date is None:
                row = self._conn.execute(
                    "SELECT MAX(trade_date) FROM observation WHERE source_code=? AND value_type='daily_final'",
                    (self._source,)).fetchone()
                trade_date = row[0] if row else None
            if not trade_date:
                raise NoDataForDate("无 sina_flow 日线数据（需回填）", detail={})
            rows = self._obs.stock_rows_for(
                trade_date=trade_date, value_type=ValueType.DAILY_FINAL,
                minute_slot="EOD", source_code=self._source)
            captured, src, stype = "EOD", self._source, SourceType.DAILY_FINAL
            # 当天盘中(还没收盘日线) → 退化到最新盘中时点（东财，无 gross）
            if not any(getattr(r, net_col) is not None for r in rows):
                pts = self.intraday_points(trade_date=trade_date)
                if pts["slots"]:
                    latest = pts["slots"][-1]
                    gross_col = None
                    rows = self._obs.stock_rows_for(
                        trade_date=trade_date, value_type=ValueType.INTRADAY_SNAPSHOT,
                        minute_slot=latest, source_code="eastmoney")
                    captured, src, stype = latest, "eastmoney", SourceType.INTRADAY_SNAPSHOT

        valid = [r for r in rows if getattr(r, net_col) is not None]
        if not valid:
            raise NoDataForDate(
                f"{trade_date} {minute_slot or 'EOD'} 无个股档位数据",
                detail={"trade_date": trade_date, "minute_slot": minute_slot})

        expected = self._conn.execute(
            "SELECT COUNT(*) FROM subject WHERE subject_kind='stock'").fetchone()[0]
        coverage = len(valid) / expected if expected else 0.0

        l1_map = build_l1_map(self._conn, as_of=trade_date)
        ind_name = {
            r[0]: r[1] for r in self._conn.execute(
                "SELECT subject_id, display_name FROM subject WHERE subject_kind='industry'").fetchall()
        }

        # 行业聚合（整数分求和）+ 收集成分股
        sector_net: dict[int, int] = {}
        sector_gross: dict[int, int] = {}
        sector_stocks: dict[int, list] = {}
        market_net = 0
        for r in valid:
            net = getattr(r, net_col)
            market_net += net
            sid = l1_map.get(r.subject_id)
            if sid is None:
                continue
            sector_net[sid] = sector_net.get(sid, 0) + net
            if gross_col and getattr(r, gross_col) is not None:
                sector_gross[sid] = sector_gross.get(sid, 0) + getattr(r, gross_col)
            sector_stocks.setdefault(sid, []).append(r)

        nodes: list[FlowNode] = [FlowNode(
            id="market", name="A股大盘", depth=0, net_yuan=cents_to_yuan_str(market_net),
            direction=_direction(market_net), subject_id=None)]
        links: list[FlowLink] = []

        # 行业按 |净额| 降序，TopN + 其他
        ranked = sorted(sector_net.items(), key=lambda kv: abs(kv[1]), reverse=True)
        top = ranked[:top_sectors]
        rest = ranked[top_sectors:]
        for sid, net in top:
            nodes.append(FlowNode(
                id=f"sector:{sid}", name=ind_name.get(sid, f"行业{sid}"), depth=1,
                net_yuan=cents_to_yuan_str(net),
                gross_yuan=cents_to_yuan_str(sector_gross[sid]) if sid in sector_gross else None,
                direction=_direction(net), subject_id=sid))
            links.append(self._link("market", f"sector:{sid}", net))
        if rest:
            rest_net = sum(n for _, n in rest)
            nodes.append(FlowNode(
                id="other:market", name=f"其他{len(rest)}个行业", depth=1,
                net_yuan=cents_to_yuan_str(rest_net), direction=_direction(rest_net), subject_id=None))
            links.append(self._link("market", "other:market", rest_net))

        prov = DataProvenance(
            trade_date=trade_date, source_type=stype,
            source_id=src, captured_at=captured, is_stale=False)
        return FlowTopologyResult(
            trade_date=trade_date, tier=tier, provenance=prov,
            coverage_pct=f"{coverage * 100:.1f}", constituent_count=len(valid),
            expected_count=expected, nodes=nodes, links=links)

    @staticmethod
    def _link(source: str, target: str, net: int) -> FlowLink:
        return FlowLink(
            source=source, target=target, abs_value=cents_to_yuan_str(abs(net)),
            signed_value=cents_to_yuan_str(net), direction=_direction(net))

    def get_tree(
        self, *, trade_date: str | None = None, tier: str = "main",
        top_sectors: int = 30, top_stocks_per_sector: int = 8,
    ) -> FlowTreeResult:
        """全层级树：大盘→行业(TopN+其他)→各行业TopN个股(+其他)。旭日/矩形树全景一次返回。"""
        if tier not in _TIER_COL:
            raise NoDataForDate(f"未知档位 {tier}", detail={"tier": tier})
        net_col = _TIER_COL[tier]
        gross_col = _TIER_GROSS.get(tier)

        if trade_date is None:
            row = self._conn.execute(
                "SELECT MAX(trade_date) FROM observation WHERE source_code=? AND value_type='daily_final'",
                (self._source,)).fetchone()
            trade_date = row[0] if row else None
        if not trade_date:
            raise NoDataForDate("无 sina_flow 日线数据", detail={})

        rows = self._obs.stock_rows_for(
            trade_date=trade_date, value_type=ValueType.DAILY_FINAL,
            minute_slot="EOD", source_code=self._source)
        valid = [r for r in rows if getattr(r, net_col) is not None]
        if not valid:
            raise NoDataForDate(f"{trade_date} 无个股档位数据", detail={"trade_date": trade_date})
        expected = self._conn.execute(
            "SELECT COUNT(*) FROM subject WHERE subject_kind='stock'").fetchone()[0]
        coverage = len(valid) / expected if expected else 0.0

        l1_map = build_l1_map(self._conn, as_of=trade_date)
        ind_name = {
            r[0]: r[1] for r in self._conn.execute(
                "SELECT subject_id, display_name FROM subject WHERE subject_kind='industry'").fetchall()}

        sector_stocks: dict[int, list] = {}
        market_net = 0
        for r in valid:
            market_net += getattr(r, net_col)
            sid = l1_map.get(r.subject_id)
            if sid is not None:
                sector_stocks.setdefault(sid, []).append(r)

        # 各行业净额 + gross
        sec_agg = []
        for sid, sr in sector_stocks.items():
            snet = sum(getattr(x, net_col) for x in sr)
            sgross = sum(getattr(x, gross_col) for x in sr
                         if gross_col and getattr(x, gross_col) is not None)
            sec_agg.append((sid, snet, sgross, sr))
        sec_agg.sort(key=lambda t: abs(t[1]), reverse=True)

        def _stock_node(r):
            net = getattr(r, net_col)
            g = getattr(r, gross_col) if gross_col else None
            return FlowTreeNode(
                name=r.display_name, net_yuan=cents_to_yuan_str(net),
                gross_yuan=cents_to_yuan_str(g) if g is not None else None,
                direction=_direction(net), subject_id=r.subject_id, depth=2)

        sector_nodes = []
        for sid, snet, sgross, sr in sec_agg[:top_sectors]:
            sr_sorted = sorted(sr, key=lambda x: abs(getattr(x, net_col)), reverse=True)
            kids = [_stock_node(x) for x in sr_sorted[:top_stocks_per_sector]]
            rest = sr_sorted[top_stocks_per_sector:]
            if rest:
                rnet = sum(getattr(x, net_col) for x in rest)
                kids.append(FlowTreeNode(
                    name=f"其他{len(rest)}只", net_yuan=cents_to_yuan_str(rnet),
                    direction=_direction(rnet), depth=2))
            sector_nodes.append(FlowTreeNode(
                name=ind_name.get(sid, f"行业{sid}"), net_yuan=cents_to_yuan_str(snet),
                gross_yuan=cents_to_yuan_str(sgross) if gross_col else None,
                direction=_direction(snet), subject_id=sid, depth=1, children=kids))

        root = FlowTreeNode(
            name="A股大盘", net_yuan=cents_to_yuan_str(market_net),
            direction=_direction(market_net), depth=0, children=sector_nodes)
        prov = DataProvenance(
            trade_date=trade_date, source_type=SourceType.DAILY_FINAL,
            source_id=self._source, captured_at="EOD", is_stale=False)
        return FlowTreeResult(
            trade_date=trade_date, tier=tier, provenance=prov,
            coverage_pct=f"{coverage * 100:.1f}", root=root)

    def get_sector_stocks(
        self, *, sector_id: int, trade_date: str | None = None, tier: str = "main",
        top_stocks: int = 10,
    ) -> FlowTopologyResult:
        """行业下钻：该 L1 行业成分股按 |净额| TopN + 其他（供桑基就地展开/Treemap）。"""
        if tier not in _TIER_COL:
            raise NoDataForDate(f"未知档位 {tier}", detail={"tier": tier})
        net_col = _TIER_COL[tier]
        gross_col = _TIER_GROSS.get(tier)

        if trade_date is None:
            row = self._conn.execute(
                "SELECT MAX(trade_date) FROM observation WHERE source_code=? AND value_type='daily_final'",
                (self._source,)).fetchone()
            trade_date = row[0] if row else None
        if not trade_date:
            raise NoDataForDate("无 sina_flow 日线数据", detail={})

        rows = self._obs.stock_rows_for(
            trade_date=trade_date, value_type=ValueType.DAILY_FINAL,
            minute_slot="EOD", source_code=self._source)
        l1_map = build_l1_map(self._conn, as_of=trade_date)
        # 只留归属该行业、该档非空的股
        mine = [r for r in rows
                if l1_map.get(r.subject_id) == sector_id and getattr(r, net_col) is not None]
        if not mine:
            raise NoDataForDate(f"行业 {sector_id} 无成分股档位数据",
                                detail={"sector_id": sector_id})

        sec_name = self._conn.execute(
            "SELECT display_name FROM subject WHERE subject_id=?", (sector_id,)).fetchone()
        sec_name = sec_name[0] if sec_name else f"行业{sector_id}"
        sec_id = f"sector:{sector_id}"

        ranked = sorted(mine, key=lambda r: abs(getattr(r, net_col)), reverse=True)
        top = ranked[:top_stocks]
        rest = ranked[top_stocks:]
        sec_net = sum(getattr(r, net_col) for r in mine)
        sec_gross = sum(getattr(r, gross_col) for r in mine
                        if gross_col and getattr(r, gross_col) is not None)

        nodes = [FlowNode(
            id=sec_id, name=sec_name, depth=1, net_yuan=cents_to_yuan_str(sec_net),
            gross_yuan=cents_to_yuan_str(sec_gross) if gross_col else None,
            direction=_direction(sec_net), subject_id=sector_id)]
        links = []
        for r in top:
            net = getattr(r, net_col)
            g = getattr(r, gross_col) if gross_col else None
            nodes.append(FlowNode(
                id=f"stock:{r.subject_id}", name=r.display_name, depth=2,
                net_yuan=cents_to_yuan_str(net),
                gross_yuan=cents_to_yuan_str(g) if g is not None else None,
                direction=_direction(net), subject_id=r.subject_id))
            links.append(self._link(sec_id, f"stock:{r.subject_id}", net))
        if rest:
            rest_net = sum(getattr(r, net_col) for r in rest)
            nodes.append(FlowNode(
                id=f"other:{sector_id}", name=f"其他{len(rest)}只", depth=2,
                net_yuan=cents_to_yuan_str(rest_net), direction=_direction(rest_net), subject_id=None))
            links.append(self._link(sec_id, f"other:{sector_id}", rest_net))

        prov = DataProvenance(
            trade_date=trade_date, source_type=SourceType.DAILY_FINAL,
            source_id=self._source, captured_at="EOD", is_stale=False)
        return FlowTopologyResult(
            trade_date=trade_date, tier=tier, provenance=prov,
            coverage_pct="100.0", constituent_count=len(mine), expected_count=len(mine),
            nodes=nodes, links=links)
