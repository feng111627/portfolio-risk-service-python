# 组合市场风险度量服务（本地）

一套完全本地运行的组合市场风险度量服务，基于 FastAPI。提供：

- 历史模拟法组合 **VaR / Expected Shortfall**（置信水平、持有期可配）
- 多标的、多因子的确定性**压力情景**
- 按币种 / 资产类别聚合**敞口**与**限额校验**（集中度、杠杆）
- 风险因子 / 行情的**原子快照刷新**

行情与风险因子**全部来自本地确定性模拟数据，不接入任何外部行情源或付费服务**。

---

## 1. 目录结构

```
main.py                     FastAPI 入口，装配 store / router / 异常处理
risk_service/
  models.py                 领域模型、错误码（不可变值对象）
  data.py                   本地确定性模拟快照（固定种子 LCG + Box-Muller）
  store.py                  原子快照存储（锁 + 不可变快照引用）
  valuation.py              线性头寸计价与缺失数据缺省语义
  risk_metrics.py           历史模拟法 VaR / ES
  scenarios.py              压力情景应用
  limits.py                 敞口聚合与限额校验
  api.py                    Pydantic DTO、HTTP 路由、错误码映射
tests/                      pytest 测试（按业务场景归类，结尾打印覆盖清单）
```

引擎层（除 `api.py`）不依赖 Web，纯函数 + 不可变数据，便于复现与测试。

---

## 2. 风险度量口径（VaR / ES）

端点：`POST /risk/var`

1. **数据对齐**：取组合中各风险因子历史**日收益率的最短公共长度**，按时间顺序对齐，不做插值或补点。
2. **历史组合盈亏**：对每个历史日，按线性 payoff 重算组合盈亏（全部折算到组合本币）：
   `pnl_t = Σ_i quantity_i * price_i * r_{i,t} / fx_i`
3. **持有期缩放**：`pnl_{t,h} = pnl_t * sqrt(h)`，`h = horizon_days`。
4. **VaR**：取亏损分布（亏损取正）的 `(1-confidence)` 经验分位，
   索引 `i = floor(n*(1-confidence))`，以**正数表示亏损**。
5. **Expected Shortfall**：`VaR` 及更坏尾部亏损的算术平均（`losses[0..i]` 均值），故恒有 `ES >= VaR`。

### 显式拒绝（绝不静默返回看似正常的数值）

| 情况 | error_code | HTTP |
|---|---|---|
| 置信水平不在 `(0,1)` 开区间 | `INVALID_CONFIDENCE` | 422 |
| 持有期 `< 1` | `INVALID_HORIZON` | 422 |
| 组合无头寸 | `EMPTY_PORTFOLIO` | 422 |
| 某因子有价格但缺历史收益率 | `MISSING_HISTORY` | 422 |
| 可计价头寸为空 / 共同样本长度为 0 | `EMPTY_SAMPLE` | 422 |
| 样本量不足以覆盖置信水平（需 `n >= ceil(1/(1-confidence))`） | `INSUFFICIENT_SAMPLE` | 422 |

例如 99% 置信水平要求至少 100 个历史观测；95% 要求至少 20 个。
部分头寸缺行情/汇率时，跳过并在响应的 `missing_positions` 中列出。

请求示例：

```json
POST /risk/var
{
  "portfolio": {
    "name": "eq", "base_ccy": "USD",
    "positions": [
      {"position_id": "p1", "factor_id": "EQ_AAPL",
       "quantity": 100, "ccy": "USD", "asset_class": "equity"}
    ]
  },
  "confidence": 0.99,
  "horizon_days": 1
}
```

---

## 3. 压力情景规则

端点：`POST /risk/stress`

- 情景由一组**因子冲击**组成，以确定方式同时作用于多标的、多因子。
- 冲击类型：
  - `relative`：`p' = p * (1 + value)`
  - `absolute`：`p' = p + value`
  - `vol_shift`：`vol' = vol + value`（当前线性组合不受波动率影响，价格不变，语义明确）
- **基准只计价一次**；每个情景在“基准快照的一份独立拷贝”上施加冲击后再计价，
  情景之间互不影响、不连锁，情景结果与基准结果在响应中清楚分离
  （`baseline_valuation` 与每个 `scenarios[*]`），因此不存在重复计价。
