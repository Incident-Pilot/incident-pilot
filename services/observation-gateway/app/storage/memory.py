"""
In-memory ObservationStore / IncidentStore — dev/test backing only.

Not durable across restarts and not safe for multi-process deployment.
Exists purely so step 7 (webhook + normalization) has something to write
to ahead of step 8's real PostgreSQL persistence layer. Both classes
satisfy the Protocols in interfaces.py so main.py can swap this out for
the Postgres-backed stores later without changing any caller.
"""

from datetime import datetime
from typing import Dict, List, Optional, Tuple

from app.collectors.base import SourceCollectionStatus
from app.storage.interfaces import TopologyGraph
from shared.models import Deployment, Evidence, Incident, IncidentStatus, Observation


class InMemoryObservationStore:
    def __init__(self) -> None:
        self._by_id: Dict[str, Observation] = {}

    async def save(self, observation: Observation) -> None:
        self._by_id[observation.observation_id] = observation

    async def list_all(self) -> List[Observation]:
        return list(self._by_id.values())

    async def list_by_incident(self, incident_id: str) -> List[Observation]:
        return [
            obs
            for obs in self._by_id.values()
            if obs.correlation.incident_id == incident_id
        ]


class InMemoryIncidentStore:
    def __init__(self) -> None:
        self._by_id: Dict[str, Incident] = {}

    async def save(self, incident: Incident) -> None:
        self._by_id[incident.incident_id] = incident

    async def get(self, incident_id: str) -> Optional[Incident]:
        return self._by_id.get(incident_id)

    async def list_all(self) -> List[Incident]:
        return list(self._by_id.values())

    async def find_correlation_candidates(
        self,
        namespace: Optional[str],
        services: List[str],
        since: datetime,
        alertnames: Optional[List[str]] = None,
    ) -> List[Incident]:
        if services:
            service_set = set(services)
            return [
                incident
                for incident in self._by_id.values()
                if incident.status == IncidentStatus.OPEN
                and incident.affected_namespace == namespace
                and service_set.intersection(incident.affected_services)
                and incident.updated_at >= since
            ]

        # No derivable service (cluster-scoped alert) — fall back to
        # alertname among other service-less incidents, same rule as
        # PostgresIncidentStore.find_correlation_candidates.
        if not alertnames:
            return []
        alertname_set = set(alertnames)
        return [
            incident
            for incident in self._by_id.values()
            if incident.status == IncidentStatus.OPEN
            and incident.affected_namespace == namespace
            and not incident.affected_services
            and alertname_set.intersection(incident.initial_alerts)
            and incident.updated_at >= since
        ]


class InMemoryEvidenceStore:
    def __init__(self) -> None:
        self._by_id: Dict[str, Evidence] = {}

    async def save(self, evidence: Evidence) -> None:
        self._by_id[evidence.evidence_id] = evidence

    async def list_by_incident(self, incident_id: str) -> List[Evidence]:
        return [e for e in self._by_id.values() if e.incident_id == incident_id]


class InMemoryTopologyStore:
    def __init__(self) -> None:
        self._by_service: Dict[str, Tuple[str, List[str]]] = {}  # service -> (namespace, depends_on)

    async def save_service(self, service: str, namespace: str, depends_on: List[str]) -> None:
        self._by_service[service] = (namespace, depends_on)

    async def get_all(self) -> TopologyGraph:
        return {service: deps for service, (_, deps) in self._by_service.items()}


class InMemoryDeploymentStore:
    def __init__(self) -> None:
        self._by_id: Dict[str, Deployment] = {}

    async def save(self, deployment: Deployment) -> None:
        self._by_id[deployment.deployment_id] = deployment

    async def get_latest(self, service: str) -> Optional[Deployment]:
        matches = [d for d in self._by_id.values() if d.service == service]
        if not matches:
            return None
        return max(matches, key=lambda d: d.deployed_at)


class InMemorySourceStatusStore:
    def __init__(self) -> None:
        self._by_incident: Dict[str, List[SourceCollectionStatus]] = {}

    async def save_many(self, incident_id: str, statuses: List[SourceCollectionStatus]) -> None:
        self._by_incident[incident_id] = list(statuses)

    async def list_by_incident(self, incident_id: str) -> List[SourceCollectionStatus]:
        return list(self._by_incident.get(incident_id, []))
