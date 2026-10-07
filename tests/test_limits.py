"""业务场景：敞口聚合、限额校验与缺失数据的缺省语义。"""

from __future__ import annotations

import math

import pytest

from risk_service.errors import MissingFxRateError, MissingMarketDataError
from risk_service.limits import aggregate_exposure, check_limits
from risk_service.models import LimitRule, MarketSnapshot, Portfolio, Position


class TestExposureAggregation:
    """场景：按币种与资产类别聚合敞口。"""

    def test_按币种与资产类别聚合(self, portfolio, snapshot):
        rep = aggregate_exposure(portfolio, snapshot)
        assert rep.base_currency == "CNY"
        assert rep.by_currency == {"USD": 7000.0, "CNY": 1000.0}
        assert rep.by_asset_class == {"equity": 7000.0, "bond": 1000.0}
        assert rep.gross_exposure == 8000.0
        assert rep.net_exposure == 8000.0

    def test_空头头寸计入总敞口且抵减净敞口(self, snapshot):
        pf = Portfolio(
            "p", "CNY",
            (
                Position("AAA", "equity", "USD", 10.0, "EQ_US"),
                Position("BBB", "bond", "CNY", -20.0, "CN_RATES"),
            ),
        )
        rep = aggregate_exposure(pf, snapshot)
        assert rep.gross_exposure == 8000.0
        assert rep.net_exposure == 6000.0

    def test_缺行情明确失败(self, portfolio):
        snap = MarketSnapshot(prices={}, fx_rates={"USD/CNY": 7.0}, factor_returns={})
        with pytest.raises(MissingMarketDataError):
            aggregate_exposure(portfolio, snap)

    def test_缺汇率明确失败(self, portfolio):
        snap = MarketSnapshot(
            prices={"AAA": 100.0, "BBB": 50.0}, fx_rates={}, factor_returns={}
        )
        with pytest.raises(MissingFxRateError):
            aggregate_exposure(portfolio, snap)


class TestLimitChecks:
    """场景：集中度与杠杆限额的超限判定。"""

    def test_集中度超限被检出(self, portfolio, snapshot):
        rep = aggregate_exposure(portfolio, snapshot)
        breaches = check_limits(rep, [LimitRule("concentration", 0.8, "equity")])
        assert len(breaches) == 1
        assert breaches[0].observed == pytest.approx(7000.0 / 8000.0)

    def test_未超限时不产生记录(self, portfolio, snapshot):
        rep = aggregate_exposure(portfolio, snapshot)
        assert check_limits(rep, [LimitRule("concentration", 0.9, "equity")]) == []

    def test_杠杆超限被检出(self, snapshot):
        # 多空对冲：总敞口大、净敞口小 -> 杠杆高
        pf = Portfolio(
            "p", "CNY",
            (
                Position("AAA", "equity", "USD", 10.0, "EQ_US"),
                Position("BBB", "bond", "CNY", -18.0, "CN_RATES"),
            ),
        )
        rep = aggregate_exposure(pf, snapshot)
        # gross = 7900, net = 6100 -> leverage ≈ 1.295
        breaches = check_limits(rep, [LimitRule("leverage", 1.2)])
        assert len(breaches) == 1
        assert breaches[0].observed == pytest.approx(7900.0 / 6100.0)

    def test_缺省语义_不存在的资产类别集中度为零(self, portfolio, snapshot):
        rep = aggregate_exposure(portfolio, snapshot)
        assert check_limits(rep, [LimitRule("concentration", 0.0, "fx")]) == []

    def test_缺省语义_零敞口组合不超限(self):
        pf = Portfolio("p", "CNY", ())
        snap = MarketSnapshot(prices={}, fx_rates={}, factor_returns={})
        rep = aggregate_exposure(pf, snap)
        rules = [LimitRule("concentration", 0.0, "equity"), LimitRule("leverage", 0.0)]
        assert check_limits(rep, rules) == []

    def test_缺省语义_净敞口为零而总敞口非零时杠杆为无穷大(self, snapshot):
        pf = Portfolio(
            "p", "CNY",
            (
                Position("BBB", "bond", "CNY", 20.0, "CN_RATES"),
                Position("BBB", "bond", "CNY", -20.0, "CN_RATES"),
            ),
        )
        rep = aggregate_exposure(pf, snapshot)
        breaches = check_limits(rep, [LimitRule("leverage", 100.0)])
        assert len(breaches) == 1
        assert breaches[0].observed == math.inf

    def test_未知限额类型明确报错(self, portfolio, snapshot):
        rep = aggregate_exposure(portfolio, snapshot)
        with pytest.raises(ValueError):
            check_limits(rep, [LimitRule("unknown", 1.0)])
