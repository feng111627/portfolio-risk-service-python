"""业务场景：历史模拟法 VaR/ES 主流程与参数、样本边界。"""

from __future__ import annotations

import math

import pytest

from risk_service.errors import (
    EmptySampleError,
    InsufficientSampleError,
    InvalidConfidenceLevelError,
    InvalidHorizonError,
    MissingFxRateError,
    MissingHistoryError,
    MissingMarketDataError,
)
from risk_service.models import MarketSnapshot, Portfolio, Position
from risk_service.var_es import compute_var_es


class TestVarEsMainFlow:
    """场景：正常参数下的 VaR/ES 计算。"""

    def test_置信水平与持有期可由参数指定且结果为正(self, portfolio, snapshot):
        r = compute_var_es(portfolio, snapshot, confidence=0.95, horizon_days=1)
        assert r.confidence == 0.95
        assert r.horizon_days == 1
        assert r.sample_size == 100
        assert r.var > 0
        assert r.es >= r.var  # ES 不差于 VaR

    def test_持有期按平方根时间缩放(self, portfolio, snapshot):
        r1 = compute_var_es(portfolio, snapshot, 0.95, 1)
        r10 = compute_var_es(portfolio, snapshot, 0.95, 10)
        assert r10.var == pytest.approx(r1.var * math.sqrt(10))
        assert r10.es == pytest.approx(r1.es * math.sqrt(10))

    def test_相同输入结果可复现(self, portfolio, snapshot):
        a = compute_var_es(portfolio, snapshot, 0.99, 5)
        b = compute_var_es(portfolio, snapshot, 0.99, 5)
        assert a == b

    def test_更高置信水平的VaR不小于较低置信水平(self, portfolio, snapshot):
        lo = compute_var_es(portfolio, snapshot, 0.90, 1)
        hi = compute_var_es(portfolio, snapshot, 0.99, 1)
        assert hi.var >= lo.var


class TestInvalidParams:
    """场景：置信水平 / 持有期越界必须被明确拒绝。"""

    @pytest.mark.parametrize("bad", [0.0, 1.0, -0.5, 1.5])
    def test_置信水平越界(self, portfolio, snapshot, bad):
        with pytest.raises(InvalidConfidenceLevelError):
            compute_var_es(portfolio, snapshot, bad, 1)

    @pytest.mark.parametrize("bad", [0, -1])
    def test_持有期越界(self, portfolio, snapshot, bad):
        with pytest.raises(InvalidHorizonError):
            compute_var_es(portfolio, snapshot, 0.95, bad)


class TestSampleBoundaries:
    """场景：空样本与样本不足必须被明确拒绝，且原因可区分。"""

    def test_因子历史为空_报空样本(self, portfolio):
        snap = MarketSnapshot(
            prices={"AAA": 100.0, "BBB": 50.0},
            fx_rates={"USD/CNY": 7.0},
            factor_returns={"EQ_US": (), "CN_RATES": ()},
        )
        with pytest.raises(EmptySampleError):
            compute_var_es(portfolio, snap, 0.95, 1)

    def test_样本不足以覆盖置信水平_报样本不足(self, portfolio):
        snap = MarketSnapshot(
            prices={"AAA": 100.0, "BBB": 50.0},
            fx_rates={"USD/CNY": 7.0},
            factor_returns={
                "EQ_US": tuple([0.01] * 10),  # 0.99 需要至少 100 个样本
                "CN_RATES": tuple([0.01] * 10),
            },
        )
        with pytest.raises(InsufficientSampleError) as exc_info:
            compute_var_es(portfolio, snap, 0.99, 1)
        assert exc_info.value.required == 100
        assert exc_info.value.actual == 10
        # 与空样本是可区分的不同失败原因
        assert not isinstance(exc_info.value, EmptySampleError)

    def test_样本恰好等于需求下界时可用(self, portfolio):
        snap = MarketSnapshot(
            prices={"AAA": 100.0, "BBB": 50.0},
            fx_rates={"USD/CNY": 7.0},
            factor_returns={
                "EQ_US": tuple([0.01, -0.01] * 10),  # 20 个样本，0.95 需 20
                "CN_RATES": tuple([0.001] * 20),
            },
        )
        r = compute_var_es(portfolio, snap, 0.95, 1)
        assert r.sample_size == 20


class TestMissingData:
    """场景：缺行情 / 缺汇率 / 缺历史时的失败原因可区分。"""

    def test_缺行情(self, portfolio):
        snap = MarketSnapshot(
            prices={"AAA": 100.0},  # 缺 BBB
            fx_rates={"USD/CNY": 7.0},
            factor_returns={"EQ_US": (0.01,) * 100, "CN_RATES": (0.01,) * 100},
        )
        with pytest.raises(MissingMarketDataError):
            compute_var_es(portfolio, snap, 0.95, 1)

    def test_缺汇率(self, portfolio):
        snap = MarketSnapshot(
            prices={"AAA": 100.0, "BBB": 50.0},
            fx_rates={},  # 缺 USD/CNY
            factor_returns={"EQ_US": (0.01,) * 100, "CN_RATES": (0.01,) * 100},
        )
        with pytest.raises(MissingFxRateError):
            compute_var_es(portfolio, snap, 0.95, 1)

    def test_缺因子历史(self, portfolio):
        snap = MarketSnapshot(
            prices={"AAA": 100.0, "BBB": 50.0},
            fx_rates={"USD/CNY": 7.0},
            factor_returns={"EQ_US": (0.01,) * 100},  # 缺 CN_RATES
        )
        with pytest.raises(MissingHistoryError):
            compute_var_es(portfolio, snap, 0.95, 1)

    def test_基准币头寸不需要汇率(self, snapshot):
        pf = Portfolio(
            "p", "CNY", (Position("BBB", "bond", "CNY", 20.0, "CN_RATES"),)
        )
        r = compute_var_es(pf, snapshot, 0.95, 1)
        assert r.sample_size == 100
