import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client():
    # Fresh app (and therefore fresh in-memory stores) per test — the
    # module-level `app` singleton in app.main would otherwise leak state
    # between tests since InMemoryIncidentStore/InMemoryObservationStore
    # are plain dicts with no reset hook.
    return TestClient(create_app())


def firing_payload(**overrides):
    base = {
        "version": "4",
        "status": "firing",
        "receiver": "gateway",
        "groupLabels": {"alertname": "HighHTTPErrorRate"},
        "commonLabels": {"alertname": "HighHTTPErrorRate", "namespace": "cloudmart-prod"},
        "commonAnnotations": {},
        "externalURL": "http://alertmanager",
        "alerts": [
            {
                "status": "firing",
                "labels": {
                    "alertname": "HighHTTPErrorRate",
                    "severity": "critical",
                    "namespace": "cloudmart-prod",
                    "service": "order-service",
                },
                "annotations": {"summary": "HTTP 500 rate increased"},
                "startsAt": "2026-08-19T09:30:00Z",
                "endsAt": "0001-01-01T00:00:00Z",
                "generatorURL": "http://prometheus/graph",
                "fingerprint": "abc123",
            }
        ],
    }
    base.update(overrides)
    return base


def test_valid_firing_payload_creates_incident_and_observation(client):
    resp = client.post("/webhooks/alertmanager", json=firing_payload())
    assert resp.status_code == 202
    body = resp.json()

    assert len(body["observations_created"]) == 1
    assert body["incident"] is not None
    assert body["incident"]["severity"] == "critical"
    assert body["incident"]["affected_services"] == ["order-service"]
    assert body["incident"]["initial_alerts"] == ["HighHTTPErrorRate"]


def test_malformed_payload_missing_alerts_returns_422(client):
    resp = client.post("/webhooks/alertmanager", json={"status": "firing"})
    assert resp.status_code == 422


def test_empty_alerts_list_returns_422(client):
    resp = client.post("/webhooks/alertmanager", json=firing_payload(alerts=[]))
    assert resp.status_code == 422


def test_invalid_alert_status_returns_422(client):
    payload = firing_payload()
    payload["alerts"][0]["status"] = "bogus"
    resp = client.post("/webhooks/alertmanager", json=payload)
    assert resp.status_code == 422


def test_resolved_only_alert_does_not_create_incident(client):
    payload = firing_payload(status="resolved")
    payload["alerts"][0]["status"] = "resolved"
    resp = client.post("/webhooks/alertmanager", json=payload)
    assert resp.status_code == 202
    body = resp.json()
    assert body["incident"] is None
    assert len(body["observations_created"]) == 1


def test_multiple_firing_alerts_in_one_delivery_correlate_to_one_incident(client):
    payload = firing_payload()
    payload["alerts"].append(
        {
            "status": "firing",
            "labels": {
                "alertname": "HighLatency",
                "severity": "warning",
                "namespace": "cloudmart-prod",
                "service": "order-service",
            },
            "annotations": {},
            "startsAt": "2026-08-19T09:31:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
        }
    )
    resp = client.post("/webhooks/alertmanager", json=payload)
    body = resp.json()

    assert len(body["observations_created"]) == 2
    assert body["incident"] is not None
    # highest severity across the batch wins
    assert body["incident"]["severity"] == "critical"
    assert sorted(body["incident"]["initial_alerts"]) == [
        "HighHTTPErrorRate",
        "HighLatency",
    ]


def test_mixed_firing_and_resolved_only_links_firing_observations_to_incident(client):
    payload = firing_payload()
    payload["alerts"].append(
        {
            "status": "resolved",
            "labels": {
                "alertname": "OldAlert",
                "severity": "info",
                "namespace": "cloudmart-prod",
                "service": "product-service",
            },
            "annotations": {},
            "startsAt": "2026-08-19T09:00:00Z",
            "endsAt": "2026-08-19T09:10:00Z",
        }
    )
    resp = client.post("/webhooks/alertmanager", json=payload)
    body = resp.json()

    assert len(body["observations_created"]) == 2
    # only the firing alert's service is on the incident
    assert body["incident"]["affected_services"] == ["order-service"]


def test_incident_visible_via_incident_store(client):
    resp = client.post("/webhooks/alertmanager", json=firing_payload())
    incident_id = resp.json()["incident"]["incident_id"]

    store = client.app.state.incident_store
    import asyncio

    incident = asyncio.run(store.get(incident_id))
    assert incident is not None
    assert incident.status.value == "open"
    assert incident.current_phase.value == "detected"
