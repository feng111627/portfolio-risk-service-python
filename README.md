# portfolio-risk-service

本地运行的组合市场风险度量服务（FastAPI）。行情与风险因子全部由本地模拟数据
确定性生成，**不接任何外部行情源或付费服务**。

## 功能概览

| 能力 | 入口 | 说明 |
| --- | --- | --- |
| 历史模拟法 VaR / ES | `GET /var-es?confidence=&horizon_days=` | 置信水平、持有期由请求参数指定 |
| 压力情景 | `POST /stress` | 多标的、多因子、含汇率冲击的情景应用 |
| 敞口聚合 | `GET /exposure` | 按币种与资产类别聚合（基准币计） |
| 限额校验 | `POST /limits/check` | 集中度 / 杠杆规则 |
| 快照刷新 | `POST /snapshot/refresh` | 本地模拟数据原子演化到下一版 |

## 风险度量口径

- **估值**：头寸基准币价值 = 数量 × 标的最新价 × 汇率（标的币种 → 基准币）。
- **历史模拟**：用头寸挂钩风险因子的历史日收益率序列回放组合日盈亏
  `pnl_t = Σ value_i × r_{f_i,t}`；各因子序列取最近 n 个样本对齐，
  n 为所用序列的最短长度。
- **持有期**：H 日盈亏按 √H 时间缩放。
- **VaR**：损失序列（升序）的 confidence 分位，索引向上取整（保守）。
- **ES**：不小于 VaR 的尾部损失均值。
- VaR / ES 均以**正数表示损失金额**（基准币）。

### 失败语义（不会静默返回"看起来正常"的数值）

| 情况 | 异常 | HTTP 错误码字段 |
| --- | --- | --- |
| 历史样本为空 | `EmptySampleError` | `empty_sample` |
| 样本数 < ⌈1/(1−confidence)⌉ | `InsufficientSampleError` | `insufficient_sample` |
| 置信水平不在开区间 (0, 1) | `InvalidConfidenceLevelError` | `invalid_confidence_level` |
| 持有期不是 ≥1 的整数 | `InvalidHorizonError` | `invalid_holding_period` |
| 缺标的行情 | `MissingMarketDataError` | `missing_market_data` |
| 缺汇率 | `MissingFxRateError` | `missing_fx_rate` |
| 缺因子历史 | `MissingHistoryError` | `missing_history` |

以上均以 HTTP 400 + `{"error": <code>, "message": ...}` 返回，彼此可区分。

## 压力情景规则

- 情景 = 名称 + 一组因子冲击 + 一组汇率冲击，均以**相对形式**给出：
  - 因子冲击：`price' = price × (1 + shock)`，只作用于挂钩该因子的标的；
  - 汇率冲击：`fx' = fx × (1 + shock)`，作用于该币种兑基准币的汇率。
- 未出现在情景中的因子 / 币种，冲击为 0（确定性缺省）。
- 情景从**同一不可变快照**出发估值，与基准估值各自独立计算一次后相减得
  盈亏：同一情景重复应用不会叠加冲击，也不会与基准重复计价；
  接口同时返回 `base_value` 与 `stressed_value`，两者清楚分开。
- 输入（组合、快照、情景）相同则结果完全相同（可复现）。

## 限额规则

- **concentration**：`|某资产类别敞口| / 总敞口 ≤ threshold`，需带 `asset_class`。
- **leverage**：`总敞口 / |净敞口| ≤ threshold`。
- 总敞口 = Σ|头寸基准币价值|；净敞口 = Σ头寸基准币价值（空头抵减）。

## 数据缺失的缺省约定

- 估值类计算（VaR/ES、情景、聚合）缺行情 / 缺汇率 / 缺历史：**明确报错**，
  原因可区分（见上表），不猜、不补、不静默。
- 聚合与限额的缺省语义（稳定）：
  - 不存在的资产类别 / 币种敞口视为 0；
  - 总敞口为 0 时，集中度与杠杆均视为 0（不超限）；
  - 净敞口为 0 而总敞口非 0 时，杠杆视为 +∞（必然超限）；
  - 基准币头寸不需要汇率（汇率恒为 1）。

## 快照刷新的并发保证

- `MarketSnapshot` 为不可变对象；刷新方整体构造新快照，不原地修改。
- `SnapshotStore.refresh()` 在锁内**一次性替换引用**，不存在"更新到一半"
  的中间态；`get()` 读到的要么是完整的旧快照，要么是完整的新快照。
- 计算请求拿到快照引用后，即使随后发生刷新，其计算仍基于同一完整快照，
  结果可复现。

## 本地验证

```bash
uv sync                 # 安装依赖
uv run pytest           # 全部测试（-v 已内置，日志按业务场景分组展示用例与结果）
uv run python main.py   # 启动服务（FastAPI）
```

测试覆盖的关键场景（与测试类一一对应）：

- `TestVarEsMainFlow` / `TestInvalidParams` / `TestSampleBoundaries` /
  `TestMissingData`：VaR/ES 主流程、非法参数、空样本与样本不足、缺失数据；
- `TestScenarioApplication` / `TestRepeatAndBaselineIsolation`：情景确定性
  应用、重复应用不叠加、情景与基准隔离、可复现；
- `TestExposureAggregation` / `TestLimitChecks`：聚合、集中度与杠杆超限、
  缺省语义；
- `TestAtomicRefresh`：原子刷新与刷新期间 8 线程并发读取的一致性；
- `tests/test_api.py`：HTTP 层主流程与错误码可区分性。

## 目录结构

```
main.py                  # FastAPI 入口与路由
risk_service/
  models.py              # 领域模型（不可变 dataclass）
  errors.py              # 可区分的业务错误类型
  var_es.py              # 历史模拟法 VaR/ES
  stress.py              # 压力情景应用
  limits.py              # 敞口聚合与限额校验
  snapshot.py            # 原子快照存储
  local_data.py          # 本地模拟行情与因子数据（种子确定性）
tests/                   # pytest 场景化测试
```
