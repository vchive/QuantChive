"""基金净值查询服务（T045，US4，旁路物理隔离）。

**物理隔离**（D4/US4 裁定）：本服务
- 不持任何 DAO 写句柄（构造只吃 FundSource，无 conn/dao）；
- 不进采集调度 JOBS；
- get_fund_nav 返回 note='not persisted'，明示净值不落库、每次实时查。
这是"只读查询与动作型能力分层"的实践——旁路查询不污染持久化观测流。
"""

from __future__ import annotations

from quantchive.datasource.fund_src import FundSource
from quantchive.service.dto import FundNavPointView, FundNavResult


class FundLookupService:
    """开放式基金净值旁路查询。只依赖 FundSource，无持久化句柄。"""

    def __init__(self, source: FundSource) -> None:
        self._source = source     # 仅数据源；无 dao、无 conn（物理隔离）

    def get_fund_nav(self, *, fund_code: str, days: int = 60) -> FundNavResult:
        points = self._source.fetch_nav(fund_code=fund_code, days=days)
        return FundNavResult(
            fund_code=fund_code,
            points=[FundNavPointView(nav_date=p.nav_date, unit_nav=p.unit_nav,
                                     growth_pct=p.growth_pct) for p in points],
            note="not persisted",
            source_id=self._source.source_id,
        )