- 同一 `scenario_id` 在同一请求中重复提交：直接以 `DUPLICATE_SCENARIO` 拒绝。
- 冲击引用快照中不存在的因子、或冲击类型非法：`INVALID_SCENARIO`。
- 情景应用是**纯函数**，不修改基准快照；同一输入 + 同一快照结果逐字节可复现。
- 缺行情 / 缺汇率头寸按统一缺省语义跳过，列入 `missing_positions`。

---

## 4. 敞口聚合与限额规则

端点：`POST /risk/exposure`

- 敞口按头寸当前市值折算到组合本币计，支持多头（正）与空头（负）。
- 按**资产类别**聚合有符号敞口，按**币种**聚合本币口径敞口；
  同时返回 `total_long / total_short / gross / net`。
- **毛敞口（gross）在头寸级别取绝对值后求和**（杠杆毛名义额），
  多空不会在同一桶内互相抵消；**净敞口（net）**为有符号求和。
- **集中度**：`|某资产类别有符号敞口| / gross`，超过 `max_concentration` 记一条 breach。
- **杠杆**：`gross / net_equity_base_ccy`，超过 `max_leverage` 记一条 breach。
- 所有超限逐条在响应 `breaches` 中返回（含观测值、阈值、明细），
  不因超限抛异常而丢失其他桶的结果；`net_equity_base_ccy <= 0` 属配置非法，
  返回 `LIMIT_CONFIG_INVALID`。

---

## 5. 数据缺失的缺省约定

为避免静默产生“看起来正常”的数字，缺失语义保持**明确且稳定**：

1. **本币头寸汇率**：头寸币种 == 组合本币时，汇率恒按 `1.0` 处理，无需在 `fx_rates` 中提供。
2. **缺汇率**（外币头寸在 `fx_rates` 中无正汇率）：该头寸**跳过**，计入 `missing_positions`，**不按 0 计**。
3. **缺行情**（`prices` 中无该因子）：该头寸**跳过**，计入 `missing_positions`。
4. **缺历史**：VaR/ES 场景下，只要某可计价因子缺历史收益率即返回 `MISSING_HISTORY`；
   情景 / 敞口场景不依赖历史，不受影响。
5. `fx_rates[ccy]` 定义为“**1 单位组合本币兑多少该币种**”，本币恒为 1.0。

被跳过的头寸不参与计价、风险与敞口聚合，调用方依据 `missing_positions` 决定后续处理。

---

## 6. 快照刷新的并发保证

- `Snapshot` 为**不可变值对象**；`SnapshotStore` 用一把锁保护一个快照引用。
- 刷新在锁外把**整份新快照完整构造**好，临界区内只做一次引用替换。
- 因此并发计算请求通过 `store.get()` 拿到的，要么是**完整旧快照**，
  要么是**完整新快照**，绝不会读到更新到一半的中间状态。
- 刷新端点：`POST /admin/snapshot`（整体提交 prices / fx_rates / history / version），
  成功后所有后续计算读到新版本。
- 尚未载入任何快照时读取：返回 `MISSING_RISK_FACTOR`，不返回 `None`。

并发一致性由 `tests/test_concurrency.py` 验证：6 个读线程持续计算、
1 个写线程持续替换快照，期间校验每次读到的快照其
`version / prices / history` 三者自洽，且计算结果版本号与快照一致。

---

## 7. 本地验证方法

依赖通过 [uv](https://docs.astral.sh/uv/) 管理，完全离线数据。

```bash
# 安装依赖（含 pytest / httpx 开发依赖）
uv sync

# 运行全部测试；结尾打印“业务场景覆盖”清单（场景 / 用例 / 结果）
uv run python -m pytest tests/

# 只看某一业务场景
uv run python -m pytest tests/test_var.py -v

# 启动服务
uv run python main.py
# 打开 http://127.0.0.1:8000/docs 可交互调用
```

测试按业务场景归类（`@pytest.mark.scenario(...)`），会话结束打印覆盖清单，
当前覆盖 5 个场景：VaR/ES 历史模拟、压力情景、敞口聚合与限额、
快照刷新并发一致性、HTTP API。

---

## 8. 本地模拟数据说明

`risk_service/data.py` 使用固定种子的 LCG 与 Box-Muller 变换生成
6 个跨币种因子（美股 AAPL/TSLA、港股 BABA、美债、欧元汇率、黄金）
的价格、汇率与 250 天日收益率。因种子与算法固定，
`build_initial_snapshot()` 每次返回**逐字节相同**的快照，保证测试与本地验证可复现。
