"""FastAPI dependencies that read shared state off `request.app.state`,
so route handlers stay decoupled from which store implementation is wired
up (in-memory today, Postgres from step 8 on)."""

from fastapi import Request

from app.context.incident_context_builder import IncidentContextBuilder
from app.storage.interfaces import (
    DeploymentStore,
    EvidenceStore,
    IncidentStore,
    ObservationStore,
    TopologyStore,
)
from app.topology.service_topology_builder import ServiceTopologyBuilder


def get_observation_store(request: Request) -> ObservationStore:
    return request.app.state.observation_store


def get_incident_store(request: Request) -> IncidentStore:
    return request.app.state.incident_store


def get_evidence_store(request: Request) -> EvidenceStore:
    return request.app.state.evidence_store


def get_topology_store(request: Request) -> TopologyStore:
    return request.app.state.topology_store


def get_deployment_store(request: Request) -> DeploymentStore:
    return request.app.state.deployment_store


def get_context_builder(request: Request) -> IncidentContextBuilder:
    # Built fresh per request (not cached on app.state) so it always picks
    # up whichever stores are current on app.state — important because the
    # Postgres lifespan swap (step 8) happens after create_app() runs, and
    # a context builder built too early would keep pointing at the
    # in-memory stores it was constructed with.
    state = request.app.state
    return IncidentContextBuilder(
        prometheus=state.prometheus_client,
        loki=state.loki_client,
        tempo=state.tempo_client,
        kubernetes=state.kubernetes_client,
        observation_store=state.observation_store,
        evidence_store=state.evidence_store,
        incident_store=state.incident_store,
        deployment_store=state.deployment_store,
    )


def get_topology_builder(request: Request) -> ServiceTopologyBuilder:
    state = request.app.state
    return ServiceTopologyBuilder(
        kubernetes=state.kubernetes_client,
        tempo=state.tempo_client,
        topology_store=state.topology_store,
    )
