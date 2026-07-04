"""采集互斥锁（spec003 · 防并发采集写坏 SQLite 单写者库）。

真实教训：两个 quantchive-collect 同时跑 → 并发写 SQLite（单写者）→ run_id 交叉、
行归属错乱、且可能"报成功实则数据错乱"（违宪章 V 审计诚实）。用文件锁（fcntl 排他 +
PID 记录）让第二个采集检测到已有采集在跑就拒绝启动。锁随进程退出自动释放（fcntl 特性）。
"""

from __future__ import annotations

import atexit
import errno
import fcntl
import os
from pathlib import Path


class CollectLockError(RuntimeError):
    """已有采集在跑，拒绝并发启动。"""


class CollectLock:
    """基于 fcntl 排他锁的采集互斥。with CollectLock(db_path): ...

    锁文件放库文件旁（.collect.lock）。持有者 PID 写入锁文件供诊断。
    非阻塞——拿不到锁立即抛 CollectLockError（不排队等）。
    """

    def __init__(self, db_path: str) -> None:
        self._path = Path(db_path).with_name(".collect.lock")
        self._fd: int | None = None

    def acquire(self) -> None:
        self._fd = os.open(self._path, os.O_RDWR | os.O_CREAT, 0o644)
        try:
            fcntl.flock(self._fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as exc:
            os.close(self._fd)
            self._fd = None
            if exc.errno in (errno.EAGAIN, errno.EACCES):
                other = self._read_holder()
                raise CollectLockError(
                    f"已有采集进程在运行（PID {other or '?'}）——SQLite 单写者，"
                    f"拒绝并发采集以防数据错乱。等它结束或 kill 后重试。") from exc
            raise
        os.ftruncate(self._fd, 0)
        os.write(self._fd, str(os.getpid()).encode())
        os.fsync(self._fd)
        atexit.register(self.release)

    def _read_holder(self) -> str | None:
        try:
            return self._path.read_text().strip() or None
        except OSError:
            return None

    def release(self) -> None:
        if self._fd is not None:
            try:
                fcntl.flock(self._fd, fcntl.LOCK_UN)
                os.close(self._fd)
            except OSError:
                pass
            self._fd = None

    def __enter__(self) -> "CollectLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()
