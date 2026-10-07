"""业务场景：压力情景的确定性应用、重复应用与基准隔离。"""

from __future__ import annotations

import pytest

from risk_service.errors import MissingMarketDataError
from risk_service.models import FactorShock, FxShock, StressScenario
from risk_service.stress import apply_scenario, scenario_pnl
from risk_service.var_es import position_values_base


@pytest.fixture()
def crash_scenario() -> StressScenario:
    return StressScenario(
        name="crash",
        factor_shocks=(FactorShock("EQ_US", -0.10),),
        fx_shocks=(FxShock("USD", -0.05),),
    )


class TestScenarioApplication:
    """场景：情景以确定方式作用于多标的、多因子头寸。"""

    def test_因子冲击只作用于挂钩该因子的标的(self, portfolio, snapshot, crash_scenario):
        v = apply_scenario(portfolio, snapshot, crash_scenario)
        # AAA: 10 * 100 * (1-0.10) * 7 * (1-0.05)
        assert v["AAA"] == pytest.approx(10 * 100 * 0.9 * 7 * 0.95)
        # BBB 挂钩 CN_RATES，无冲击，保持原价
        assert v["BBB"] == pytest.approx(20 * 50.0)

    def test_未出现在情景中的因子与币种冲击为零(self, portfolio, snapshot):
        empty = StressScenario(name="noop")
        v = apply_scenario(portfolio, snapshot, empty)
        assert v["AAA"] == pytest.approx(10 * 100 * 7.0)
        assert v["BBB"] == pytest.approx(20 * 50.0)

    def test_情景盈亏等于情景价值减基准价值(self, portfolio, snapshot, crash_scenario):
        base = sum(position_values_base(portfolio, snapshot))
        stressed = sum(apply_scenario(portfolio, snapshot, crash_scenario).values())
        assert scenario_pnl(portfolio, snapshot, crash_scenario) == pytest.approx(
            stressed - base
        )


class TestRepeatAndBaselineIsolation:
    """场景：重复应用不重复计价，情景与基准结果清楚分开。"""

    def test_同一情景重复应用结果一致且不叠加(self, portfolio, snapshot, crash_scenario):
        first = apply_scenario(portfolio, snapshot, crash_scenario)
        second = apply_scenario(portfolio, snapshot, crash_scenario)
        assert first == second
        # 冲击不叠加：第二次应用后 AAA 仍是 0.9*0.95 而非 (0.9*0.95)^2
        assert second["AAA"] == pytest.approx(10 * 100 * 0.9 * 7 * 0.95)

    def test_应用情景后基准估值不受影响(self, portfolio, snapshot, crash_scenario):
        base_before = position_values_base(portfolio, snapshot)
        apply_scenario(portfolio, snapshot, crash_scenario)
        scenario_pnl(portfolio, snapshot, crash_scenario)
        base_after = position_values_base(portfolio, snapshot)
        assert base_before == base_after

    def test_相同输入结果可复现(self, portfolio, snapshot, crash_scenario):
        pnl1 = scenario_pnl(portfolio, snapshot, crash_scenario)
        pnl2 = scenario_pnl(portfolio, snapshot, crash_scenario)
        assert pnl1 == pnl2

    def test_情景缺行情同样明确失败(self, portfolio, snapshot, crash_scenario):
        broken = type(snapshot)(
            prices={"AAA": 100.0},  # 缺 BBB
            fx_rates=snapshot.fx_rates,
            factor_returns=snapshot.factor_returns,
        )
        with pytest.raises(MissingMarketDataError):
            apply_scenario(portfolio, broken, crash_scenario)
