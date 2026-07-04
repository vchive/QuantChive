"""采集互斥锁——第二个采集拒绝并发启动、锁随释放可再取（防 SQLite 单写者错乱）。"""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from quantchive.cli.collect_lock import CollectLock, CollectLockError


def _db():
    return str(Path(tempfile.mkdtemp()) / "q.db")


def test_second_acquire_refused() -> None:
    db = _db()
    a = CollectLock(db)
    a.acquire()
    try:
        b = CollectLock(db)
        with pytest.raises(CollectLockError):
            b.acquire()                     # 已被持有 → 拒绝并发
    finally:
        a.release()


def test_release_allows_reacquire() -> None:
    db = _db()
    a = CollectLock(db)
    a.acquire()
    a.release()
    b = CollectLock(db)
    b.acquire()                             # 释放后可再取
    b.release()


def test_context_manager() -> None:
    db = _db()
    with CollectLock(db):
        other = CollectLock(db)
        with pytest.raises(CollectLockError):
            other.acquire()
    # 退出 with 后锁已释放
    again = CollectLock(db)
    again.acquire()
    again.release()


def test_holder_pid_recorded() -> None:
    import os
    db = _db()
    a = CollectLock(db)
    a.acquire()
    try:
        holder = Path(db).with_name(".collect.lock").read_text().strip()
        assert holder == str(os.getpid())   # 锁文件记持有者 PID（诊断用）
    finally:
        a.release()
