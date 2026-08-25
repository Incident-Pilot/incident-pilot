import asyncio
from datetime import datetime, timezone

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from shared.models import (
    Correlation,
    Evidence,
    EvidenceType,
    Incident,
    Observation,
    ObservationSource,
    Severity,
    SignalType,
)


def run(coro):
    return asyncio.run(coro)


@pytest.fixture()
def client(bypass_auth):
    app = create_app()
    bypass_auth(app)
    return TestClient(app)


def make_incident(**overrides) -> Incident:
    now = datetime.now(timezone.utc)
    defaults = dict(
        incident_id="INC-TEST0001",
        title="HighHTTPErrorRate",
        severity=Severity.CRITICAL,
        created_at=now,
        updated_at=now,
        source="alertmanager",
        affected_services=["order-service"],
        affected_namespace="cloudmart-prod",
        initial_alerts=["HighHTTPErrorRate"],
    )
    defaults.update(overrides)
    return Incident(**defaults)


def seed_incident(client, incident: Incident):
    run(client.app.state.incident_store.save(incident))


def test_get_incidents_empty(client):
    resp = client.get("/incidents")
    assert resp.status_code == 200
    assert resp.json()["incidents"] == []


def test_get_incidents_lists_seeded_incident(client):
    seed_incident(client, make_incident())
    resp = client.get("/incidents")
    body = resp.json()
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["incident_id"] == "INC-TEST0001"


def test_get_incidents_returns_newest_first(client):
    older = make_incident(
        incident_id="INC-OLDER", created_at=datetime(2026, 8, 20, 8, 0, tzinfo=timezone.utc)
    )
    newer = make_incident(
        incident_id="INC-NEWER", created_at=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc)
    )
    seed_incident(client, older)
    seed_incident(client, newer)

    resp = client.get("/incidents")
    ids = [i["incident_id"] for i in resp.json()["incidents"]]
    assert ids == ["INC-NEWER", "INC-OLDER"]


def test_get_incidents_filters_by_status(client):
    open_incident = make_incident(incident_id="INC-OPEN")
    resolved_incident = make_incident(incident_id="INC-RESOLVED", status="resolved")
    seed_incident(client, open_incident)
    seed_incident(client, resolved_incident)

    resp = client.get("/incidents?status=resolved")
    body = resp.json()
    assert len(body["incidents"]) == 1
    assert body["incidents"][0]["incident_id"] == "INC-RESOLVED"


def test_get_incident_detail_not_found_returns_404(client):
    resp = client.get("/incidents/INC-DOES-NOT-EXIST")
    assert resp.status_code == 404


