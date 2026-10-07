"""领域模型：组合、头寸、快照、情景、限额。"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Position:
    """单个头寸。

    instrument_id: 标的代码
    asset_class: 资产类别（如 equity / bond / fx / commodity）
    currency: 计价币种（ISO 代码，如 USD / CNY）
    quantity: 持仓数量（正为多，负为空）
    factor_id: 该标的价格所挂钩的风险因子 id
    """

    instrument_id: str
    asset_class: str
    currency: str
    quantity: float
    factor_id: str


@dataclass(frozen=True)
class Portfolio:
    """组合：一组头寸 + 基准币种。"""

    portfolio_id: str
    base_currency: str
    positions: tuple[Position, ...]


@dataclass(frozen=True)
class MarketSnapshot:
    """某一时刻的完整市场快照（不可变）。

    prices: instrument_id -> 最新价（标的自身币种）
    fx_rates: "CCY1/CCY2" -> 1 单位 CCY1 兑换 CCY2 的数量
    factor_returns: factor_id -> 历史日收益率序列（旧 -> 新）
    """

    prices: dict[str, float]
    fx_rates: dict[str, float]
    factor_returns: dict[str, tuple[float, ...]]
    version: int = 0


@dataclass(frozen=True)
class FactorShock:
    """单个风险因子的冲击。"""

    factor_id: str
    # 相对冲击，如 -0.10 表示该因子对应标的价格下跌 10%
    shock: float


@dataclass(frozen=True)
class FxShock:
    """单个币种的汇率冲击（相对基准币）。"""

    currency: str
    shock: float


@dataclass(frozen=True)
class StressScenario:
    """压力情景：命名 + 一组因子冲击 + 一组汇率冲击。"""

    name: str
    factor_shocks: tuple[FactorShock, ...] = ()
    fx_shocks: tuple[FxShock, ...] = ()


@dataclass(frozen=True)
class LimitRule:
    """限额规则。

    kind: "concentration"（单一资产类别敞口占比上限）
          或 "leverage"（总敞口 / 净资产上限）
    """

    kind: str
    threshold: float
    asset_class: str | None = None  # 仅 concentration 使用


@dataclass(frozen=True)
class LimitBreach:
    """一次限额超限记录。"""

    rule: LimitRule
    observed: float
    message: str


@dataclass(frozen=True)
class VarEsResult:
    """VaR/ES 计算结果。"""

    confidence: float
    horizon_days: int
    var: float
    es: float
    sample_size: int


@dataclass(frozen=True)
class ExposureReport:
    """敞口聚合结果。"""

    base_currency: str
    by_currency: dict[str, float] = field(default_factory=dict)
    by_asset_class: dict[str, float] = field(default_factory=dict)
    gross_exposure: float = 0.0
    net_exposure: float = 0.0
