"""本地组合市场风险度量服务（FastAPI 入口）。

行情与风险因子全部由本地模拟数据生成，不接任何外部行情源。
"""

from __future__ import annotations

import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from risk_service.errors import RiskServiceError
from risk_service.limits import aggregate_exposure, check_limits
from risk_service.local_data import (
    INSTRUMENTS,
    build_initial_snapshot,
    build_refreshed_snapshot,
)
from risk_service.models import (
    FactorShock,
    FxShock,
    LimitRule,
    Portfolio,
    Position,
    StressScenario,
)
from risk_service.snapshot import SnapshotStore
from risk_service.stress import apply_scenario, scenario_pnl
from risk_service.var_es import compute_var_es, position_values_base

app = FastAPI(title="portfolio-risk-service")

# 进程内唯一的快照存储：刷新原子替换，计算只读完整快照
STORE = SnapshotStore(build_initial_snapshot(seed=42))
_refresh_seed = {"n": 0}


def demo_portfolio() -> Portfolio:
    """本地演示组合：每只模拟标的各持有一笔。"""
    positions = tuple(
        Position(
            instrument_id=iid,
            asset_class=spec[2],
            currency=spec[1],
            quantity=10.0 + 5.0 * i,
            factor_id=spec[3],
        )
        for i, (iid, spec) in enumerate(sorted(INSTRUMENTS.items()))
    )
    return Portfolio(portfolio_id="demo", base_currency="CNY", positions=positions)


@app.exception_handler(RiskServiceError)
async def risk_error_handler(_: Request, exc: RiskServiceError) -> JSONResponse:
    return JSONResponse(
        status_code=400,
        content={"error": exc.code, "message": exc.message},
    )


@app.get("/")
def read_root() -> dict[str, str]:
    return {"service": "portfolio-risk-service", "snapshot_version": str(STORE.version)}


@app.get("/var-es")
def var_es(confidence: float = 0.95, horizon_days: int = 1) -> dict:
    snapshot = STORE.get()
    result = compute_var_es(demo_portfolio(), snapshot, confidence, horizon_days)
    return {
        "confidence": result.confidence,
        "horizon_days": result.horizon_days,
        "var": result.var,
        "es": result.es,
        "sample_size": result.sample_size,
        "snapshot_version": snapshot.version,
    }


class ScenarioIn(BaseModel):
    name: str
    factor_shocks: dict[str, float] = Field(default_factory=dict)
    fx_shocks: dict[str, float] = Field(default_factory=dict)


@app.post("/stress")
def stress(body: ScenarioIn) -> dict:
    snapshot = STORE.get()
    portfolio = demo_portfolio()
    scenario = StressScenario(
        name=body.name,
        factor_shocks=tuple(
            FactorShock(fid, s) for fid, s in sorted(body.factor_shocks.items())
        ),
        fx_shocks=tuple(
            FxShock(c, s) for c, s in sorted(body.fx_shocks.items())
        ),
    )
    base_value = sum(position_values_base(portfolio, snapshot))
    stressed = apply_scenario(portfolio, snapshot, scenario)
    return {
        "scenario": scenario.name,
        "base_value": base_value,
        "stressed_value": sum(stressed.values()),
        "pnl": scenario_pnl(portfolio, snapshot, scenario),
        "stressed_by_instrument": stressed,
        "snapshot_version": snapshot.version,
    }


@app.get("/exposure")
def exposure() -> dict:
    snapshot = STORE.get()
    report = aggregate_exposure(demo_portfolio(), snapshot)
    return {
        "base_currency": report.base_currency,
        "by_currency": report.by_currency,
        "by_asset_class": report.by_asset_class,
        "gross_exposure": report.gross_exposure,
        "net_exposure": report.net_exposure,
        "snapshot_version": snapshot.version,
    }


class LimitRuleIn(BaseModel):
    kind: str  # "concentration" | "leverage"
    threshold: float
    asset_class: str | None = None


@app.post("/limits/check")
def limits_check(rules: list[LimitRuleIn]) -> dict:
    snapshot = STORE.get()
    report = aggregate_exposure(demo_portfolio(), snapshot)
    parsed = [
        LimitRule(kind=r.kind, threshold=r.threshold, asset_class=r.asset_class)
        for r in rules
    ]
    breaches = check_limits(report, parsed)
    return {
        "breached": len(breaches) > 0,
        "breaches": [
            {
                "kind": b.rule.kind,
                "asset_class": b.rule.asset_class,
                "threshold": b.rule.threshold,
                "observed": b.observed,
                "message": b.message,
            }
            for b in breaches
        ],
        "snapshot_version": snapshot.version,
    }


@app.post("/snapshot/refresh")
def snapshot_refresh() -> dict:
    """用本地模拟数据原子地演化出下一版快照。"""
    _refresh_seed["n"] += 1
    new_snapshot = build_refreshed_snapshot(STORE.get(), seed=1000 + _refresh_seed["n"])
    STORE.refresh(new_snapshot)
    return {"snapshot_version": new_snapshot.version}


if __name__ == "__main__":
    uvicorn.run(app, host="127.0.0.1", port=0)
