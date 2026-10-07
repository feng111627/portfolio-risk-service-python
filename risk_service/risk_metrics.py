"""历史模拟法 VaR / ES。

口径（详见 README）：
1. 仅使用快照中各因子“共同对齐”的历史日收益率（取最短公共长度，
   时间顺序保持一致，不做任何插值/补点）。
2. 每个历史日按线性头寸重新构造组合盈亏：
   pnl_t = Σ_i quantity_i * price_i * r_{i,t} / fx_i （全部折算到本币）。
3. 持有期 h 用 sqrt(h) 缩放：pnl_{t,h} = pnl_t * sqrt(h)。
4. VaR：取亏损分布的 (1-confidence) 分位（经验分位，索引 floor 法），
   以正数表示亏损；ES 为超过 VaR 的尾部亏损均值。
5. 拒绝条件均为显式错误码，绝不静默：
   - confidence 不在 (0,1)：INVALID_CONFIDENCE
   - horizon_days < 1：INVALID_HORIZON
   - 组合为空：EMPTY_PORTFOLIO
   - 有可计价头寸但某因子缺历史：MISSING_HISTORY
   - 共同样本为空：EMPTY_SAMPLE
   - 样本数不足以覆盖所选置信水平：INSUFFICIENT_SAMPLE
     （需 n >= ceil(1/(1-confidence))）。
"""
from __future__ import annotations

import math

from .models import (
    ErrorCode,
    Portfolio,
    RiskError,
    Snapshot,
    VaRRequest,
    VaRResult,
)
from .valuation import value_position


def _validate(req: VaRRequest) -> None:
    if not (0.0 < req.confidence < 1.0):
        raise RiskError(
            ErrorCode.INVALID_CONFIDENCE,
            f"confidence must be within (0,1), got {req.confidence}",
        )
    if req.horizon_days < 1:
        raise RiskError(
            ErrorCode.INVALID_HORIZON,
            f"horizon_days must be >= 1, got {req.horizon_days}",
        )
    if len(req.portfolio.positions) == 0:
        raise RiskError(ErrorCode.EMPTY_PORTFOLIO, "portfolio has no positions")


def _pnl_series(portfolio: Portfolio, snapshot: Snapshot) -> tuple[list[float], tuple[str, ...]]:
    # 1) 确定可计价头寸与缺失清单。
    live = []
    missing: list[str] = []
    for pos in portfolio.positions:
        v0 = value_position(pos, portfolio, snapshot)
        if v0 is None:
            missing.append(pos.position_id)
            continue
        hist = snapshot.history.get(pos.factor_id)
        if hist is None:
            raise RiskError(
                ErrorCode.MISSING_HISTORY,
                f"factor {pos.factor_id} has no historical returns",
            )
        live.append((pos, hist))

    if not live:
        return [], tuple(missing)

    # 2) 最短公共长度对齐。
    n = min(len(hist) for _pos, hist in live)
    if n == 0:
        return [], tuple(missing)

    # 3) 逐日构造历史组合盈亏（基准市值 = quantity*price/fx）。
    pnls = [0.0] * n
    for pos, hist in live:
        base_value = value_position(pos, portfolio, snapshot)
        for t in range(n):
            pnls[t] += base_value * hist[t]
    return pnls, tuple(missing)


def historical_var_es(req: VaRRequest, snapshot: Snapshot) -> VaRResult:
    _validate(req)
    pnls, missing = _pnl_series(req.portfolio, snapshot)

    if not pnls:
        raise RiskError(
            ErrorCode.EMPTY_SAMPLE,
            "aligned historical sample is empty (no live positions or no returns)",
        )

    n = len(pnls)
    need = math.ceil(1.0 / (1.0 - req.confidence))
    if n < need:
        raise RiskError(
            ErrorCode.INSUFFICIENT_SAMPLE,
            f"sample size {n} < required {need} for confidence {req.confidence}",
        )

    # 亏损为正：最坏亏损排在最前。
    losses = sorted((-p * math.sqrt(req.horizon_days) for p in pnls), reverse=True)
    idx = min(n - 1, int(n * (1.0 - req.confidence)))
    var = losses[idx]
    tail = losses[: idx + 1]
    es = sum(tail) / len(tail)
    return VaRResult(
        var=var,
        expected_shortfall=es,
        confidence=req.confidence,
        horizon_days=req.horizon_days,
        sample_size=n,
        snapshot_version=snapshot.version,
        missing_positions=missing,
    )
