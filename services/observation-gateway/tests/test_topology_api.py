import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(bypass_auth):
    app = create_app()
    bypass_auth(app)
    # Same reasoning as the webhook test fixture: GET /topology's builder
    # calls the real Kubernetes/Tempo clients, which point at real
    # (in-cluster-only) URLs by default — blank them out so this test
    # only exercises the static-seed fallback path, not live network I/O.
    app.state.tempo_client = None
    app.state.kubernetes_client = None
    return TestClient(app)


def test_get_topology_returns_known_call_chain(client):
    resp = client.get("/topology")
    assert resp.status_code == 200
    body = resp.json()

    assert body["namespace"] == "cloudmart-prod"
    assert body["topology"]["frontend"] == ["product-service", "order-service", "user-service"]
    assert body["topology"]["order-service"] == ["product-service", "notification-service"]


def test_get_topology_is_idempotent_across_calls(client):
    first = client.get("/topology").json()
    second = client.get("/topology").json()
    assert first == second


def test_get_services_empty_before_topology_ever_built(client):
    resp = client.get("/services")
    assert resp.status_code == 200
    assert resp.json()["services"] == []


def test_get_services_reflects_last_built_topology(client):
    client.get("/topology")  # populates the topology store with the static seed
    resp = client.get("/services")
    assert resp.status_code == 200
    assert resp.json()["services"] == [
        "frontend",
        "notification-service",
        "order-service",
        "product-service",
        "user-service",
    ]
