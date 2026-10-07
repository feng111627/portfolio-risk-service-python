"""FastAPI 路由、DTO 与领域对象互转。

引擎层（risk_service 内除 api 外的模块）保持纯 Python、不依赖 Web；
本层负责 Pydantic 校验、HTTP 状态映射：
- 参数校验失败（pydantic）-> 422
- RiskError 业务失败      -> 422，body 内含稳定 error_code 与 message，
                            与正常 200 结果明确区分，绝不返回看似正常的数。
"""
from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse

from .limits import aggregate_exposure
from .models import (
    AssetClass,
    Limits,
    Portfolio,
    Position,
    RiskError,
    Scenario,
    ScenarioShock,
    Snapshot,
    StressRequest,
    VaRRequest,
)
from .risk_metrics import historical_var_es
from .scenarios import apply_stress
from .store import SnapshotStore


# ---------------- DTO ----------------

from pydantic import BaseModel, Field


class PositionDTO(BaseModel):
    position_id: str
    factor_id: str
    quantity: float
    ccy: str
    asset_class: AssetClass


class PortfolioDTO(BaseModel):
    name: str
    base_ccy: str
    positions: list[PositionDTO]


class VaRRequestDTO(BaseModel):
    portfolio: PortfolioDTO
    confidence: float = Field(description="(0,1) 开区间")
    horizon_days: int = Field(ge=1)


class ScenarioShockDTO(BaseModel):
    factor_id: str
    type: str
    value: float


class ScenarioDTO(BaseModel):
    scenario_id: str
    shocks: list[ScenarioShockDTO]


class StressRequestDTO(BaseModel):
    portfolio: PortfolioDTO
    scenarios: list[ScenarioDTO]
    include_baseline: bool = True


class LimitsDTO(BaseModel):
    max_concentration: float
    max_leverage: float
    net_equity_base_ccy: float


class ExposureRequestDTO(BaseModel):
    portfolio: PortfolioDTO
    limits: LimitsDTO | None = None


class SnapshotDTO(BaseModel):
    version: int
    prices: dict[str, float]
    fx_rates: dict[str, float]
    history: dict[str, list[float]]
    volatilities: dict[str, float] = {}


# ---------------- 映射 ----------------

def _to_domain(dto: PortfolioDTO) -> Portfolio:
    return Portfolio(
        name=dto.name,
        base_ccy=dto.base_ccy,
        positions=tuple(
            Position(
                position_id=p.position_id,
                factor_id=p.factor_id,
                quantity=p.quantity,
                ccy=p.ccy,
                asset_class=p.asset_class,
            )
            for p in dto.positions
        ),
    )


def create_router(store: SnapshotStore) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    def health():
        return {"status": "ok", "snapshot_version": store.get().version}

    @router.post("/risk/var")
    def calc_var(dto: VaRRequestDTO):
        req = VaRRequest(
            portfolio=_to_domain(dto.portfolio),
            confidence=dto.confidence,
            horizon_days=dto.horizon_days,
        )
        res = historical_var_es(req, store.get())
        return {
            "var": res.var,
            "expected_shortfall": res.expected_shortfall,
            "confidence": res.confidence,
            "horizon_days": res.horizon_days,
            "sample_size": res.sample_size,
            "snapshot_version": res.snapshot_version,
            "missing_positions": list(res.missing_positions),
        }

    @router.post("/risk/stress")
    def calc_stress(dto: StressRequestDTO):
        scenarios = tuple(
            Scenario(
                scenario_id=s.scenario_id,
                shocks=tuple(
                    ScenarioShock(factor_id=h.factor_id, type=h.type, value=h.value)
                    for h in s.shocks
                ),
            )
            for s in dto.scenarios
        )
        req = StressRequest(
            portfolio=_to_domain(dto.portfolio),
            scenarios=scenarios,
            include_baseline=dto.include_baseline,
        )
        res = apply_stress(req, store.get())
        return {
            "baseline_pnl": res.baseline_pnl,
            "baseline_valuation": res.baseline_valuation,
            "scenarios": [
                {
                    "scenario_id": s.scenario_id,
                    "pnl_base_ccy": s.pnl_base_ccy,
                    "valuation_base_ccy": s.valuation_base_ccy,
                    "shocked_prices": s.shocked_prices,
                }
                for s in res.scenarios
            ],
            "missing_positions": list(res.missing_positions),
            "snapshot_version": res.snapshot_version,
        }

    @router.post("/risk/exposure")
    def calc_exposure(dto: ExposureRequestDTO):
        limits = None
        if dto.limits is not None:
            limits = Limits(
                max_concentration=dto.limits.max_concentration,
                max_leverage=dto.limits.max_leverage,
                net_equity_base_ccy=dto.limits.net_equity_base_ccy,
            )
        res = aggregate_exposure(_to_domain(dto.portfolio), store.get(), limits)
        return {
            "total_long": res.total_long,
            "total_short": res.total_short,
            "gross": res.gross,
            "net": res.net,
            "by_asset_class": res.by_asset_class,
            "by_ccy": res.by_ccy,
            "missing_positions": list(res.missing_positions),
            "breaches": [
                {
                    "limit_name": b.limit_name,
                    "observed": b.observed,
                    "limit": b.limit,
                    "detail": b.detail,
                }
                for b in res.breaches
            ],
            "snapshot_version": res.snapshot_version,
        }

    @router.post("/admin/snapshot")
    def replace_snapshot(dto: SnapshotDTO):
        new_snap = Snapshot(
            version=dto.version,
            prices=dto.prices,
            fx_rates=dto.fx_rates,
            history={k: tuple(v) for k, v in dto.history.items()},
            volatilities=dto.volatilities,
        )
        store.replace(new_snap)
        return {"status": "replaced", "snapshot_version": new_snap.version}

    return router


def install_exception_handlers(app) -> None:
    @app.exception_handler(RiskError)
    async def _risk_error_handler(_request: Request, exc: RiskError):
        return JSONResponse(
            status_code=422,
            content={"error_code": exc.code.value, "message": exc.message},
        )

    @app.exception_handler(RequestValidationError)
    async def _validation_handler(_request: Request, exc: RequestValidationError):
        return JSONResponse(
            status_code=422,
            content={"error_code": "VALIDATION_ERROR", "detail": exc.errors()},
        )