def test_get_incident_detail_matches_spec_shape(client):
    incident = make_incident()
    seed_incident(client, incident)

    obs = Observation.new(
        source=ObservationSource.ALERTMANAGER,
        signal_type=SignalType.ALERT,
        cluster="cloudmart-k3s",
        signal="HighHTTPErrorRate",
        service="order-service",
        correlation=Correlation(incident_id=incident.incident_id),
    )
    run(client.app.state.observation_store.save(obs))

    evidence = Evidence(
        evidence_id="ev-test0001",
        incident_id=incident.incident_id,
        type=EvidenceType.METRIC,
        source=ObservationSource.PROMETHEUS,
        timestamp=datetime.now(timezone.utc),
        summary="HTTP 500 rate increased",
    )
    run(client.app.state.evidence_store.save(evidence))

    run(
        client.app.state.topology_store.save_service(
            "order-service", "cloudmart-prod", ["product-service", "notification-service"]
        )
    )
    run(client.app.state.topology_store.save_service("frontend", "cloudmart-prod", ["order-service"]))

    resp = client.get(f"/incidents/{incident.incident_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["incident_id"] == incident.incident_id
    assert body["severity"] == "critical"
    assert body["affected_services"] == ["order-service"]
    assert body["observations"] == [obs.observation_id]
    assert body["evidence"] == [
        {"id": "ev-test0001", "type": "metric", "summary": "HTTP 500 rate increased"}
    ]
    # topology subgraph is limited to affected_services — "frontend" isn't
    # one of them, so it must not appear even though it's in the full graph
    assert body["topology"] == {"order-service": ["product-service", "notification-service"]}


def test_get_incident_observations_returns_full_objects(client):
    incident = make_incident()
    seed_incident(client, incident)
    obs = Observation.new(
        source=ObservationSource.LOKI,
        signal_type=SignalType.LOG,
        cluster="c",
        signal="log_line",
        correlation=Correlation(incident_id=incident.incident_id),
    )
    run(client.app.state.observation_store.save(obs))

    resp = client.get(f"/incidents/{incident.incident_id}/observations")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["observation_id"] == obs.observation_id
    assert body[0]["source"] == "loki"


def test_get_incident_observations_404_for_missing_incident(client):
    resp = client.get("/incidents/INC-DOES-NOT-EXIST/observations")
    assert resp.status_code == 404


def test_get_incident_evidence_returns_full_objects(client):
    incident = make_incident()
    seed_incident(client, incident)
    evidence = Evidence(
        evidence_id="ev-test0002",
        incident_id=incident.incident_id,
        type=EvidenceType.LOG,
        source=ObservationSource.LOKI,
        timestamp=datetime.now(timezone.utc),
        summary="database connection timeout",
    )
    run(client.app.state.evidence_store.save(evidence))

    resp = client.get(f"/incidents/{incident.incident_id}/evidence")
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 1
    assert body[0]["evidence_id"] == "ev-test0002"
    assert body[0]["summary"] == "database connection timeout"


def test_get_incident_evidence_404_for_missing_incident(client):
    resp = client.get("/incidents/INC-DOES-NOT-EXIST/evidence")
    assert resp.status_code == 404


def test_get_incident_timeline_sorted_chronologically(client):
    incident = make_incident()
    seed_incident(client, incident)

    early_obs = Observation.new(
        source=ObservationSource.ALERTMANAGER,
        signal_type=SignalType.ALERT,
        cluster="c",
        signal="HighHTTPErrorRate",
        correlation=Correlation(incident_id=incident.incident_id),
        timestamp=datetime(2026, 8, 20, 9, 0, tzinfo=timezone.utc),
    )
    late_evidence = Evidence(
        evidence_id="ev-late",
        incident_id=incident.incident_id,
        type=EvidenceType.METRIC,
        source=ObservationSource.PROMETHEUS,
        timestamp=datetime(2026, 8, 20, 9, 5, tzinfo=timezone.utc),
        summary="later evidence",
    )
    run(client.app.state.observation_store.save(early_obs))
    run(client.app.state.evidence_store.save(late_evidence))

    resp = client.get(f"/incidents/{incident.incident_id}/timeline")
    assert resp.status_code == 200
    timeline = resp.json()["timeline"]
    assert len(timeline) == 2
    assert timeline[0]["kind"] == "observation"
    assert timeline[1]["kind"] == "evidence"
    # Every entry needs timestamp/description/source regardless of kind, so
    # a consumer can build a flat TimelineEvent without kind-specific logic.
    for entry in timeline:
        assert entry["timestamp"]
        assert entry["description"]
        assert entry["source"]
    assert timeline[1]["source"] == "prometheus"


def test_get_incident_timeline_404_for_missing_incident(client):
    resp = client.get("/incidents/INC-DOES-NOT-EXIST/timeline")
    assert resp.status_code == 404


def test_get_incident_source_status_returns_persisted_statuses(client):
    from app.collectors.base import SourceCollectionStatus, SourceStatus

    incident = make_incident()
    seed_incident(client, incident)
    run(
        client.app.state.source_status_store.save_many(
            incident.incident_id,
            [
                SourceCollectionStatus(
                    source="prometheus", status=SourceStatus.AVAILABLE, observation_count=12
                ),
                SourceCollectionStatus(
                    source="kubernetes",
                    status=SourceStatus.UNAVAILABLE,
                    error="RBAC forbidden",
                    observation_count=0,
                ),
            ],
        )
    )

    resp = client.get(f"/incidents/{incident.incident_id}/source-status")
    assert resp.status_code == 200
    body = resp.json()
    assert body["incident_id"] == incident.incident_id
    by_source = {s["source"]: s for s in body["source_status"]}
    assert by_source["prometheus"] == {
        "source": "prometheus",
        "status": "available",
        "error": None,
        "observation_count": 12,
    }
    assert by_source["kubernetes"]["status"] == "unavailable"
    assert by_source["kubernetes"]["error"] == "RBAC forbidden"


def test_get_incident_source_status_404_for_missing_incident(client):
    resp = client.get("/incidents/INC-DOES-NOT-EXIST/source-status")
    assert resp.status_code == 404


def test_get_incident_source_status_empty_before_context_collection(client):
    incident = make_incident()
    seed_incident(client, incident)

    resp = client.get(f"/incidents/{incident.incident_id}/source-status")
    assert resp.status_code == 200
    assert resp.json()["source_status"] == []
