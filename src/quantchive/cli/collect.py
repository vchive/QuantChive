"""不依赖 Web 的采集入口（通用 subject×observation 模型）。

用法:
  uv run quantchive-collect --target sector [--sector-type industry,concept]
  uv run quantchive-collect --target stock          # 全市场个股 + 大盘求和
  uv run quantchive-collect --target etf            # 全 ETF
供手动灌数据 / cron。
"""

from __future__ import annotations

import argparse
import sys

from quantchive.core.db import connect, transaction
from quantchive.core.logging import get_logger
from quantchive.core.settings import get_settings
from quantchive.dao.db_init import init_db
from quantchive.dao.observation_dao import ObservationDao
from quantchive.dao.run_dao import RunDao
from quantchive.dao.subject_dao import SubjectDao
from quantchive.datasource.em_etf_src import EastMoneyEtfSource
from quantchive.datasource.em_sector_src import EastMoneySource
from quantchive.datasource.em_stock_src import EastMoneyStockSource
from quantchive.models.enums import Caliber, SectorType
from quantchive.service.ingest_service import (
    CollectRequest,
    backfill_daily_flow,
    collect_all_sector_members,
    collect_etf_observations_once,
    collect_sector_observations_once,
    collect_stock_observations_once,
)
from quantchive.cli.collect_lock import CollectLock, CollectLockError
from quantchive.datasource._http_client import default_http_get
from quantchive.service.market_aggregate import MarketAggregator

