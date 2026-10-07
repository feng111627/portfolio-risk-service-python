"""组合计价。

缺省语义（稳定且明确，详见 README）：
- 头寸币种 == 组合本币：汇率恒按 1.0 处理。
- 外币头寸缺 fx_rates[ccy]：该头寸跳过、计入 missing_positions，不猜 0。
- 因子缺 prices[factor_id]：该头寸跳过、计入 missing_positions。
跳过的头寸不参与计价、风险、敞口；调用方拿到缺失清单自行决定。
所有头寸为线性 payoff：value = quantity * price / fx[ccy]，
其中 fx[ccy] 定义为“1 单位本币兑多少该币种”，本币自身为 1.0。
"""
from __future__ import annotations

from .models import Portfolio, Position, Snapshot


def position_value_base(quantity: float, price: float, fx_per_base: float) -> float:
    return quantity * price / fx_per_base


def _fx_for(ccy: str, base_ccy: str, snapshot: Snapshot) -> float | None:
    if ccy == base_ccy:
        return 1.0
    return snapshot.fx_rates.get(ccy)


def value_position(pos: Position, portfolio: Portfolio, snapshot: Snapshot) -> float | None:
    """返回本币市值；缺价格/汇率时返回 None。"""
    price = snapshot.prices.get(pos.factor_id)
    if price is None:
        return None
    fx = _fx_for(pos.ccy, portfolio.base_ccy, snapshot)
    if fx is None or fx <= 0:
        return None
    return position_value_base(pos.quantity, price, fx)


def missing_reason(pos: Position, portfolio: Portfolio, snapshot: Snapshot) -> str:
    if snapshot.prices.get(pos.factor_id) is None:
        return "missing_price"
    fx = _fx_for(pos.ccy, portfolio.base_ccy, snapshot)
    if fx is None or fx <= 0:
        return "missing_fx"
    return "ok"


def value_portfolio(
    portfolio: Portfolio, snapshot: Snapshot
) -> tuple[float, tuple[str, ...]]:
    total = 0.0
    missing: list[str] = []
    for pos in portfolio.positions:
        v = value_position(pos, portfolio, snapshot)
        if v is None:
            missing.append(pos.position_id)
        else:
            total += v
    return total, tuple(missing)
