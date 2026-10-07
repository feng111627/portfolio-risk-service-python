"""压力情景应用。

规则（详见 README）：
- 基准组合只计价一次；每个情景在“基准快照的一份独立拷贝”上
  逐因子确定性施加冲击后再计价一次，情景之间互不影响、不连锁。
- 因此同一情景出现在结果里只有一条；请求内 scenario_id 重复
  直接以 DUPLICATE_SCENARIO 拒绝，避免重复计价/歧义。
- 冲击引用快照中不存在的因子、或冲击类型非法：INVALID_SCENARIO。
- 缺行情/汇率的头寸与基准计价采用同一缺省语义：跳过并列入
  missing_positions，基准与每个情景各自报告。
- 冲击类型：
    relative:  p' = p * (1 + value)
    absolute:  p' = p + value
    vol_shift: 仅平移波动率字段，不改变线性组合价格（明确允许但无计价影响）。
- 纯函数：同输入 + 同快照 => 逐字节可复现。
"""
from __future__ import annotations

from .models import (
    ErrorCode,
    RiskError,
    Scenario,
    Snapshot,
    StressRequest,
    StressResult,
    ScenarioPnl,
)
from .valuation import value_portfolio

_VALID_SHOCK_TYPES = frozenset({"relative", "absolute", "vol_shift"})


def _apply_shocks(base: Snapshot, scenario: Scenario) -> Snapshot:
    prices = dict(base.prices)
    vols = dict(base.volatilities)
    for shock in scenario.shocks:
        if shock.type == "vol_shift":
            if shock.factor_id not in vols and shock.factor_id not in prices:
                raise RiskError(
                    ErrorCode.INVALID_SCENARIO,
                    f"shock references unknown factor {shock.factor_id}",
                )
            vols[shock.factor_id] = vols.get(shock.factor_id, 0.0) + shock.value
            continue
        if shock.type not in ("relative", "absolute"):
            raise RiskError(
                ErrorCode.INVALID_SCENARIO,
                f"unknown shock type {shock.type!r} for factor {shock.factor_id}",
            )
        if shock.factor_id not in prices:
            raise RiskError(
                ErrorCode.INVALID_SCENARIO,
                f"shock references unknown factor {shock.factor_id}",
            )
        if shock.type == "relative":
            prices[shock.factor_id] = prices[shock.factor_id] * (1.0 + shock.value)
        else:
            prices[shock.factor_id] = prices[shock.factor_id] + shock.value
    return Snapshot(
        version=base.version,
        prices=prices,
        fx_rates=base.fx_rates,
        history=base.history,
        volatilities=vols,
    )


def apply_stress(req: StressRequest, snapshot: Snapshot) -> StressResult:
    if len(req.portfolio.positions) == 0:
        raise RiskError(ErrorCode.EMPTY_PORTFOLIO, "portfolio has no positions")

    seen: set[str] = set()
    for sc in req.scenarios:
        if sc.scenario_id in seen:
            raise RiskError(
                ErrorCode.DUPLICATE_SCENARIO,
                f"scenario {sc.scenario_id} submitted more than once",
            )
        seen.add(sc.scenario_id)
        for shock in sc.shocks:
            if shock.type not in _VALID_SHOCK_TYPES:
                raise RiskError(
                    ErrorCode.INVALID_SCENARIO,
                    f"unknown shock type {shock.type!r}",
                )

    # 基准只计价一次。
    baseline_value, baseline_missing = value_portfolio(req.portfolio, snapshot)

    results: list[ScenarioPnl] = []
    scenario_missing: set[str] = set(baseline_missing)
    for sc in req.scenarios:
        shocked = _apply_shocks(snapshot, sc)
        val, miss = value_portfolio(req.portfolio, shocked)
        scenario_missing.update(miss)
        shocked_prices = {
            fid: shocked.prices[fid]
            for fid in {s.factor_id for s in sc.shocks}
            if fid in shocked.prices
        }
        results.append(
            ScenarioPnl(
                scenario_id=sc.scenario_id,
                pnl_base_ccy=val - baseline_value,
                valuation_base_ccy=val,
                shocked_prices=shocked_prices,
            )
        )

    return StressResult(
        baseline_pnl=0.0,
        baseline_valuation=baseline_value,
        scenarios=tuple(results),
        missing_positions=tuple(sorted(scenario_missing)),
        snapshot_version=snapshot.version,
    )
