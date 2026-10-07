"""快照存储：原子刷新，读写并发一致。

并发保证：
- MarketSnapshot 本身是不可变对象（frozen dataclass，内部只放 dict/tuple，
  刷新方必须整体构造新对象，不得原地修改）；
- refresh() 在锁内一次性替换引用，不存在"更新到一半"的中间态；
- get() 读到的是引用快照，要么是完整的旧版本，要么是完整的新版本；
- 读侧拿到引用后，即使随后发生刷新，其计算仍基于同一完整快照，结果可复现。
"""

from __future__ import annotations

import threading

from .models import MarketSnapshot


class SnapshotStore:
    """持有当前市场快照；刷新整体替换，读取永远拿到完整快照。"""

    def __init__(self, initial: MarketSnapshot) -> None:
        self._lock = threading.Lock()
        self._snapshot = initial

    def get(self) -> MarketSnapshot:
        """读取当前完整快照（不可变对象，读侧无需加锁）。"""
        return self._snapshot

    def refresh(self, new_snapshot: MarketSnapshot) -> None:
        """原子地用新快照整体替换旧快照。"""
        with self._lock:
            self._snapshot = new_snapshot

    @property
    def version(self) -> int:
        return self._snapshot.version
