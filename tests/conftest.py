"""pytest 共享夹具与业务场景覆盖汇总。

测试用例用 @pytest.mark.scenario("场景名") 归类；会话结束时打印
“覆盖了哪些业务场景、每个场景的用例与结果”，便于从日志核对覆盖面。
"""
from __future__ import annotations

import pytest

from risk_service.data import build_initial_snapshot
from risk_service.models import AssetClass, Portfolio, Position, Snapshot
from risk_service.store import SnapshotStore


@pytest.fixture
def snapshot() -> Snapshot:
    return build_initial_snapshot()


@pytest.fixture
def store(snapshot: Snapshot) -> SnapshotStore:
    return SnapshotStore(snapshot)


@pytest.fixture
def equity_portfolio() -> Portfolio:
    return Portfolio(
        name="eq",
        base_ccy="USD",
        positions=(Position("p-aapl", "EQ_AAPL", 100.0, "USD", AssetClass.EQUITY),),
    )


@pytest.fixture
def multi_portfolio() -> Portfolio:
    return Portfolio(
        name="multi",
        base_ccy="USD",
        positions=(
            Position("p-aapl", "EQ_AAPL", 100.0, "USD", AssetClass.EQUITY),
            Position("p-tsla", "EQ_TSLA", 20.0, "USD", AssetClass.EQUITY),
            Position("p-baba", "EQ_BABA", 100.0, "CNY", AssetClass.EQUITY),
            Position("p-gold", "CMD_GOLD", 2.0, "USD", AssetClass.COMMODITY),
        ),
    )


@pytest.fixture
def client(store):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from risk_service.api import create_router, install_exception_handlers

    app = FastAPI()
    app.include_router(create_router(store))
    install_exception_handlers(app)
    return TestClient(app)


# ---------------- 业务场景覆盖汇总 ----------------


def pytest_configure(config):
    config._risk_coverage = {}  # scenario -> list[(name, outcome)]


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    outcome.get_result().risk_item = item


def pytest_runtest_logreport(report):
    if report.when != "call":
        return
    item = getattr(report, "risk_item", None)
    if item is None:
        return
    marker = item.get_closest_marker("scenario")
    scenario = marker.args[0] if marker and marker.args else "未归类"
    item.config._risk_coverage.setdefault(scenario, []).append(
        (item.name, report.outcome)
    )


def pytest_terminal_summary(terminalreporter, exitstatus, config):
    coverage = config._risk_coverage
    terminalreporter.write_sep("=", "组合风险服务 · 业务场景覆盖")
    total = 0
    for scenario in sorted(coverage):
        cases = coverage[scenario]
        total += len(cases)
        passed = sum(1 for _n, o in cases if o == "passed")
        verdict = "全部通过" if passed == len(cases) else "存在失败"
        terminalreporter.write_line(
            f"[{scenario}] 用例 {len(cases)} 个，通过 {passed} —— {verdict}"
        )
        for name, outcome in cases:
            mark = {"passed": "PASS", "failed": "FAIL", "skipped": "SKIP"}.get(
                outcome, outcome
            )
            terminalreporter.write_line(f"    - {mark:<4} {name}")
    terminalreporter.write_sep("=", f"共覆盖业务场景 {len(coverage)} 个 / 用例 {total} 个")
