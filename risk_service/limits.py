"""敞口聚合与限额校验。

口径（详见 README）：
- 敞口按头寸当前市值（折算到组合本币）计，支持多/空头（负数量）。
- 缺价格或缺汇率的头寸：跳过并稳定地列入 missing_positions，
  不以 0 计入任何桶，避免静默稀释集中度。
- 按资产类别聚合为有符号敞口；按币种同理（本币口径）。
- 集中度 = 单一资产类别 |有符号敞口| / 总毛敞口（gross），
  超过 limits.max_concentration 记一条 LIMIT_BREACH（结果中逐条列出）。
- 杠杆 = 总毛敞口 / limits.net_equity_base_ccy，
  超过 limits.max_leverage 记一条；net_equity <= 0 视为配置非法，明确拒绝。
- gross=0 时集中度不做比较（无敞口可集中）。
"""
from __future__ import annotations

from .models import (
    ErrorCode,
    ExposureBucket,
    ExposureResult,
    Limits,
    Portfolio,
    RiskError,
    Snapshot,
)
from .valuation import value_position


def aggregate_exposure(
    portfolio: Portfolio,
    snapshot: Snapshot,
    limits: Limits | None = None,
) -> ExposureResult:
    by_class: dict[str, float] = {}
    by_ccy: dict[str, float] = {}
    buckets: list[ExposureBucket] = []
    missing: list[str] = []

    long_total = 0.0
    short_total = 0.0

    for pos in portfolio.positions:
        v = value_position(pos, portfolio, snapshot)
        if v is None:
            missing.append(pos.position_id)
            continue
        ac = pos.asset_class.value if hasattr(pos.asset_class, "value") else str(pos.asset_class)
        by_class[ac] = by_class.get(ac, 0.0) + v
        by_ccy[pos.ccy] = by_ccy.get(pos.ccy, 0.0) + v
        buckets.append(ExposureBucket(ccy=pos.ccy, asset_class=ac, exposure_base_ccy=v))
        if v >= 0:
            long_total += v
        else:
            short_total += v

    # gross 在头寸级别取绝对值（杠杆毛名义额），避免多空在同一桶内互相抵消。
    gross = long_total - short_total
    net = long_total + short_total

    breaches = []
    if limits is not None:
        if limits.net_equity_base_ccy <= 0:
            raise RiskError(
                ErrorCode.LIMIT_CONFIG_INVALID,
                "net_equity_base_ccy must be positive for leverage check",
            )
        if gross > 0:
            for ac in sorted(by_class):
                conc = abs(by_class[ac]) / gross
                if conc > limits.max_concentration:
                    breaches.append(
                        _breach(
                            "concentration",
                            conc,
                            limits.max_concentration,
                            f"asset_class={ac}",
                        )
                    )
        leverage = gross / limits.net_equity_base_ccy
        if leverage > limits.max_leverage:
            breaches.append(
                _breach("leverage", leverage, limits.max_leverage, "gross/net_equity")
            )

    return ExposureResult(
        total_long=long_total,
        total_short=short_total,
        gross=gross,
        net=net,
        by_asset_class=dict(sorted(by_class.items())),
        by_ccy=dict(sorted(by_ccy.items())),
        buckets=tuple(buckets),
        missing_positions=tuple(missing),
        breaches=tuple(breaches),
        snapshot_version=snapshot.version,
    )


def _breach(name: str, observed: float, limit: float, detail: str):
    from .models import LimitBreach

    return LimitBreach(limit_name=name, observed=observed, limit=limit, detail=detail)
