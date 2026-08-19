"""
Real-database integration tests for PostgresObservationStore /
PostgresIncidentStore — the row_to_* mapping and the SQL itself can only
be genuinely proven against an actual Postgres, not a mock.

Skipped by default (no live cluster/DB in CI or a fresh dev sandbox) — set
POSTGRES_DSN to a reachable, disposable database to run these:

    POSTGRES_DSN=postgresql://incidentpilot@127.0.0.1:5433/incidentpilot \\
        pytest services/observation-gateway/tests/test_postgres_store_integration.py -v

Each test truncates the tables it touches first, so run order doesn't
matter and this is safe to repeat against the same database.
"""

import os
from datetime import datetime, timedelta, timezone

import asyncpg
import pytest

from app.storage.postgres.evidence_store import PostgresEvidenceStore
from app.storage.postgres.incident_store import PostgresIncidentStore
from app.storage.postgres.observation_store import PostgresObservationStore
from app.storage.postgres.pool import create_pool, init_schema
from shared.models import (
    Correlation,
    Evidence,
    EvidenceType,
    Incident,
    Observation,
    ObservationSource,
    RawReference,
    Severity,
    SignalType,
)

POSTGRES_DSN = os.getenv("POSTGRES_DSN", "")

pytestmark = pytest.mark.skipif(
    not POSTGRES_DSN, reason="POSTGRES_DSN not set — skipping real-database integration tests"
)


@pytest.fixture()
async def pool():
    p = await create_pool(POSTGRES_DSN)
    await init_schema(p)
    async with p.acquire() as conn:
        await conn.execute(
            "TRUNCATE evidence, deployments, service_topology, observations, incidents"
        )
    yield p
    await p.close()


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


def make_observation(**overrides) -> Observation:
    defaults = dict(
        source=ObservationSource.ALERTMANAGER,
        signal_type=SignalType.ALERT,
        severity=Severity.CRITICAL,
        cluster="cloudmart-k3s",
        namespace="cloudmart-prod",
        service="order-service",
        resource="order-service-abc123",
        signal="HighHTTPErrorRate",
        value=0.42,
        labels={"alertname": "HighHTTPErrorRate", "severity": "critical"},
        metadata={"fingerprint": "abc123"},
        correlation=Correlation(incident_id=None, trace_id="trace-1"),
    )
    defaults.update(overrides)
    return Observation.new(**defaults)


@pytest.mark.anyio
async def test_incident_round_trip(pool):
    store = PostgresIncidentStore(pool)
    incident = make_incident()

    await store.save(incident)
    fetched = await store.get(incident.incident_id)

    assert fetched is not None
    assert fetched.incident_id == incident.incident_id
    assert fetched.title == incident.title
    assert fetched.severity == Severity.CRITICAL
    assert fetched.affected_services == ["order-service"]
    assert fetched.initial_alerts == ["HighHTTPErrorRate"]
    assert fetched.created_at == incident.created_at


@pytest.mark.anyio
async def test_incident_get_missing_returns_none(pool):
    store = PostgresIncidentStore(pool)
    assert await store.get("INC-DOES-NOT-EXIST") is None


@pytest.mark.anyio
async def test_incident_save_is_upsert(pool):
    store = PostgresIncidentStore(pool)
    incident = make_incident()
    await store.save(incident)

    updated = incident.model_copy(update={"title": "Renamed", "severity": Severity.WARNING})
    await store.save(updated)

    fetched = await store.get(incident.incident_id)
    assert fetched.title == "Renamed"
    assert fetched.severity == Severity.WARNING

    all_incidents = await store.list_all()
    assert len(all_incidents) == 1


@pytest.mark.anyio
async def test_observation_round_trip_preserves_jsonb_and_correlation(pool):
    obs_store = PostgresObservationStore(pool)
    observation = make_observation()

    await obs_store.save(observation)
    rows = await obs_store.list_all()

    assert len(rows) == 1
    fetched = rows[0]
    assert fetched.observation_id == observation.observation_id
    assert fetched.labels == {"alertname": "HighHTTPErrorRate", "severity": "critical"}
    assert fetched.metadata == {"fingerprint": "abc123"}
    assert fetched.correlation.trace_id == "trace-1"
    assert fetched.correlation.incident_id is None
    assert fetched.value == 0.42


@pytest.mark.anyio
async def test_observation_linked_to_incident_via_fk(pool):
    incident_store = PostgresIncidentStore(pool)
    obs_store = PostgresObservationStore(pool)

    incident = make_incident()
    await incident_store.save(incident)

    observation = make_observation(
        correlation=Correlation(incident_id=incident.incident_id)
    )
    await obs_store.save(observation)

    linked = await obs_store.list_by_incident(incident.incident_id)
    assert len(linked) == 1
    assert linked[0].observation_id == observation.observation_id

    unrelated = await obs_store.list_by_incident("INC-SOME-OTHER-ONE")
    assert unrelated == []


