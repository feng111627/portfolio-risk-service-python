"""历史模拟法 VaR / ES。

口径：
- 以当前快照重估每个头寸的基准币价值 value_i；
- 用头寸挂钩因子的历史日收益率序列回放组合日盈亏 pnl_t = Σ value_i * r_{f_i,t}；
- 持有期 H 的盈亏按 sqrt(H) 时间缩放；
- VaR 取损失序列的 confidence 分位（向上取整索引），ES 取不小于 VaR 的损失均值；
- VaR/ES 均以正数表示损失金额（基准币）。
"""

from __future__ import annotations

import math

from .errors import (
    EmptySampleError,
    InsufficientSampleError,
    InvalidConfidenceLevelError,
    InvalidHorizonError,
    MissingFxRateError,
    MissingHistoryError,
    MissingMarketDataError,
)
from .models import MarketSnapshot, Portfolio, VarEsResult


def _validate_confidence(confidence: float) -> None:
    if not (0.0 < confidence < 1.0):
        raise InvalidConfidenceLevelError(
            f"confidence must be in (0, 1), got {confidence}"
        )


def _validate_horizon(horizon_days: int) -> None:
    if not isinstance(horizon_days, int) or horizon_days < 1:
        raise InvalidHorizonError(
            f"horizon_days must be an integer >= 1, got {horizon_days!r}"
        )


def fx_rate(snapshot: MarketSnapshot, from_ccy: str, to_ccy: str) -> float:
    """取 from_ccy -> to_ccy 的汇率；同币种为 1，缺失抛 MissingFxRateError。"""
    if from_ccy == to_ccy:
        return 1.0
    key = f"{from_ccy}/{to_ccy}"
    try:
        return snapshot.fx_rates[key]
    except KeyError:
        raise MissingFxRateError(f"missing fx rate {key}") from None


def position_values_base(
    portfolio: Portfolio, snapshot: MarketSnapshot
) -> list[float]:
    """每个头寸的当前基准币价值（带符号）。"""
    values: list[float] = []
    for pos in portfolio.positions:
        try:
            price = snapshot.prices[pos.instrument_id]
        except KeyError:
            raise MissingMarketDataError(
                f"missing price for {pos.instrument_id}"
            ) from None
        rate = fx_rate(snapshot, pos.currency, portfolio.base_currency)
        values.append(pos.quantity * price * rate)
    return values


def compute_var_es(
    portfolio: Portfolio,
    snapshot: MarketSnapshot,
    confidence: float,
    horizon_days: int,
) -> VarEsResult:
    """基于因子历史收益率重估组合，计算 VaR 与 ES。"""
    _validate_confidence(confidence)
    _validate_horizon(horizon_days)

    values = position_values_base(portfolio, snapshot)

    series: list[tuple[float, ...]] = []
    for pos in portfolio.positions:
        try:
            s = snapshot.factor_returns[pos.factor_id]
        except KeyError:
            raise MissingHistoryError(
                f"missing factor history for {pos.factor_id}"
            ) from None
        series.append(s)

    if not series:
        raise EmptySampleError("portfolio has no positions")

    n = min(len(s) for s in series)
    if n == 0:
        raise EmptySampleError("factor return history is empty")

    required = math.ceil(1.0 / (1.0 - confidence))
    if n < required:
        raise InsufficientSampleError(required=required, actual=n)

    # 回放每日组合盈亏（取各序列最近 n 个样本，确定性对齐）
    daily_pnl: list[float] = []
    for t in range(n):
        pnl_t = 0.0
        for value, s in zip(values, series):
            pnl_t += value * s[len(s) - n + t]
        daily_pnl.append(pnl_t)

    scale = math.sqrt(horizon_days)
    losses = sorted(-p * scale for p in daily_pnl)

    idx = math.ceil(confidence * n) - 1  # 0 基索引，向上取整保证保守
    var = losses[idx]
    tail = losses[idx:]
    es = sum(tail) / len(tail)

    return VarEsResult(
        confidence=confidence,
        horizon_days=horizon_days,
        var=var,
        es=es,
        sample_size=n,
    )
