"""业务场景：快照原子刷新与刷新期间的并发一致性。"""

from __future__ import annotations

import threading

from risk_service.models import MarketSnapshot
from risk_service.snapshot import SnapshotStore


def _make_snapshot(version: int) -> MarketSnapshot:
    """构造一份内部自洽的快照：所有字段都携带相同的版本标记。

    若读到"更新到一半"的状态，字段间的版本标记就会不一致。
    """
    return MarketSnapshot(
        prices={"AAA": float(version), "BBB": float(version)},
        fx_rates={"USD/CNY": float(version)},
        factor_returns={"EQ_US": (float(version),) * 8},
        version=version,
    )


def _assert_consistent(snap: MarketSnapshot) -> None:
    v = float(snap.version)
    assert snap.prices["AAA"] == v
    assert snap.prices["BBB"] == v
    assert snap.fx_rates["USD/CNY"] == v
    assert all(r == v for r in snap.factor_returns["EQ_US"])


class TestAtomicRefresh:
    """场景：刷新是原子的，读取只能看到完整的旧或新快照。"""

    def test_刷新后读到完整新快照(self):
        store = SnapshotStore(_make_snapshot(1))
        store.refresh(_make_snapshot(2))
        snap = store.get()
        assert snap.version == 2
        _assert_consistent(snap)

    def test_刷新前拿到的旧快照在刷新后仍完整可用(self):
        store = SnapshotStore(_make_snapshot(1))
        old = store.get()
        store.refresh(_make_snapshot(2))
        # 旧快照不被原地修改
        _assert_consistent(old)
        assert old.version == 1
        assert store.get().version == 2

    def test_刷新期间并发读取只能读到完整快照(self):
        store = SnapshotStore(_make_snapshot(0))
        stop = threading.Event()
        errors: list[AssertionError] = []

        def reader() -> None:
            while not stop.is_set():
                try:
                    _assert_consistent(store.get())
                except AssertionError as exc:  # pragma: no cover - 失败路径
                    errors.append(exc)
                    return

        threads = [threading.Thread(target=reader) for _ in range(8)]
        for t in threads:
            t.start()
        for version in range(1, 500):
            store.refresh(_make_snapshot(version))
        stop.set()
        for t in threads:
            t.join()
        assert errors == []
        assert store.get().version == 499