_log = get_logger(__name__)


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="quantchive-collect")
    parser.add_argument("--target", default="sector",
                        choices=["sector", "stock", "etf", "members", "backfill"])
    parser.add_argument("--sector-type", default="industry,concept")
    parser.add_argument("--scope", default="stock", choices=["stock"],
                        help="backfill: 回填个股历史")
    parser.add_argument("--source", default="baostock", choices=["baostock", "sina", "eastmoney"],
                        help="backfill: 历史源（baostock 价量/不限流；sina 资金流/不限流；eastmoney 资金流/受限流）")
    parser.add_argument("--metric", default="price_hist", choices=["price_hist", "money_flow"],
                        help="backfill: 指标类别")
    parser.add_argument("--days", type=int, default=30, help="backfill: 回填交易日数")
    parser.add_argument("--limit", type=int, default=None,
                        help="members/backfill: 限制主体数（调试）")
    args = parser.parse_args(argv)

    settings = get_settings()
    conn = connect(settings.db_path)
    init_db(conn)

    # 采集互斥：防两个 collect 并发写 SQLite 单写者库（否则 run_id 交叉/行错乱）
    try:
        _lock = CollectLock(settings.db_path)
        _lock.acquire()
    except CollectLockError as exc:
        print(f"✗ {exc}", file=sys.stderr)
        sys.exit(2)

    # backfill：历史回填（逐主体自动提交，不包大事务）
    if args.target == "backfill":
        def _prog(done, total, name):
            if done % 100 == 0 or done == total:
                print(f"  回填 {done}/{total} … {name}", flush=True)

        # 新浪个股历史资金流（独立于东财限流，多源扩展）
        if args.source == "sina":
            from quantchive.datasource.sina_flow_src import SinaFlowSource
            from quantchive.service.ingest_service import backfill_flow_history
            summary = backfill_flow_history(
                conn, source=SinaFlowSource(), days=args.days, source_code="sina_flow",
                limit=args.limit, progress=_prog)
            _log.info("资金流历史回填完成", extra={"context": summary})
            print(f"backfill[sina/money_flow]: 主体 {summary['subjects_ok']}/{summary['subjects_total']} "
                  f"· 日线行 {summary['rows_written']} · {summary['days']}天 "
                  f"· 跳过已存 {summary['skipped_covered']} · 失败 {summary['subjects_failed']}")
            sys.exit(1 if summary["rows_written"] == 0 and summary["skipped_covered"] == 0 else 0)

        # baostock 价量历史（独立于东财限流，US2/D4）
        if args.source == "baostock":
            from quantchive.datasource.baostock_src import BaostockSource
            from quantchive.service.ingest_service import backfill_price_history
            summary = backfill_price_history(
                conn, source=BaostockSource(), scope=args.scope, days=args.days,
                source_code="baostock", limit=args.limit, progress=_prog)
            _log.info("历史回填完成", extra={"context": summary})
            print(f"backfill[baostock/{summary['scope']}]: 主体 "
                  f"{summary['subjects_ok']}/{summary['subjects_total']} · 日线行 {summary['rows_written']} "
                  f"· {summary['days']}天 · 跳过已存 {summary['skipped_covered']} · 失败 {summary['subjects_failed']}")
            sys.exit(1 if summary["rows_written"] == 0 and summary["skipped_covered"] == 0 else 0)

        # 东财资金流历史（受限流，节流缓解）
        from quantchive.datasource.em_kline_src import EastMoneyDailyFlowSource
        summary = backfill_daily_flow(
            conn, source=EastMoneyDailyFlowSource(http_get=default_http_get()), scope=args.scope,
            days=args.days, limit=args.limit, sleep_sec=0.25, progress=_prog)
        _log.info("历史回填完成", extra={"context": summary})
        print(f"backfill[{summary['scope']}]: 主体 {summary['subjects_ok']}/{summary['subjects_total']} "
              f"· 日线行 {summary['rows_written']} · {summary['days']}天 "
              f"· 限流跳过 {summary['throttled']} · 失败 {summary['subjects_failed']}")
        sys.exit(1 if summary["rows_written"] == 0 else 0)

    # members：遍历 991 板块，每板块独立自动提交（不包进单一大事务，防长锁/崩溃全丢）
    if args.target == "members":
        def _prog(done, total, name):
            if done % 50 == 0 or done == total:
                print(f"  成分采集 {done}/{total} … {name}", flush=True)
        summary = collect_all_sector_members(
            conn, source=EastMoneySource(http_get=default_http_get()), adapter_version="push2delay-v2",
            limit=args.limit, progress=_prog)
        _log.info("成分采集完成", extra={"context": summary})
        print(f"members: 板块 {summary['boards_ok']}/{summary['boards_total']} 成功 "
              f"· 成分行 {summary['members_written']} · 失败 {summary['boards_failed']}")
        sys.exit(1 if summary["members_written"] == 0 else 0)

    with transaction(conn):
        if args.target == "sector":
            sector_types = tuple(SectorType(s.strip()) for s in args.sector_type.split(","))
            req = CollectRequest(
                caliber=Caliber.EASTMONEY, source_code="eastmoney",
                sector_types=sector_types, adapter_version="push2delay-v2")
            result = collect_sector_observations_once(
                req, source=EastMoneySource(http_get=default_http_get()), observation_dao=ObservationDao(conn),
                run_dao=RunDao(conn), subject_dao=SubjectDao(conn))
        elif args.target == "stock":
            result = collect_stock_observations_once(
                source=EastMoneyStockSource(http_get=default_http_get()), observation_dao=ObservationDao(conn),
                run_dao=RunDao(conn), subject_dao=SubjectDao(conn),
                aggregator=MarketAggregator(conn, threshold=settings.market_coverage_threshold),
                adapter_version="push2delay-stock-v1")
        else:  # etf
            result = collect_etf_observations_once(
                source=EastMoneyEtfSource(http_get=default_http_get()), observation_dao=ObservationDao(conn),
                subject_dao=SubjectDao(conn), run_dao=RunDao(conn),
                adapter_version="push2delay-etf-v1")

    _log.info("collect 完成", extra={"context": {
        "target": args.target, "status": result.status.value, "rows": result.rows_written,
        "failed": result.sectors_failed, "trade_date": result.trade_date}})
    print(f"target={args.target} status={result.status.value} rows={result.rows_written} "
          f"failed={result.sectors_failed} date={result.trade_date} slot={result.batch_slot}")
    if result.rows_written == 0:
        sys.exit(1)


if __name__ == "__main__":
    main()
