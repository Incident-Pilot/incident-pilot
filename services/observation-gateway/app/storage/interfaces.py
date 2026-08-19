"""
Storage interfaces for Observations and Incidents.

Defined now (step 7) so the Alertmanager webhook has somewhere durable-ish
to write without coupling to a concrete backend. `InMemoryObservationStore`
/ `InMemoryIncidentStore` (memory.py) satisfy these for local dev and tests;
step 8 adds Postgres-backed implementations of the same interfaces so
main.py can swap the in-memory store for the real one without touching the
webhook handler.
"""

from typing import List, Optional, Protocol

from shared.models import Incident, Observation


class ObservationStore(Protocol):
    async def save(self, observation: Observation) -> None: ...

    async def list_all(self) -> List[Observation]: ...

    async def list_by_incident(self, incident_id: str) -> List[Observation]: ...


class IncidentStore(Protocol):
    async def save(self, incident: Incident) -> None: ...

    async def get(self, incident_id: str) -> Optional[Incident]: ...

    async def list_all(self) -> List[Incident]: ...
