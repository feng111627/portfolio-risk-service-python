"""业务场景：敞口聚合、集中度/杠杆限额超限、缺失数据缺省语义。"""
from __future__ import annotations

import pytest

from risk_service.limits import aggregate_exposure
from risk_service.models import (
    AssetClass,
    ErrorCode,
    Limits,
    Portfolio,
    Position,
    RiskError,
)

pytestmark = pytest.mark.scenario("敞口聚合与限额")


def test_exposure_aggregates_by_ccy_and_asset_class(multi_portfolio, snapshot):
    """场景：按币种与资产类别聚合多/空头敞口。"""
    res = aggregate_exposure(multi_portfolio, snapshot)
    aapl = 100.0 * snapshot.prices["EQ_AAPL"]
    tsla = 20.0 * snapshot.prices["EQ_TSLA"]
    baba = 100.0 * snapshot.prices["EQ_BABA"] / snapshot.fx_rates["CNY"]
    gold = 2.0 * snapshot.prices["CMD_GOLD"]
    assert res.by_ccy["USD"] == pytest.approx(aapl + tsla + gold)
    assert res.by_ccy["CNY"] == pytest.approx(baba)
    assert res.by_asset_class["equity"] == pytest.approx(aapl + tsla + baba)
    assert res.by_asset_class["commodity"] == pytest.approx(gold)
    assert res.net == pytest.approx(aapl + tsla + baba + gold)


def test_concentration_breach_flagged(multi_portfolio, snapshot):
    """场景：单一资产类别集中度超阈值 -> LIMIT_BREACH 逐条列出。"""
    res = aggregate_exposure(
        multi_portfolio, snapshot,
        Limits(max_concentration=0.5, max_leverage=100.0, net_equity_base_ccy=1e9),
    )
    names = {b.limit_name for b in res.breaches}
    assert "concentration" in names
    breach = next(b for b in res.breaches if b.limit_name == "concentration")
    assert breach.observed > breach.limit


def test_leverage_breach_flagged(multi_portfolio, snapshot):
    """场景：gross/net_equity 超杠杆阈值 -> LIMIT_BREACH。"""
    res = aggregate_exposure(
        multi_portfolio, snapshot,
        Limits(max_concentration=1.0, max_leverage=1.0, net_equity_base_ccy=1000.0),
    )
    breach = next(b for b in res.breaches if b.limit_name == "leverage")
    assert breach.observed == pytest.approx(res.gross / 1000.0)
    assert breach.observed > breach.limit


def test_no_breach_when_within_limits(multi_portfolio, snapshot):
    """场景：限额宽松时不产生任何 breach。"""
    res = aggregate_exposure(
        multi_portfolio, snapshot,
        Limits(max_concentration=1.0, max_leverage=1000.0, net_equity_base_ccy=1e9),
    )
    assert res.breaches == ()


def test_short_positions_reduce_net_but_add_to_gross(snapshot):
    """场景：空头减少净敞口但增加毛敞口，杠杆按毛额计。"""
    p = Portfolio(
        "ls",
        (
            Position("long", "EQ_AAPL", 100.0, "USD", AssetClass.EQUITY),
            Position("short", "EQ_TSLA", -100.0, "USD", AssetClass.EQUITY),
        ),
        "USD",
    )
    res = aggregate_exposure(p, snapshot)
    aapl = 100.0 * snapshot.prices["EQ_AAPL"]
    tsla = -100.0 * snapshot.prices["EQ_TSLA"]
    assert res.total_short < 0
    assert res.net == pytest.approx(aapl + tsla)
    assert res.gross == pytest.approx(abs(aapl) + abs(tsla))


def test_missing_price_and_fx_are_stable_defaults(snapshot):
    """场景：缺行情与缺汇率的头寸均跳过、列入 missing_positions，绝不按 0 计。"""
    p = Portfolio(
        "miss",
        (
            Position("no-price", "NO_FACTOR", 1.0, "USD", AssetClass.EQUITY),
            Position("no-fx", "EQ_AAPL", 1.0, "CHF", AssetClass.EQUITY),
            Position("ok", "EQ_AAPL", 1.0, "USD", AssetClass.EQUITY),
        ),
        "USD",
    )
    res = aggregate_exposure(p, snapshot)
    assert set(res.missing_positions) == {"no-price", "no-fx"}
    assert res.gross == pytest.approx(snapshot.prices["EQ_AAPL"])


def test_base_ccy_implicit_fx_is_one(snapshot):
    """场景：本币头寸即使无显式汇率也按 1.0 计价。"""
    p = Portfolio("b", (Position("x", "EQ_AAPL", 3.0, "USD", AssetClass.EQUITY),), "USD")
    res = aggregate_exposure(p, snapshot)
    assert res.gross == pytest.approx(3.0 * snapshot.prices["EQ_AAPL"])


def test_limit_config_nonpositive_equity_rejected(multi_portfolio, snapshot):
    """场景：限额配置 net_equity<=0 -> LIMIT_CONFIG_INVALID。"""
    with pytest.raises(RiskError) as ei:
        aggregate_exposure(
            multi_portfolio, snapshot,
            Limits(max_concentration=0.5, max_leverage=1.0, net_equity_base_ccy=0.0),
        )
    assert ei.value.code is ErrorCode.LIMIT_CONFIG_INVALID
