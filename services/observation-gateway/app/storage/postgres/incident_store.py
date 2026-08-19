"""PostgresIncidentStore — satisfies the IncidentStore Protocol
(app/storage/interfaces.py) against the real `incidents` table."""

from datetime import datetime
from typing import List, Optional

import asyncpg

from shared.models import Incident, IncidentPhase, IncidentStatus, Severity


def row_to_incident(row) -> Incident:
    return Incident(
        incident_id=row["incident_id"],
        title=row["title"],
        severity=Severity(row["severity"]),
        status=IncidentStatus(row["status"]),
        current_phase=IncidentPhase(row["current_phase"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        source=row["source"],
        affected_services=row["affected_services"] or [],
        affected_namespace=row["affected_namespace"],
        initial_alerts=row["initial_alerts"] or [],
        root_cause=row["root_cause"],
        root_cause_confidence=row["root_cause_confidence"],
    )


class PostgresIncidentStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save(self, incident: Incident) -> None:
        await self._pool.execute(
            """
            INSERT INTO incidents (
                incident_id, title, severity, status, current_phase,
                created_at, updated_at, source, affected_services,
                affected_namespace, initial_alerts, root_cause,
                root_cause_confidence
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13)
            ON CONFLICT (incident_id) DO UPDATE SET
                title = EXCLUDED.title,
                severity = EXCLUDED.severity,
                status = EXCLUDED.status,
                current_phase = EXCLUDED.current_phase,
                updated_at = EXCLUDED.updated_at,
                affected_services = EXCLUDED.affected_services,
                affected_namespace = EXCLUDED.affected_namespace,
                initial_alerts = EXCLUDED.initial_alerts,
                root_cause = EXCLUDED.root_cause,
                root_cause_confidence = EXCLUDED.root_cause_confidence
            """,
            incident.incident_id,
            incident.title,
            incident.severity.value,
            incident.status.value,
            incident.current_phase.value,
            incident.created_at,
            incident.updated_at,
            incident.source,
            incident.affected_services,
            incident.affected_namespace,
            incident.initial_alerts,
            incident.root_cause,
            incident.root_cause_confidence,
        )

    async def get(self, incident_id: str) -> Optional[Incident]:
        row = await self._pool.fetchrow(
            "SELECT * FROM incidents WHERE incident_id = $1", incident_id
        )
        return row_to_incident(row) if row else None

    async def list_all(self) -> List[Incident]:
        rows = await self._pool.fetch("SELECT * FROM incidents ORDER BY created_at DESC")
        return [row_to_incident(row) for row in rows]

    async def find_correlation_candidates(
        self, namespace: Optional[str], services: List[str], since: datetime
    ) -> List[Incident]:
        if not services:
            return []
        rows = await self._pool.fetch(
            """
            SELECT * FROM incidents
            WHERE status = 'open'
              AND affected_namespace IS NOT DISTINCT FROM $1
              AND updated_at >= $2
              AND affected_services ?| $3::text[]
            ORDER BY updated_at DESC
            """,
            namespace,
            since,
            services,
        )
        return [row_to_incident(row) for row in rows]
