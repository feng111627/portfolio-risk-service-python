"""原子快照存储。

读多写少场景：用一把锁保护一个不可变 Snapshot 引用。
刷新时先在锁外把整份新 Snapshot 构造好，再在临界区内做一次
指针替换；因此任何并发计算请求拿到的都是完整的旧快照或
完整的新快照，绝不会读到更新到一半的中间状态。
"""
from __future__ import annotations

import threading

from .models import RiskError, ErrorCode, Snapshot


class SnapshotStore:
    def __init__(self, initial: Snapshot | None = None) -> None:
        self._lock = threading.RLock()
        self._snapshot = initial

    def get(self) -> Snapshot:
        with self._lock:
            snap = self._snapshot
        if snap is None:
            # 尚未载入任何快照：明确失败，绝不返回 None 让上层静默。
            raise RiskError(ErrorCode.MISSING_RISK_FACTOR, "no snapshot loaded")
        return snap

    def replace(self, new_snapshot: Snapshot) -> None:
        """原子替换整份快照。

        Snapshot 本身为不可变值对象，构造发生在锁外；
        临界区内仅一次引用赋值，读者不可能看到半成品。
        """
        if not isinstance(new_snapshot, Snapshot):
            raise TypeError("new_snapshot must be Snapshot")
        with self._lock:
            self._snapshot = new_snapshot
