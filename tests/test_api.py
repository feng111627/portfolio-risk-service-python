"""业务场景：HTTP API 主流程与错误码、刷新后版本切换。"""
from __future__ import annotations

import pytest

pytestmark = pytest.mark.scenario("HTTP API")

VAR_BODY = {
    "portfolio": {
        "name": "d",
        "base_ccy": "USD",
        "positions": [
            {"position_id": "p1", "factor_id": "EQ_AAPL", "quantity": 100,
             "ccy": "USD", "asset_class": "equity"}
        ],
    },
    "confidence": 0.99,
    "horizon_days": 1,
}


def test_health(client):
    """场景：健康检查返回快照版本。"""
    assert client.get("/health").json()["snapshot_version"] == 1


def test_var_endpoint_main_flow(client):
    """场景：VaR 端点返回正常结构与正数亏损。"""
    r = client.post("/risk/var", json=VAR_BODY)
    assert r.status_code == 200
    body = r.json()
    assert body["var"] > 0
    assert body["expected_shortfall"] >= body["var"]
    assert body["sample_size"] == 250


def test_var_endpoint_distinct_error_codes(client):
    """场景：越界置信水平/持有期/样本不足各自返回可区分 error_code。"""
    r = client.post("/risk/var", json={**VAR_BODY, "confidence": 2.0})
    assert r.status_code == 422
    assert r.json()["error_code"] == "INVALID_CONFIDENCE"

    r = client.post("/risk/var", json={**VAR_BODY, "horizon_days": 0})
    # pydantic 层 ge=1 拦截
    assert r.status_code == 422

    r = client.post(
        "/risk/var",
        json={**VAR_BODY, "portfolio": {"name": "e", "base_ccy": "USD", "positions": []}},
    )
    assert r.status_code == 422
    assert r.json()["error_code"] == "EMPTY_PORTFOLIO"


def test_stress_endpoint_and_duplicate_scenario(client):
    """场景：压力端点基准/情景分离；重复情景返回 DUPLICATE_SCENARIO。"""
    body = {
        "portfolio": VAR_BODY["portfolio"],
        "scenarios": [
            {"scenario_id": "crash",
             "shocks": [{"factor_id": "EQ_AAPL", "type": "relative", "value": -0.2}]}
        ],
    }
    r = client.post("/risk/stress", json=body)
    assert r.status_code == 200
    data = r.json()
    assert data["baseline_pnl"] == 0.0
    assert data["scenarios"][0]["scenario_id"] == "crash"
    assert data["scenarios"][0]["pnl_base_ccy"] < 0

    body_dup = {
        "portfolio": VAR_BODY["portfolio"],
        "scenarios": [body["scenarios"][0], body["scenarios"][0]],
    }
    r = client.post("/risk/stress", json=body_dup)
    assert r.status_code == 422
    assert r.json()["error_code"] == "DUPLICATE_SCENARIO"


def test_exposure_endpoint_limit_breach(client):
    """场景：敞口端点在超限时返回 breaches。"""
    body = {
        "portfolio": VAR_BODY["portfolio"],
        "limits": {"max_concentration": 0.5, "max_leverage": 1.0,
                   "net_equity_base_ccy": 100.0},
    }
    r = client.post("/risk/exposure", json=body)
    assert r.status_code == 200
    names = {b["limit_name"] for b in r.json()["breaches"]}
    assert {"concentration", "leverage"} <= names


def test_admin_snapshot_refresh_switches_version_atomically(client):
    """场景：整份新快照刷新后，后续计算读到新版本。"""
    new_snap = {
        "version": 42,
        "prices": {"F": 11.0},
        "fx_rates": {"USD": 1.0},
        "history": {"F": [0.01] * 100},
        "volatilities": {},
    }
    r = client.post("/admin/snapshot", json=new_snap)
    assert r.status_code == 200
    assert client.get("/health").json()["snapshot_version"] == 42

    body = {
        "portfolio": {
            "name": "d", "base_ccy": "USD",
            "positions": [{"position_id": "x", "factor_id": "F", "quantity": 1,
                           "ccy": "USD", "asset_class": "equity"}],
        },
        "confidence": 0.95, "horizon_days": 1,
    }
    assert client.post("/risk/var", json=body).json()["snapshot_version"] == 42
