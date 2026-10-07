"""风险度量服务的错误类型定义。

每种失败原因都是可区分的独立异常类型，API 层据此返回不同的错误码。
"""

from __future__ import annotations


class RiskServiceError(Exception):
    """所有业务错误的基类。"""

    code: str = "risk_service_error"

    def __init__(self, message: str = "") -> None:
        super().__init__(message or self.code)
        self.message = message or self.code


class EmptySampleError(RiskServiceError):
    """历史样本为空。"""

    code = "empty_sample"


class InsufficientSampleError(RiskServiceError):
    """样本数量不足以覆盖所选置信水平。"""

    code = "insufficient_sample"

    def __init__(self, required: int, actual: int) -> None:
        super().__init__(f"need at least {required} samples, got {actual}")
        self.required = required
        self.actual = actual


class InvalidConfidenceLevelError(RiskServiceError):
    """置信水平越界（必须位于开区间 (0, 1)）。"""

    code = "invalid_confidence_level"


class InvalidHorizonError(RiskServiceError):
    """持有期越界（必须为 >= 1 的整数）。"""

    code = "invalid_holding_period"


class MissingMarketDataError(RiskServiceError):
    """缺少标的行情。"""

    code = "missing_market_data"


class MissingFxRateError(RiskServiceError):
    """缺少汇率。"""

    code = "missing_fx_rate"


class MissingHistoryError(RiskServiceError):
    """缺少风险因子历史序列。"""

    code = "missing_history"
