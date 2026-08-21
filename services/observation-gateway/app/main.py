"""
Observation Gateway FastAPI app — spec section 12.

Step 7 wired up the app itself, /health, /ready, and the webhook route.
Step 8 adds real persistence: if `settings.postgres_dsn` is set, the
lifespan handler below opens an asyncpg pool, applies the schema, and
swaps `app.state.*_store` over to the Postgres-backed implementations:
otherwise the in-memory stores from step 7 keep working unchanged (this
is what tests use, since they never set POSTGRES_DSN). Step 10 adds the
four telemetry adapter clients (Prometheus/Loki/Tempo/Kubernetes) to
`app.state` for the Context Builder — see app/api/deps.py for how a
request wires them into an IncidentContextBuilder. The Kubernetes client
is optional: if no kubeconfig is reachable (e.g. running outside a
cluster with no local kubeconfig), it's left as None and the Context
Builder reports that source UNAVAILABLE rather than crashing the app.
Step 11 adds GET /topology (and /services), backed by a TopologyStore the
same way. Step 13 adds POST /ingest/gitleaks and /ingest/trivy. Step 14
adds GET /incidents and its sub-resources, plus a shared bearer-token
`require_api_key` dependency (app/api/auth.py) applied to every router
below except /health and /ready.
"""

from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.api.incidents import router as incidents_router
from app.api.security_ingestion import router as security_ingestion_router
from app.api.topology import router as topology_router
from app.api.webhooks import router as webhooks_router
from app.collectors.kubernetes_adapter import KubernetesClient
from app.collectors.loki_adapter import LokiClient
from app.collectors.prometheus_adapter import PrometheusClient
from app.collectors.tempo_adapter import TempoClient
from app.config.settings import settings
from app.storage.memory import (
    InMemoryDeploymentStore,
    InMemoryEvidenceStore,
    InMemoryIncidentStore,
    InMemoryObservationStore,
    InMemorySourceStatusStore,
    InMemoryTopologyStore,
)
from app.storage.postgres.deployment_store import PostgresDeploymentStore
from app.storage.postgres.evidence_store import PostgresEvidenceStore
from app.storage.postgres.incident_store import PostgresIncidentStore
from app.storage.postgres.observation_store import PostgresObservationStore
from app.storage.postgres.pool import create_pool, init_schema
from app.storage.postgres.source_status_store import PostgresSourceStatusStore
from app.storage.postgres.topology_store import PostgresTopologyStore


@asynccontextmanager
async def _lifespan(app: FastAPI):
    if settings.postgres_dsn:
        pool = await create_pool(settings.postgres_dsn)
        await init_schema(pool)
        app.state.pg_pool = pool
        app.state.observation_store = PostgresObservationStore(pool)
        app.state.incident_store = PostgresIncidentStore(pool)
        app.state.evidence_store = PostgresEvidenceStore(pool)
        app.state.topology_store = PostgresTopologyStore(pool)
        app.state.deployment_store = PostgresDeploymentStore(pool)
        app.state.source_status_store = PostgresSourceStatusStore(pool)
    yield
    pool = getattr(app.state, "pg_pool", None)
    if pool is not None:
        await pool.close()


def create_app() -> FastAPI:
    app = FastAPI(title="IncidentPilot Observation Gateway", lifespan=_lifespan)

    # Default to in-memory so the app is usable (and every existing test
    # keeps passing) before/without the lifespan's Postgres swap-in above.
    app.state.observation_store = InMemoryObservationStore()
    app.state.incident_store = InMemoryIncidentStore()
    app.state.evidence_store = InMemoryEvidenceStore()
    app.state.topology_store = InMemoryTopologyStore()
    app.state.deployment_store = InMemoryDeploymentStore()
    app.state.source_status_store = InMemorySourceStatusStore()

    app.state.prometheus_client = PrometheusClient(
        settings.prometheus_base_url, timeout_seconds=settings.http_timeout_seconds
    )
    app.state.loki_client = LokiClient(
        settings.loki_base_url, timeout_seconds=settings.http_timeout_seconds
    )
    app.state.tempo_client = TempoClient(
        settings.tempo_base_url, timeout_seconds=settings.http_timeout_seconds
    )
    try:
        app.state.kubernetes_client = KubernetesClient(
            timeout_seconds=settings.http_timeout_seconds
        )
    except Exception:
        # No reachable kubeconfig (e.g. local dev with no cluster at all) —
        # the Context Builder treats a None client as source UNAVAILABLE.
        app.state.kubernetes_client = None

    @app.get("/health")
    async def health():
        return {"status": "ok"}

    @app.get("/ready")
    async def ready():
        return {"status": "ready"}

    app.include_router(webhooks_router)
    app.include_router(topology_router)
    app.include_router(security_ingestion_router)
    app.include_router(incidents_router)

    return app


app = create_app()
