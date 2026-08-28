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

from app.collectors.base import SourceCollectionStatus, SourceStatus
from app.storage.postgres.deployment_store import PostgresDeploymentStore
from app.storage.postgres.evidence_store import PostgresEvidenceStore
from app.storage.postgres.incident_store import PostgresIncidentStore
from app.storage.postgres.observation_store import PostgresObservationStore
from app.storage.postgres.pool import create_pool, init_schema
from app.storage.postgres.source_status_store import PostgresSourceStatusStore
from app.storage.postgres.topology_store import PostgresTopologyStore
from shared.models import (
    Correlation,
    Deployment,
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
            "TRUNCATE evidence, deployments, service_topology, incident_source_status, "
            "observations, incidents"
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
async def test_find_correlation_candidates_empty_services_and_no_alertnames_returns_nothing(pool):
    store = PostgresIncidentStore(pool)
    await store.save(make_incident())

    candidates = await store.find_correlation_candidates(
        namespace="cloudmart-prod", services=[], since=datetime.now(timezone.utc)
    )
    assert candidates == []


@pytest.mark.anyio
async def test_find_correlation_candidates_empty_services_falls_back_to_alertname(pool):
    store = PostgresIncidentStore(pool)
    cluster_incident = make_incident(
        incident_id="INC-CLUSTER1",
        affected_services=[],
        affected_namespace=None,
        initial_alerts=["KubeControllerManagerDown"],
    )
    await store.save(cluster_incident)

    candidates = await store.find_correlation_candidates(
        namespace=None,
        services=[],
        since=datetime.now(timezone.utc) - timedelta(minutes=15),
        alertnames=["KubeControllerManagerDown"],
    )
    assert [c.incident_id for c in candidates] == [cluster_incident.incident_id]


@pytest.mark.anyio
async def test_find_correlation_candidates_alertname_fallback_ignores_incidents_with_services(pool):
    store = PostgresIncidentStore(pool)
    normal_incident = make_incident()  # affected_services=["order-service"]
    await store.save(normal_incident)

    candidates = await store.find_correlation_candidates(
        namespace="cloudmart-prod",
        services=[],
        since=datetime.now(timezone.utc) - timedelta(minutes=15),
        alertnames=["HighHTTPErrorRate"],
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


# --- step 11: PostgresTopologyStore ------------------------------------------


@pytest.mark.anyio
async def test_topology_save_and_get_all(pool):
    store = PostgresTopologyStore(pool)
    await store.save_service("frontend", "cloudmart-prod", ["product-service", "order-service"])
    await store.save_service("product-service", "cloudmart-prod", [])

    graph = await store.get_all()
    assert graph["frontend"] == ["product-service", "order-service"]
    assert graph["product-service"] == []


@pytest.mark.anyio
async def test_topology_save_service_is_upsert(pool):
    store = PostgresTopologyStore(pool)
    await store.save_service("frontend", "cloudmart-prod", ["product-service"])
    await store.save_service("frontend", "cloudmart-prod", ["product-service", "order-service"])

    graph = await store.get_all()
    assert graph["frontend"] == ["product-service", "order-service"]
    assert len(graph) == 1


# --- step 12: PostgresDeploymentStore ---------------------------------------


def make_deployment(**overrides) -> Deployment:
    defaults = dict(
        deployment_id="dep-order-service-abc1234",
        service="order-service",
        namespace="cloudmart-prod",
        commit_sha="abc1234",
        branch="main",
        image_tag="localhost:5000/cloudmart/order-service:v1",
        rollout_revision="7",
        deployed_at=datetime.now(timezone.utc),
        success=True,
    )
    defaults.update(overrides)
    return Deployment(**defaults)


@pytest.mark.anyio
async def test_deployment_round_trip_including_branch_column(pool):
    store = PostgresDeploymentStore(pool)
    deployment = make_deployment()
    await store.save(deployment)

    fetched = await store.get_latest("order-service")
    assert fetched is not None
    assert fetched.commit_sha == "abc1234"
    assert fetched.branch == "main"
    assert fetched.rollout_revision == "7"
    assert fetched.success is True


@pytest.mark.anyio
async def test_deployment_get_latest_returns_most_recent(pool):
    store = PostgresDeploymentStore(pool)
    older = make_deployment(
        deployment_id="dep-order-service-older",
        commit_sha="older111",
        deployed_at=datetime.now(timezone.utc) - timedelta(hours=1),
    )
    newer = make_deployment(
        deployment_id="dep-order-service-newer",
        commit_sha="newer222",
        deployed_at=datetime.now(timezone.utc),
    )
    await store.save(older)
    await store.save(newer)

    latest = await store.get_latest("order-service")
    assert latest.commit_sha == "newer222"


@pytest.mark.anyio
async def test_deployment_get_latest_missing_service_returns_none(pool):
    store = PostgresDeploymentStore(pool)
    assert await store.get_latest("nonexistent-service") is None


@pytest.mark.anyio
async def test_deployment_save_is_upsert(pool):
    store = PostgresDeploymentStore(pool)
    deployment = make_deployment()
    await store.save(deployment)
    await store.save(deployment.model_copy(update={"success": False}))

    fetched = await store.get_latest("order-service")
    assert fetched.success is False


# --- source_status: incident_source_status (spec section 13/37) -----------


@pytest.mark.anyio
async def test_source_status_round_trip(pool):
    incident_store = PostgresIncidentStore(pool)
    status_store = PostgresSourceStatusStore(pool)
    incident = make_incident()
    await incident_store.save(incident)

    await status_store.save_many(
        incident.incident_id,
        [
            SourceCollectionStatus(source="prometheus", status=SourceStatus.AVAILABLE, observation_count=5),
            SourceCollectionStatus(
                source="kubernetes",
                status=SourceStatus.UNAVAILABLE,
                error="RBAC forbidden",
                observation_count=0,
            ),
        ],
    )

    fetched = await status_store.list_by_incident(incident.incident_id)
    by_source = {s.source: s for s in fetched}
    assert by_source["prometheus"].status == SourceStatus.AVAILABLE
    assert by_source["prometheus"].observation_count == 5
    assert by_source["kubernetes"].status == SourceStatus.UNAVAILABLE
    assert by_source["kubernetes"].error == "RBAC forbidden"


@pytest.mark.anyio
async def test_source_status_save_many_replaces_previous_run(pool):
    incident_store = PostgresIncidentStore(pool)
    status_store = PostgresSourceStatusStore(pool)
    incident = make_incident()
    await incident_store.save(incident)

    await status_store.save_many(
        incident.incident_id,
        [SourceCollectionStatus(source="tempo", status=SourceStatus.TIMEOUT, error="slow")],
    )
    await status_store.save_many(
        incident.incident_id,
        [SourceCollectionStatus(source="tempo", status=SourceStatus.AVAILABLE, observation_count=3)],
    )

    fetched = await status_store.list_by_incident(incident.incident_id)
    assert len(fetched) == 1
    assert fetched[0].status == SourceStatus.AVAILABLE
    assert fetched[0].observation_count == 3


@pytest.fixture()
def anyio_backend():
    return "asyncio"
