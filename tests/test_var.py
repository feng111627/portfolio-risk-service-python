"""业务场景：历史模拟法 VaR/ES 主流程、非法参数、样本不足、缺失数据。"""
from __future__ import annotations

import math

import pytest

from risk_service.models import (
    AssetClass,
    ErrorCode,
    Portfolio,
    Position,
    RiskError,
    Snapshot,
    VaRRequest,
)
from risk_service.risk_metrics import historical_var_es

pytestmark = pytest.mark.scenario("VaR/ES 历史模拟")


def test_var_es_main_flow_positive_loss_and_ordering(equity_portfolio, snapshot):
    """场景：99%/1日 VaR、ES 为正亏损且 ES >= VaR。"""
    res = historical_var_es(VaRRequest(equity_portfolio, 0.99, 1), snapshot)
    assert res.sample_size == 250
    assert res.var > 0
    assert res.expected_shortfall >= res.var


def test_var_horizon_scales_with_sqrt_time(equity_portfolio, snapshot):
    """场景：持有期 10 天按 sqrt(10) 缩放，VaR/ES 同步。"""
    d1 = historical_var_es(VaRRequest(equity_portfolio, 0.99, 1), snapshot)
    d10 = historical_var_es(VaRRequest(equity_portfolio, 0.99, 10), snapshot)
    assert d10.var == pytest.approx(d1.var * math.sqrt(10), rel=1e-12)
    assert d10.expected_shortfall == pytest.approx(
        d1.expected_shortfall * math.sqrt(10), rel=1e-12
    )


def test_var_reproducible_given_same_snapshot(equity_portfolio, snapshot):
    """场景：同输入同快照，结果逐字节一致。"""
    r1 = historical_var_es(VaRRequest(equity_portfolio, 0.95, 1), snapshot)
    r2 = historical_var_es(VaRRequest(equity_portfolio, 0.95, 1), snapshot)
    assert r1 == r2


def test_var_higher_confidence_never_lower(equity_portfolio, snapshot):
    """场景：99% 置信水平的 VaR 不低于 95%。"""
    v95 = historical_var_es(VaRRequest(equity_portfolio, 0.95, 1), snapshot).var
    v99 = historical_var_es(VaRRequest(equity_portfolio, 0.99, 1), snapshot).var
    assert v99 >= v95


def test_var_multi_factor_alignment_uses_shortest_history(multi_portfolio, snapshot):
    """场景：多因子取最短公共长度对齐。"""
    # 截短一个因子的历史，验证对齐生效且不报错。
    short = Snapshot(
        version=snapshot.version,
        prices=dict(snapshot.prices),
        fx_rates=dict(snapshot.fx_rates),
        history={
            **{k: v for k, v in snapshot.history.items() if k != "EQ_AAPL"},
            "EQ_AAPL": snapshot.history["EQ_AAPL"][:120],
        },
        volatilities=dict(snapshot.volatilities),
    )
    res = historical_var_es(VaRRequest(multi_portfolio, 0.99, 1), short)
    assert res.sample_size == 120


@pytest.mark.parametrize("confidence", [0.0, 1.0, -0.1, 1.5])
def test_var_rejects_confidence_out_of_range(equity_portfolio, snapshot, confidence):
    """场景：置信水平越界 -> INVALID_CONFIDENCE，可区分。"""
    with pytest.raises(RiskError) as ei:
        historical_var_es(VaRRequest(equity_portfolio, confidence, 1), snapshot)
    assert ei.value.code is ErrorCode.INVALID_CONFIDENCE


@pytest.mark.parametrize("horizon", [0, -3])
def test_var_rejects_horizon_out_of_range(equity_portfolio, snapshot, horizon):
    """场景：持有期越界 -> INVALID_HORIZON，可区分。"""
    with pytest.raises(RiskError) as ei:
        historical_var_es(VaRRequest(equity_portfolio, 0.99, horizon), snapshot)
    assert ei.value.code is ErrorCode.INVALID_HORIZON


def test_var_rejects_empty_portfolio(snapshot):
    """场景：空组合 -> EMPTY_PORTFOLIO。"""
    req = VaRRequest(Portfolio("empty", (), "USD"), 0.99, 1)
    with pytest.raises(RiskError) as ei:
        historical_var_es(req, snapshot)
    assert ei.value.code is ErrorCode.EMPTY_PORTFOLIO


def test_var_rejects_when_all_positions_unpriceable(snapshot):
    """场景：全部头寸缺行情/汇率 -> EMPTY_SAMPLE，不返回看似正常的数。"""
    p = Portfolio(
        "all-missing",
        (Position("x", "NO_FACTOR", 1.0, "USD", AssetClass.EQUITY),),
        "USD",
    )
    with pytest.raises(RiskError) as ei:
        historical_var_es(VaRRequest(p, 0.99, 1), snapshot)
    assert ei.value.code is ErrorCode.EMPTY_SAMPLE


def test_var_rejects_missing_history_distinct_from_empty(snapshot):
    """场景：因子有价格但无历史 -> MISSING_HISTORY，与样本为空明确区分。"""
    snap = Snapshot(2, {"F": 10.0}, {"USD": 1.0}, {})
    p = Portfolio("f", (Position("x", "F", 1.0, "USD", AssetClass.EQUITY),), "USD")
    with pytest.raises(RiskError) as ei:
        historical_var_es(VaRRequest(p, 0.99, 1), snap)
    assert ei.value.code is ErrorCode.MISSING_HISTORY


def test_var_rejects_insufficient_sample_size():
    """场景：样本数量不足以覆盖所选置信水平 -> INSUFFICIENT_SAMPLE。"""
    snap = Snapshot(
        3, {"F": 10.0}, {"USD": 1.0}, {"F": tuple(0.01 for _ in range(50))}
    )
    p = Portfolio("f", (Position("x", "F", 1.0, "USD", AssetClass.EQUITY),), "USD")
    with pytest.raises(RiskError) as ei:
        historical_var_es(VaRRequest(p, 0.99, 1), snap)
    assert ei.value.code is ErrorCode.INSUFFICIENT_SAMPLE
    # 同样本在更低置信水平（只需 20 个）应可计算。
    assert historical_var_es(VaRRequest(p, 0.95, 1), snap).sample_size == 50


def test_var_missing_fx_position_is_skipped_and_reported(snapshot):
    """场景：部分头寸缺汇率 -> 跳过并在 missing_positions 报告，其余正常计算。"""
    p = Portfolio(
        "mixed",
        (
            Position("ok", "EQ_AAPL", 100.0, "USD", AssetClass.EQUITY),
            Position("nofx", "EQ_TSLA", 1.0, "CHF", AssetClass.EQUITY),
        ),
        "USD",
    )
    res = historical_var_es(VaRRequest(p, 0.99, 1), snapshot)
    assert res.missing_positions == ("nofx",)
    assert res.var > 0