@pytest.mark.anyio
async def test_observation_fk_rejects_unknown_incident_id(pool):
    obs_store = PostgresObservationStore(pool)
    observation = make_observation(correlation=Correlation(incident_id="INC-GHOST"))

    with pytest.raises(asyncpg.ForeignKeyViolationError):
        await obs_store.save(observation)


# --- step 9: find_correlation_candidates (spec section 7) ------------------


@pytest.mark.anyio
async def test_find_correlation_candidates_matches_namespace_service_and_window(pool):
    store = PostgresIncidentStore(pool)
    incident = make_incident()
    await store.save(incident)

    candidates = await store.find_correlation_candidates(
        namespace="cloudmart-prod",
        services=["order-service"],
        since=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    assert [c.incident_id for c in candidates] == [incident.incident_id]


@pytest.mark.anyio
async def test_find_correlation_candidates_excludes_resolved_incidents(pool):
    store = PostgresIncidentStore(pool)
    resolved = make_incident(status="resolved")
    await store.save(resolved)

    candidates = await store.find_correlation_candidates(
        namespace="cloudmart-prod",
        services=["order-service"],
        since=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    assert candidates == []


@pytest.mark.anyio
async def test_find_correlation_candidates_excludes_stale_incidents(pool):
    store = PostgresIncidentStore(pool)
    stale_time = datetime.now(timezone.utc) - timedelta(hours=2)
    stale = make_incident(created_at=stale_time, updated_at=stale_time)
    await store.save(stale)

    candidates = await store.find_correlation_candidates(
        namespace="cloudmart-prod",
        services=["order-service"],
        since=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    assert candidates == []


@pytest.mark.anyio
async def test_find_correlation_candidates_requires_service_overlap(pool):
    store = PostgresIncidentStore(pool)
    incident = make_incident(affected_services=["product-service"])
    await store.save(incident)

    candidates = await store.find_correlation_candidates(
        namespace="cloudmart-prod",
        services=["order-service"],
        since=datetime.now(timezone.utc) - timedelta(minutes=15),
    )
    assert candidates == []


@pytest.mark.anyio
async def test_find_correlation_candidates_empty_services_returns_nothing(pool):
    store = PostgresIncidentStore(pool)
    await store.save(make_incident())

    candidates = await store.find_correlation_candidates(
        namespace="cloudmart-prod", services=[], since=datetime.now(timezone.utc)
    )
    assert candidates == []


# --- step 10: PostgresEvidenceStore -----------------------------------------


@pytest.mark.anyio
async def test_evidence_round_trip_with_raw_reference(pool):
    incident_store = PostgresIncidentStore(pool)
    obs_store = PostgresObservationStore(pool)
    evidence_store = PostgresEvidenceStore(pool)

    incident = make_incident()
    await incident_store.save(incident)
    observation = make_observation(correlation=Correlation(incident_id=incident.incident_id))
    await obs_store.save(observation)

    evidence = Evidence(
        evidence_id="ev-test0001",
        incident_id=incident.incident_id,
        type=EvidenceType.METRIC,
        source=ObservationSource.PROMETHEUS,
        timestamp=datetime.now(timezone.utc),
        service="order-service",
        resource="order-service-abc123",
        summary="pod_restarts for order-service: 3.0",
        observation_id=observation.observation_id,
        raw_reference=RawReference(query='kube_pod_container_status_restarts_total{...}'),
    )
    await evidence_store.save(evidence)

    fetched = await evidence_store.list_by_incident(incident.incident_id)
    assert len(fetched) == 1
    assert fetched[0].evidence_id == "ev-test0001"
    assert fetched[0].summary == "pod_restarts for order-service: 3.0"
    assert fetched[0].raw_reference.query == 'kube_pod_container_status_restarts_total{...}'
    assert fetched[0].observation_id == observation.observation_id


@pytest.mark.anyio
async def test_evidence_save_is_upsert(pool):
    incident_store = PostgresIncidentStore(pool)
    evidence_store = PostgresEvidenceStore(pool)
    incident = make_incident()
    await incident_store.save(incident)

    evidence = Evidence(
        evidence_id="ev-test0002",
        incident_id=incident.incident_id,
        type=EvidenceType.ALERT,
        source=ObservationSource.ALERTMANAGER,
        timestamp=datetime.now(timezone.utc),
        summary="original summary",
    )
    await evidence_store.save(evidence)
    await evidence_store.save(evidence.model_copy(update={"summary": "updated summary"}))

    fetched = await evidence_store.list_by_incident(incident.incident_id)
    assert len(fetched) == 1
    assert fetched[0].summary == "updated summary"


@pytest.fixture()
def anyio_backend():
    return "asyncio"
