"""敞口聚合与限额校验。

缺省语义（稳定且明确）：
- 缺行情 -> MissingMarketDataError；缺汇率 -> MissingFxRateError（与 VaR 一致）；
- 聚合时不存在的资产类别 / 币种敞口视为 0；
- 组合总敞口为 0 时，集中度与杠杆均视为 0（不超限）；
- 净敞口为 0 但总敞口非 0 时，杠杆视为无穷大（必然超限）。
"""

from __future__ import annotations

import math

from .models import ExposureReport, LimitBreach, LimitRule, MarketSnapshot, Portfolio
from .var_es import position_values_base


def aggregate_exposure(
    portfolio: Portfolio,
    snapshot: MarketSnapshot,
) -> ExposureReport:
    """按币种与资产类别聚合敞口（基准币计，带符号）。"""
    values = position_values_base(portfolio, snapshot)

    by_currency: dict[str, float] = {}
    by_asset_class: dict[str, float] = {}
    gross = 0.0
    net = 0.0
    for pos, v in zip(portfolio.positions, values):
        by_currency[pos.currency] = by_currency.get(pos.currency, 0.0) + v
        by_asset_class[pos.asset_class] = by_asset_class.get(pos.asset_class, 0.0) + v
        gross += abs(v)
        net += v

    return ExposureReport(
        base_currency=portfolio.base_currency,
        by_currency=by_currency,
        by_asset_class=by_asset_class,
        gross_exposure=gross,
        net_exposure=net,
    )


def _concentration(report: ExposureReport, asset_class: str) -> float:
    if report.gross_exposure == 0.0:
        return 0.0
    return abs(report.by_asset_class.get(asset_class, 0.0)) / report.gross_exposure


def _leverage(report: ExposureReport) -> float:
    if report.gross_exposure == 0.0:
        return 0.0
    if report.net_exposure == 0.0:
        return math.inf
    return report.gross_exposure / abs(report.net_exposure)


def check_limits(
    report: ExposureReport,
    rules: list[LimitRule],
) -> list[LimitBreach]:
    """对聚合结果逐条校验限额，返回全部超限记录。"""
    breaches: list[LimitBreach] = []
    for rule in rules:
        if rule.kind == "concentration":
            ac = rule.asset_class or ""
            observed = _concentration(report, ac)
            if observed > rule.threshold:
                breaches.append(
                    LimitBreach(
                        rule=rule,
                        observed=observed,
                        message=(
                            f"concentration in '{ac}' {observed:.4f} "
                            f"> {rule.threshold}"
                        ),
                    )
                )
        elif rule.kind == "leverage":
            observed = _leverage(report)
            if observed > rule.threshold:
                breaches.append(
                    LimitBreach(
                        rule=rule,
                        observed=observed,
                        message=(
                            f"leverage {observed:.4f} > {rule.threshold}"
                        ),
                    )
                )
        else:
            raise ValueError(f"unknown limit rule kind: {rule.kind!r}")
    return breaches
