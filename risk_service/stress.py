"""压力情景应用。

规则：
- 因子冲击以相对形式作用于挂钩该因子的标的价格：price' = price * (1 + shock)；
- 汇率冲击以相对形式作用于该币种兑基准币的汇率：fx' = fx * (1 + shock)；
- 未出现在情景中的因子 / 币种冲击为 0（确定性的缺省语义）；
- 同一情景重复应用、或与基准同时计算，均从同一不可变快照出发各算各的，
  不会互相污染，也不会重复计价；
- 输入（组合、快照、情景）相同则结果完全相同（可复现）。
"""

from __future__ import annotations

from .errors import MissingMarketDataError
from .models import MarketSnapshot, Portfolio, StressScenario
from .var_es import fx_rate, position_values_base


def _factor_shock_map(scenario: StressScenario) -> dict[str, float]:
    return {fs.factor_id: fs.shock for fs in scenario.factor_shocks}


def _fx_shock_map(scenario: StressScenario) -> dict[str, float]:
    return {fs.currency: fs.shock for fs in scenario.fx_shocks}


def apply_scenario(
    portfolio: Portfolio,
    snapshot: MarketSnapshot,
    scenario: StressScenario,
) -> dict[str, float]:
    """对组合应用一次情景，返回 {instrument_id: 冲击后价值(基准币)}。"""
    f_shocks = _factor_shock_map(scenario)
    x_shocks = _fx_shock_map(scenario)

    values: dict[str, float] = {}
    for pos in portfolio.positions:
        try:
            price = snapshot.prices[pos.instrument_id]
        except KeyError:
            raise MissingMarketDataError(
                f"missing price for {pos.instrument_id}"
            ) from None
        rate = fx_rate(snapshot, pos.currency, portfolio.base_currency)

        shocked_price = price * (1.0 + f_shocks.get(pos.factor_id, 0.0))
        shocked_rate = rate * (1.0 + x_shocks.get(pos.currency, 0.0))
        values[pos.instrument_id] = pos.quantity * shocked_price * shocked_rate
    return values


def scenario_pnl(
    portfolio: Portfolio,
    snapshot: MarketSnapshot,
    scenario: StressScenario,
) -> float:
    """情景下组合相对基准的盈亏（基准币）。

    基准价值与情景价值各自独立计价一次后相减，不存在重复计价。
    """
    stressed = sum(apply_scenario(portfolio, snapshot, scenario).values())
    base = sum(position_values_base(portfolio, snapshot))
    return stressed - base
