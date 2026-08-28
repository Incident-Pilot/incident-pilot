import pytest
from fastapi.testclient import TestClient

from app.config.settings import settings
from app.main import create_app


@pytest.fixture()
def unconfigured_client():
    # settings.api_key is "" by default in this test environment (never
    # set via GATEWAY_API_KEY) — exercises the fail-closed path directly.
    assert settings.api_key == ""
    return TestClient(create_app())


@pytest.fixture()
def configured_client():
    # settings is a frozen dataclass singleton, imported by reference into
    # every module that uses it (app.api.auth included) — monkeypatch's
    # normal setattr() would raise FrozenInstanceError, and replacing the
    # object outright wouldn't be visible to those already-bound
    # references. Mutate the existing instance directly and restore it.
    original = settings.api_key
    object.__setattr__(settings, "api_key", "test-key-123")
    app = create_app()
    app.state.prometheus_client = None
    app.state.loki_client = None
    app.state.tempo_client = None
    app.state.kubernetes_client = None
    try:
        yield TestClient(app)
    finally:
        object.__setattr__(settings, "api_key", original)


def test_protected_route_fails_closed_when_no_api_key_configured(unconfigured_client):
    resp = unconfigured_client.get("/topology")
    assert resp.status_code == 503


def test_health_and_ready_never_require_auth(unconfigured_client):
    assert unconfigured_client.get("/health").status_code == 200
    assert unconfigured_client.get("/ready").status_code == 200


def test_protected_route_rejects_missing_authorization_header(configured_client):
    resp = configured_client.get("/topology")
    assert resp.status_code == 401


def test_protected_route_rejects_wrong_key(configured_client):
    resp = configured_client.get(
        "/topology", headers={"Authorization": "Bearer wrong-key"}
    )
    assert resp.status_code == 401


def test_protected_route_rejects_non_bearer_scheme(configured_client):
    resp = configured_client.get(
        "/topology", headers={"Authorization": "Basic test-key-123"}
    )
    assert resp.status_code == 401


def test_protected_route_accepts_correct_bearer_token(configured_client):
    resp = configured_client.get(
        "/topology", headers={"Authorization": "Bearer test-key-123"}
    )
    assert resp.status_code == 200


def test_webhook_route_is_protected(configured_client):
    resp = configured_client.post("/webhooks/alertmanager", json={"status": "firing", "alerts": []})
    assert resp.status_code == 401


def test_ingest_routes_are_protected(configured_client):
    assert configured_client.post("/ingest/gitleaks", json=[]).status_code == 401
    assert configured_client.post("/ingest/trivy", json={}).status_code == 401


def test_incidents_routes_are_protected(configured_client):
    assert configured_client.get("/incidents").status_code == 401
    assert configured_client.get("/incidents/INC-0001").status_code == 401
    assert (
        configured_client.patch(
            "/incidents/INC-0001/status", json={"status": "resolved"}
        ).status_code
        == 401
    )


def test_services_route_is_protected(configured_client):
    assert configured_client.get("/services").status_code == 401
