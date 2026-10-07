"""本地模拟行情与风险因子数据（不接任何外部源）。

所有数据由显式随机种子确定性地生成：同一种子必然得到同一快照，
便于测试复现与本地验证。
"""

from __future__ import annotations

import random

from .models import MarketSnapshot

# 本地模拟的标的池：instrument_id -> (起始价, 币种, 资产类别, 因子id)
INSTRUMENTS: dict[str, tuple[float, str, str, str]] = {
    "ACME": (100.0, "USD", "equity", "EQ_US"),
    "GLOBE": (80.0, "USD", "equity", "EQ_US"),
    "CNBOND10Y": (98.0, "CNY", "bond", "CN_RATES"),
    "GOLD_SPOT": (60.0, "USD", "commodity", "CMDTY"),
}

BASE_FX: dict[str, float] = {
    "USD/CNY": 7.1,
    "EUR/CNY": 7.8,
}

HISTORY_LEN = 300


def _gen_returns(rng: random.Random, n: int, mu: float, sigma: float) -> tuple[float, ...]:
    return tuple(rng.gauss(mu, sigma) for _ in range(n))


def build_initial_snapshot(seed: int = 42) -> MarketSnapshot:
    """用固定随机种子生成一份确定性的初始快照。"""
    rng = random.Random(seed)
    prices = {iid: spec[0] for iid, spec in INSTRUMENTS.items()}
    factor_returns = {
        "EQ_US": _gen_returns(rng, HISTORY_LEN, 0.0002, 0.012),
        "CN_RATES": _gen_returns(rng, HISTORY_LEN, 0.0000, 0.004),
        "CMDTY": _gen_returns(rng, HISTORY_LEN, 0.0001, 0.015),
    }
    return MarketSnapshot(
        prices=prices,
        fx_rates=dict(BASE_FX),
        factor_returns=factor_returns,
        version=1,
    )


def build_refreshed_snapshot(current: MarketSnapshot, seed: int) -> MarketSnapshot:
    """基于当前快照确定性地演化出下一版快照（整体新对象，不原地修改）。"""
    rng = random.Random(seed)
    prices = {
        iid: round(p * (1.0 + rng.gauss(0.0, 0.005)), 6)
        for iid, p in current.prices.items()
    }
    fx_rates = {
        k: round(r * (1.0 + rng.gauss(0.0, 0.002)), 6)
        for k, r in current.fx_rates.items()
    }
    # 历史序列滚动：丢掉最旧一条，追加一条新收益
    factor_returns: dict[str, tuple[float, ...]] = {}
    for fid, series in current.factor_returns.items():
        sigma = 0.01
        new_ret = rng.gauss(0.0, sigma)
        factor_returns[fid] = tuple(series[1:]) + (new_ret,)
    return MarketSnapshot(
        prices=prices,
        fx_rates=fx_rates,
        factor_returns=factor_returns,
        version=current.version + 1,
    )
