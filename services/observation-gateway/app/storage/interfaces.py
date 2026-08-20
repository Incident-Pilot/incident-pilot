"""
Storage interfaces for Observations and Incidents.

Defined now (step 7) so the Alertmanager webhook has somewhere durable-ish
to write without coupling to a concrete backend. `InMemoryObservationStore`
/ `InMemoryIncidentStore` (memory.py) satisfy these for local dev and tests;
step 8 adds Postgres-backed implementations of the same interfaces so
main.py can swap the in-memory store for the real one without touching the
webhook handler.
"""

from datetime import datetime
from typing import Dict, List, Optional, Protocol

from shared.models import Deployment, Evidence, Incident, Observation

TopologyGraph = Dict[str, List[str]]


class ObservationStore(Protocol):
    async def save(self, observation: Observation) -> None: ...

    async def list_all(self) -> List[Observation]: ...

    async def list_by_incident(self, incident_id: str) -> List[Observation]: ...


class IncidentStore(Protocol):
    async def save(self, incident: Incident) -> None: ...

    async def get(self, incident_id: str) -> Optional[Incident]: ...

    async def list_all(self) -> List[Incident]: ...

    async def find_correlation_candidates(
        self, namespace: Optional[str], services: List[str], since: datetime
    ) -> List[Incident]:
        """OPEN incidents matching spec section 7's deterministic dedup
        rule: same namespace, at least one overlapping service, updated
        within the correlation window (`since`). Used by
        app/correlation/incident_correlator.py — never returns anything
        if `services` is empty, since namespace alone isn't a safe match."""
        ...


class EvidenceStore(Protocol):
    async def save(self, evidence: Evidence) -> None: ...

    async def list_by_incident(self, incident_id: str) -> List[Evidence]: ...


class TopologyStore(Protocol):
    async def save_service(self, service: str, namespace: str, depends_on: List[str]) -> None: ...

    async def get_all(self) -> TopologyGraph:
        """service -> its list of dependencies, across every namespace
        stored so far. Used to serve GET /topology (spec section 10)."""
        ...


class DeploymentStore(Protocol):
    async def save(self, deployment: Deployment) -> None: ...

    async def get_latest(self, service: str) -> Optional[Deployment]:
        """Most recent Deployment record for a service, by `deployed_at`.
        Used by the Context Builder (step 12) to answer "was this service
        deployed recently" for an incident."""
        ...
