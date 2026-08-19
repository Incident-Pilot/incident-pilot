"""FastAPI dependencies that read shared state off `request.app.state`,
so route handlers stay decoupled from which store implementation is wired
up (in-memory today, Postgres from step 8 on)."""

from fastapi import Request

from app.storage.interfaces import IncidentStore, ObservationStore


def get_observation_store(request: Request) -> ObservationStore:
    return request.app.state.observation_store


def get_incident_store(request: Request) -> IncidentStore:
    return request.app.state.incident_store
