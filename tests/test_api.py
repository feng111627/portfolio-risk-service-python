"""业务场景：HTTP 接口主流程与失败原因的可区分性。"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from main import STORE, app


@pytest.fixture()
def client() -> TestClient:
    return TestClient(app)


class TestVarEsApi:
    def test_主流程(self, client):
        r = client.get("/var-es", params={"confidence": 0.95, "horizon_days": 1})
        assert r.status_code == 200
        body = r.json()
        assert body["var"] > 0 and body["es"] >= body["var"]
        assert body["sample_size"] > 0

    def test_置信水平越界返回可区分错误码(self, client):
        r = client.get("/var-es", params={"confidence": 1.5})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_confidence_level"

    def test_持有期越界返回可区分错误码(self, client):
        r = client.get("/var-es", params={"horizon_days": 0})
        assert r.status_code == 400
        assert r.json()["error"] == "invalid_holding_period"


class TestStressApi:
    def test_情景结果与基准结果分开返回(self, client):
        r = client.post(
            "/stress",
            json={"name": "crash", "factor_shocks": {"EQ_US": -0.1}},
        )
        assert r.status_code == 200
        body = r.json()
        assert body["base_value"] != body["stressed_value"]
        assert body["pnl"] == pytest.approx(
            body["stressed_value"] - body["base_value"]
        )

    def test_相同请求结果可复现(self, client):
        payload = {"name": "crash", "factor_shocks": {"EQ_US": -0.1}}
        a = client.post("/stress", json=payload).json()
        b = client.post("/stress", json=payload).json()
        assert a == b


class TestLimitsApi:
    def test_限额超限被检出(self, client):
        r = client.post(
            "/limits/check",
            json=[{"kind": "concentration", "threshold": 0.01, "asset_class": "equity"}],
        )
        assert r.status_code == 200
        body = r.json()
        assert body["breached"] is True
        assert body["breaches"][0]["kind"] == "concentration"

    def test_未超限(self, client):
        r = client.post(
            "/limits/check",
            json=[{"kind": "concentration", "threshold": 0.99, "asset_class": "equity"}],
        )
        assert r.json()["breached"] is False


class TestSnapshotRefreshApi:
    def test_刷新后版本递增且计算仍可用(self, client):
        before = STORE.version
        r = client.post("/snapshot/refresh")
        assert r.status_code == 200
        assert r.json()["snapshot_version"] == before + 1
        ok = client.get("/var-es", params={"confidence": 0.95})
        assert ok.status_code == 200
        assert ok.json()["snapshot_version"] == before + 1
