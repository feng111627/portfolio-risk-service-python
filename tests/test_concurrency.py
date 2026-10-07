"""业务场景：快照刷新期间并发计算只能读到完整旧快照或完整新快照。"""
from __future__ import annotations

import threading

import pytest

from risk_service.models import (
    AssetClass,
    Portfolio,
    Position,
    Snapshot,
    VaRRequest,
)
from risk_service.risk_metrics import historical_var_es
from risk_service.store import SnapshotStore

pytestmark = pytest.mark.scenario("快照刷新并发一致性")


def _snap(version: int) -> Snapshot:
    # 每个版本整份自洽：价格/汇率/历史长度都一致，绝不出现半更新状态。
    return Snapshot(
        version=version,
        prices={"F": 10.0 * version},
        fx_rates={"USD": 1.0},
        history={"F": tuple(0.001 * version for _ in range(200))},
    )


def test_readers_always_see_complete_old_or_new_snapshot():
    """场景：大量并发读 + 持续写，读到的快照必须是某个完整版本。

    校验点：每次读取后立刻做计算，且检查 prices/history/version 三者
    自洽（F 价=10*version，收益率=0.001*version）。任何半成品都会断言失败。
    """
    store = SnapshotStore(_snap(1))
    portfolio = Portfolio(
        "p", (Position("x", "F", 10.0, "USD", AssetClass.EQUITY),), "USD"
    )
    stop = threading.Event()
    errors: list[Exception] = []
    seen_versions: set[int] = set()
    lock = threading.Lock()

    def reader() -> None:
        while not stop.is_set():
            try:
                snap = store.get()
                # 快照内部自洽性（原子性的可观察证据）。
                assert snap.prices["F"] == pytest.approx(10.0 * snap.version)
                assert snap.history["F"][0] == pytest.approx(0.001 * snap.version)
                res = historical_var_es(VaRRequest(portfolio, 0.95, 1), snap)
                assert res.snapshot_version == snap.version
                with lock:
                    seen_versions.add(snap.version)
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

    def writer() -> None:
        v = 1
        while not stop.is_set():
            v += 1
            # 新快照在锁外完整构造，replace 只做指针替换。
            store.replace(_snap(v))

    threads = [threading.Thread(target=reader) for _ in range(6)]
    threads.append(threading.Thread(target=writer))
    for t in threads:
        t.start()
    threading.Event().wait(0.4)
    stop.set()
    for t in threads:
        t.join(timeout=2)

    assert errors == []
    # 读者确实观察到了至少两个完整版本，证明刷新发生且每个版本都自洽。
    assert len(seen_versions) >= 2


def test_get_before_load_fails_explicitly():
    """场景：未载入快照时读取，明确失败而非静默返回 None。"""
    from risk_service.models import ErrorCode, RiskError

    with pytest.raises(RiskError) as ei:
        SnapshotStore().get()
    assert ei.value.code is ErrorCode.MISSING_RISK_FACTOR
