"""共享夹具：确定性的本地快照与演示组合。"""

from __future__ import annotations

import pytest

from risk_service.models import MarketSnapshot, Portfolio, Position


@pytest.fixture()
def snapshot() -> MarketSnapshot:
    """两标的、两因子、一条汇率的确定性快照。"""
    return MarketSnapshot(
        prices={"AAA": 100.0, "BBB": 50.0},
        fx_rates={"USD/CNY": 7.0},
        factor_returns={
            "EQ_US": tuple([0.01, -0.02, 0.005, -0.01, 0.003] * 20),  # 100 个样本
            "CN_RATES": tuple([0.001, -0.001] * 50),  # 100 个样本
        },
        version=1,
    )


@pytest.fixture()
def portfolio() -> Portfolio:
    """一多一空、跨币种跨资产类别的演示组合。"""
    return Portfolio(
        portfolio_id="demo",
        base_currency="CNY",
        positions=(
            Position("AAA", "equity", "USD", 10.0, "EQ_US"),
            Position("BBB", "bond", "CNY", 20.0, "CN_RATES"),
        ),
    )
