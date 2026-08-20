import pytest
from fastapi.testclient import TestClient

from app.main import create_app


@pytest.fixture()
def client(bypass_auth):
    # Fresh app (and therefore fresh in-memory stores) per test — the
    # module-level `app` singleton in app.main would otherwise leak state
    # between tests since InMemoryIncidentStore/InMemoryObservationStore
    # are plain dicts with no reset hook.
    app = create_app()
    bypass_auth(app)
    # A firing alert now also kicks off the step-10 Context Builder as a
    # background task, which TestClient runs synchronously before the
    # request returns. create_app() wires the real Prometheus/Loki/Tempo/
    # Kubernetes clients at their real (in-cluster-only) URLs — reachable
    # in prod, but here that means slow/hanging DNS lookups against
    # unreachable *.svc.cluster.local hosts. Webhook tests only care about
    # normalization/correlation, not live telemetry collection, so blank
    # the clients out here; the Context Builder's None-client path reports
    # each source UNAVAILABLE immediately instead of attempting real I/O.
    app.state.prometheus_client = None
    app.state.loki_client = None
    app.state.tempo_client = None
    app.state.kubernetes_client = None
    return TestClient(app)


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
    # step 10: the Context Builder background task runs to completion
    # (TestClient executes background tasks synchronously) and always
    # ends with READY_FOR_INVESTIGATION, even with every telemetry source
    # unavailable (this fixture blanks out all four adapter clients) —
    # "no data collected" and "data collected but nothing found" both
    # still mean the incident is ready to be looked at.
    assert incident.current_phase.value == "ready_for_investigation"


# --- step 9: cross-delivery correlation (spec section 7) -------------------


def test_same_alert_refiring_in_a_separate_delivery_reuses_the_incident(client):
    first = client.post("/webhooks/alertmanager", json=firing_payload())
    second = client.post("/webhooks/alertmanager", json=firing_payload())

    first_incident_id = first.json()["incident"]["incident_id"]
    second_incident_id = second.json()["incident"]["incident_id"]

    assert first_incident_id == second_incident_id
    store = client.app.state.incident_store
    import asyncio

    all_incidents = asyncio.run(store.list_all())
    assert len(all_incidents) == 1


def test_related_alert_in_a_separate_delivery_merges_into_same_incident(client):
    first = client.post("/webhooks/alertmanager", json=firing_payload())
    first_incident_id = first.json()["incident"]["incident_id"]

    latency_payload = firing_payload(
        groupLabels={"alertname": "HighLatency", "namespace": "cloudmart-prod"},
        commonLabels={"alertname": "HighLatency", "namespace": "cloudmart-prod"},
    )
    latency_payload["alerts"] = [
        {
            "status": "firing",
            "labels": {
                "alertname": "HighLatency",
                "severity": "warning",
                "namespace": "cloudmart-prod",
                "service": "order-service",
            },
            "annotations": {},
            "startsAt": "2026-08-19T09:35:00Z",
            "endsAt": "0001-01-01T00:00:00Z",
        }
    ]
    second = client.post("/webhooks/alertmanager", json=latency_payload)
    body = second.json()

    assert body["incident"]["incident_id"] == first_incident_id
    assert sorted(body["incident"]["initial_alerts"]) == ["HighHTTPErrorRate", "HighLatency"]
    assert body["incident"]["severity"] == "critical"


def test_unrelated_service_in_a_separate_delivery_creates_a_new_incident(client):
    first = client.post("/webhooks/alertmanager", json=firing_payload())
    first_incident_id = first.json()["incident"]["incident_id"]

    other_payload = firing_payload()
    other_payload["alerts"][0]["labels"]["service"] = "product-service"
    second = client.post("/webhooks/alertmanager", json=other_payload)

    assert second.json()["incident"]["incident_id"] != first_incident_id
