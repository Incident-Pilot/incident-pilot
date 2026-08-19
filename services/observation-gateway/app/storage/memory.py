"""
In-memory ObservationStore / IncidentStore — dev/test backing only.

Not durable across restarts and not safe for multi-process deployment.
Exists purely so step 7 (webhook + normalization) has something to write
to ahead of step 8's real PostgreSQL persistence layer. Both classes
satisfy the Protocols in interfaces.py so main.py can swap this out for
the Postgres-backed stores later without changing any caller.
"""

from typing import Dict, List, Optional

from shared.models import Incident, Observation


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
