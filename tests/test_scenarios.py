"""业务场景：压力情景的确定性、可复现、重复应用拒绝、与基准确分离、不重复计价。"""
from __future__ import annotations

import pytest

from risk_service.models import (
    AssetClass,
    ErrorCode,
    Portfolio,
    Position,
    RiskError,
    Scenario,
    ScenarioShock,
    StressRequest,
)
from risk_service.scenarios import apply_stress

pytestmark = pytest.mark.scenario("压力情景")

CRASH = Scenario(
    "crash",
    (
        ScenarioShock("EQ_AAPL", "relative", -0.20),
        ScenarioShock("EQ_BABA", "relative", -0.10),
    ),
)
AAPL_UP = Scenario("aapl_up", (ScenarioShock("EQ_AAPL", "relative", 0.05),))


def test_scenario_pnl_is_deterministic_and_reproducible(multi_portfolio, snapshot):
    """场景：同输入同快照，情景盈亏可复现。"""
    req = StressRequest(multi_portfolio, (CRASH, AAPL_UP))
    r1 = apply_stress(req, snapshot)
    r2 = apply_stress(req, snapshot)
    assert r1 == r2


def test_relative_shock_pnl_matches_manual_calc(multi_portfolio, snapshot):
    """场景：-20% AAPL 的情景盈亏 == 该头寸基准市值 * -0.20。"""
    res = apply_stress(StressRequest(multi_portfolio, (CRASH,)), snapshot)
    crash = next(s for s in res.scenarios if s.scenario_id == "crash")
    aapl_value = 100.0 * snapshot.prices["EQ_AAPL"] / snapshot.fx_rates["USD"]
    baba_ccy = 100.0 * snapshot.prices["EQ_BABA"]
    baba_value = baba_ccy / snapshot.fx_rates["CNY"]
    expected = aapl_value * -0.20 + baba_value * -0.10
    assert crash.pnl_base_ccy == pytest.approx(expected, rel=1e-12)
    assert crash.valuation_base_ccy == pytest.approx(
        res.baseline_valuation + expected, rel=1e-12
    )


def test_scenarios_are_isolated_from_each_other(multi_portfolio, snapshot):
    """场景：多情景一起算与单独算结果一致，情景间不连锁。"""
    together = apply_stress(
        StressRequest(multi_portfolio, (CRASH, AAPL_UP)), snapshot
    )
    alone = apply_stress(StressRequest(multi_portfolio, (AAPL_UP,)), snapshot)
    together_up = next(s for s in together.scenarios if s.scenario_id == "aapl_up")
    alone_up = alone.scenarios[0]
    assert together_up.pnl_base_ccy == alone_up.pnl_base_ccy


def test_baseline_valued_once_and_separated(multi_portfolio, snapshot):
    """场景：基准只计价一次，结果字段与情景结果清楚分开。"""
    res = apply_stress(
        StressRequest(multi_portfolio, (CRASH, AAPL_UP), include_baseline=True),
        snapshot,
    )
    assert res.baseline_pnl == 0.0
    assert {s.scenario_id for s in res.scenarios} == {"crash", "aapl_up"}
    # 情景结果的计价值都相对同一基准，且自身不等于基准（冲击非零）。
    for s in res.scenarios:
        assert s.valuation_base_ccy != pytest.approx(res.baseline_valuation)


def test_duplicate_scenario_in_request_is_rejected(multi_portfolio, snapshot):
    """场景：同一情景重复提交 -> DUPLICATE_SCENARIO，不做重复计价。"""
    with pytest.raises(RiskError) as ei:
        apply_stress(StressRequest(multi_portfolio, (CRASH, CRASH)), snapshot)
    assert ei.value.code is ErrorCode.DUPLICATE_SCENARIO


def test_repeated_application_across_calls_is_stable(multi_portfolio, snapshot):
    """场景：同一情景多次请求重复应用，每次结果相同（无状态副作用）。"""
    vals = {
        apply_stress(StressRequest(multi_portfolio, (CRASH,)), snapshot)
        .scenarios[0]
        .pnl_base_ccy
        for _ in range(5)
    }
    assert len(vals) == 1


def test_scenario_unknown_factor_rejected(multi_portfolio, snapshot):
    """场景：冲击引用不存在因子 -> INVALID_SCENARIO。"""
    bad = Scenario("x", (ScenarioShock("NOPE", "relative", -0.1),))
    with pytest.raises(RiskError) as ei:
        apply_stress(StressRequest(multi_portfolio, (bad,)), snapshot)
    assert ei.value.code is ErrorCode.INVALID_SCENARIO


def test_scenario_unknown_shock_type_rejected(multi_portfolio, snapshot):
    """场景：非法冲击类型 -> INVALID_SCENARIO。"""
    bad = Scenario("x", (ScenarioShock("EQ_AAPL", "weird", 1.0),))
    with pytest.raises(RiskError) as ei:
        apply_stress(StressRequest(multi_portfolio, (bad,)), snapshot)
    assert ei.value.code is ErrorCode.INVALID_SCENARIO


def test_absolute_and_relative_shock_types(multi_portfolio, snapshot):
    """场景：absolute 与 relative 两种作用方式均确定可算。"""
    rel = Scenario("r", (ScenarioShock("EQ_AAPL", "relative", 0.10),))
    ab = Scenario("a", (ScenarioShock("EQ_AAPL", "absolute", 5.0),))
    res = apply_stress(StressRequest(multi_portfolio, (rel, ab)), snapshot)
    prices = {s.scenario_id: s.shocked_prices["EQ_AAPL"] for s in res.scenarios}
    assert prices["r"] == pytest.approx(snapshot.prices["EQ_AAPL"] * 1.10)
    assert prices["a"] == pytest.approx(snapshot.prices["EQ_AAPL"] + 5.0)


def test_scenario_missing_market_position_skipped(snapshot):
    """场景：缺行情头寸按统一缺省语义跳过并报告，不影响情景对其他头寸的作用。"""
    p = Portfolio(
        "m",
        (
            Position("miss", "NO_FACTOR", 1.0, "USD", AssetClass.EQUITY),
            Position("ok", "EQ_AAPL", 10.0, "USD", AssetClass.EQUITY),
        ),
        "USD",
    )
    res = apply_stress(StressRequest(p, (AAPL_UP,)), snapshot)
    assert res.missing_positions == ("miss",)
    assert res.scenarios[0].pnl_base_ccy == pytest.approx(
        10.0 * snapshot.prices["EQ_AAPL"] * 0.05
    )


def test_scenario_does_not_mutate_snapshot(multi_portfolio, snapshot):
    """场景：施加情景后基准快照价格不被修改（不可变语义）。"""
    before = dict(snapshot.prices)
    apply_stress(StressRequest(multi_portfolio, (CRASH,)), snapshot)
    assert snapshot.prices == before
