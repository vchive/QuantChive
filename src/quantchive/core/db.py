"""SQLite 连接工厂（data-model.md 全局约定）。

- WAL 模式：盘中"1-3分钟写 + 页面并发读"，读不阻塞写。
- foreign_keys=ON：否则 FK 是死约束（宪章 II/V 引用完整性失效）。
- busy_timeout：争锁时等待而非立即 SQLITE_BUSY。
- 写连接串行化（ingest 专用），读连接分离。
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

_PRAGMAS = (
    "PRAGMA foreign_keys = ON;",
    "PRAGMA journal_mode = WAL;",
    "PRAGMA busy_timeout = 5000;",
)


def _apply_pragmas(conn: sqlite3.Connection) -> None:
    for stmt in _PRAGMAS:
        conn.execute(stmt)


def connect(db_path: Path | str) -> sqlite3.Connection:
    """建立一个应用了标准 pragma 的连接。调用方负责关闭。"""
    path = Path(db_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), isolation_level=None)  # autocommit; 显式事务
    conn.row_factory = sqlite3.Row
    _apply_pragmas(conn)
    return conn


@contextmanager
def get_connection(db_path: Path | str) -> Iterator[sqlite3.Connection]:
    conn = connect(db_path)
    try:
        yield conn
    finally:
        conn.close()


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """显式事务：成功提交，异常回滚（禁无声吞异常，宪章 V）。"""
    conn.execute("BEGIN;")
    try:
        yield conn
    except Exception:
        conn.execute("ROLLBACK;")
        raise
    else:
        conn.execute("COMMIT;")
