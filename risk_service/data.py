"""本地模拟行情/风险因子数据。

不接入任何外部行情源或付费服务：所有价格、汇率、历史收益率均由
带固定种子的确定性 LCG 生成，因此同一份代码产出的初始快照逐字节
可复现，便于本地验证与测试对齐。
"""
from __future__ import annotations

import math

from .models import Snapshot

_HISTORY_LEN = 250


def _lcg(seed: int):
    """Numerical Recipes LCG，返回产生 (0,1) 均匀数的闭包。"""
    state = seed & 0xFFFFFFFF

    def next_u01() -> float:
        nonlocal state
        state = (1664525 * state + 1013904223) & 0xFFFFFFFF
        return state / 0x100000000

    return next_u01


def _simulate_returns(seed: int, mu: float, sigma: float, n: int) -> tuple[float, ...]:
    """用两个均匀数的 Box-Muller 变换生成正态日收益率（确定性）。"""
    u = _lcg(seed)
    out: list[float] = []
    for _ in range(n):
        u1 = max(u(), 1e-12)
        u2 = u()
        z = math.sqrt(-2.0 * math.log(u1)) * math.cos(2.0 * math.pi * u2)
        out.append(mu + sigma * z)
    return tuple(out)


def build_initial_snapshot() -> Snapshot:
    # factor_id -> (价格, 币种, 日漂移, 日波动)
    factor_specs = {
        "EQ_AAPL":   (230.0,  "USD", 0.0005, 0.016),
        "EQ_TSLA":   (250.0,  "USD", 0.0002, 0.030),
        "EQ_BABA":   (85.0,   "CNY", 0.0001, 0.022),
        "IR_UST10Y": (102.0,  "USD", 0.0000, 0.005),
        "FX_EURUSD": (1.09,   "EUR", 0.0000, 0.006),
        "CMD_GOLD":  (2650.0, "USD", 0.0003, 0.010),
    }
    prices: dict[str, float] = {}
    history: dict[str, tuple[float, ...]] = {}
    vols: dict[str, float] = {}
    for i, (fid, (px, _ccy, mu, sigma)) in enumerate(factor_specs.items()):
        prices[fid] = px
        history[fid] = _simulate_returns(seed=1000 + i * 79, mu=mu, sigma=sigma, n=_HISTORY_LEN)
        vols[fid] = sigma
    fx_rates = {"USD": 1.0, "CNY": 7.10, "EUR": 0.92, "JPY": 150.0}
    return Snapshot(
        version=1,
        prices=prices,
        fx_rates=fx_rates,
        history=history,
        volatilities=vols,
    )
