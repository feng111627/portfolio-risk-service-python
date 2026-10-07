"""领域模型与请求/响应类型定义。

所有头寸、行情快照、风险计算请求与结果均为不可变值对象，
保证同一输入 + 同一快照下计算结果可复现。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum


class AssetClass(str, Enum):
    """资产类别，用于敞口聚合与限额校验。"""

    EQUITY = "equity"
    RATES = "rates"
    FX = "fx"
    COMMODITY = "commodity"


class ErrorCode(str, Enum):
    """可区分的业务失败原因。"""

    EMPTY_SAMPLE = "EMPTY_SAMPLE"                 # 历史样本为空
    INSUFFICIENT_SAMPLE = "INSUFFICIENT_SAMPLE"   # 样本不足以覆盖置信水平
    INVALID_CONFIDENCE = "INVALID_CONFIDENCE"     # 置信水平越界
    INVALID_HORIZON = "INVALID_HORIZON"           # 持有期越界
    EMPTY_PORTFOLIO = "EMPTY_PORTFOLIO"           # 组合为空
    MISSING_RISK_FACTOR = "MISSING_RISK_FACTOR"   # 头寸引用了快照中不存在的因子
    MISSING_FX_RATE = "MISSING_FX_RATE"           # 本币换算缺汇率
    MISSING_HISTORY = "MISSING_HISTORY"           # 因子缺历史收益率
    INVALID_SCENARIO = "INVALID_SCENARIO"         # 情景定义非法
    DUPLICATE_SCENARIO = "DUPLICATE_SCENARIO"     # 同一情景重复提交
    LIMIT_BREACH = "LIMIT_BREACH"                 # 限额超限
    LIMIT_CONFIG_INVALID = "LIMIT_CONFIG_INVALID"  # 限额配置非法


class RiskError(Exception):
    """携带稳定错误码的业务异常。"""

    def __init__(self, code: ErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True)
class Position:
        """单笔头寸。

        factor_id 决定该头寸暴露在哪个风险因子上；
        quantity 为持仓数量（可为负，表示空头）；
        ccy 为该头寸计价币种；asset_class 用于聚合。
        """

        position_id: str
        factor_id: str
        quantity: float
        ccy: str
        asset_class: AssetClass


@dataclass(frozen=True)
class Portfolio:
    """组合：一组头寸 + 报告币种。"""

    name: str
    positions: tuple[Position, ...]
    base_ccy: str


@dataclass(frozen=True)
class VaRRequest:
    portfolio: Portfolio
    confidence: float  # (0, 1) 开区间
    horizon_days: int  # >= 1


@dataclass(frozen=True)
class VaRResult:
    var: float
    expected_shortfall: float
    confidence: float
    horizon_days: int
    sample_size: int
    snapshot_version: int
    missing_positions: tuple[str, ...] = ()


@dataclass(frozen=True)
class ScenarioShock:
        """单一因子冲击：type 决定作用方式。

        - "relative": 新价 = 基准价 * (1 + value)
        - "absolute": 新价 = 基准价 + value
        - "vol_shift": 波动率绝对值平移（预留，当前线性组合只取价）
        """

        factor_id: str
        type: str
        value: float


@dataclass(frozen=True)
class Scenario:
    """压力情景：以确定方式同时作用于多因子。"""

    scenario_id: str
    shocks: tuple[ScenarioShock, ...]


@dataclass(frozen=True)
class StressRequest:
    portfolio: Portfolio
    scenarios: tuple[Scenario, ...]
    include_baseline: bool = True


@dataclass(frozen=True)
class ScenarioPnl:
    scenario_id: str
    pnl_base_ccy: float
    valuation_base_ccy: float
    shocked_prices: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class StressResult:
    baseline_pnl: float
    baseline_valuation: float
    scenarios: tuple[ScenarioPnl, ...]
    missing_positions: tuple[str, ...]
    snapshot_version: int


@dataclass(frozen=True)
class Snapshot:
    """不可变行情/风险因子快照。

    prices: factor_id -> 因子价格（本币或其交易币种）
    fx_rates: ccy -> 兑 base_ccy 的汇率；base_ccy 自身恒为 1.0
    history: factor_id -> 历史日收益率序列（按时间顺序）
    volatilities: factor_id -> 波动率（预留字段）
    """

    version: int
    prices: dict[str, float]
    fx_rates: dict[str, float]
    history: dict[str, tuple[float, ...]]
    volatilities: dict[str, float] = field(default_factory=dict)


@dataclass(frozen=True)
class ExposureBucket:
    ccy: str
    asset_class: AssetClass | str
    exposure_base_ccy: float


@dataclass(frozen=True)
class Limits:
    """限额：集中度（单一资产类别敞口/总敞口）与杠杆（总绝对值/净值）。"""

    max_concentration: float
    max_leverage: float
    net_equity_base_ccy: float


@dataclass(frozen=True)
class LimitBreach:
    limit_name: str
    observed: float
    limit: float
    detail: str


@dataclass(frozen=True)
class ExposureResult:
    total_long: float
    total_short: float
    gross: float
    net: float
    by_asset_class: dict[str, float]
    by_ccy: dict[str, float]
    buckets: tuple[ExposureBucket, ...]
    missing_positions: tuple[str, ...]
    breaches: tuple[LimitBreach, ...]
    snapshot_version: int
