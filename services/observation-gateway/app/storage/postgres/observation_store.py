"""PostgresObservationStore — satisfies the ObservationStore Protocol
(app/storage/interfaces.py) against the real `observations` table."""

from typing import List

import asyncpg

from shared.models import Correlation, Observation, ObservationSource, Severity, SignalType


def row_to_observation(row) -> Observation:
    return Observation(
        observation_id=row["observation_id"],
        timestamp=row["timestamp"],
        source=ObservationSource(row["source"]),
        signal_type=SignalType(row["signal_type"]),
        severity=Severity(row["severity"]),
        cluster=row["cluster"],
        namespace=row["namespace"],
        service=row["service"],
        resource=row["resource"],
        signal=row["signal"],
        value=row["value"],
        labels=row["labels"] or {},
        metadata=row["metadata"] or {},
        correlation=Correlation(
            trace_id=row["trace_id"],
            deployment_id=row["deployment_id"],
            incident_id=row["incident_id"],
        ),
    )


class PostgresObservationStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save(self, observation: Observation) -> None:
        await self._pool.execute(
            """
            INSERT INTO observations (
                observation_id, "timestamp", source, signal_type, severity,
                cluster, namespace, service, resource, signal, value,
                labels, metadata, trace_id, deployment_id, incident_id
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10,$11,$12,$13,$14,$15,$16)
            ON CONFLICT (observation_id) DO UPDATE SET
                "timestamp" = EXCLUDED."timestamp",
                severity = EXCLUDED.severity,
                namespace = EXCLUDED.namespace,
                service = EXCLUDED.service,
                resource = EXCLUDED.resource,
                value = EXCLUDED.value,
                labels = EXCLUDED.labels,
                metadata = EXCLUDED.metadata,
                trace_id = EXCLUDED.trace_id,
                deployment_id = EXCLUDED.deployment_id,
                incident_id = EXCLUDED.incident_id
            """,
            observation.observation_id,
            observation.timestamp,
            observation.source.value,
            observation.signal_type.value,
            observation.severity.value,
            observation.cluster,
            observation.namespace,
            observation.service,
            observation.resource,
            observation.signal,
            observation.value,
            observation.labels,
            observation.metadata,
            observation.correlation.trace_id,
            observation.correlation.deployment_id,
            observation.correlation.incident_id,
        )

    async def list_all(self) -> List[Observation]:
        rows = await self._pool.fetch('SELECT * FROM observations ORDER BY "timestamp" DESC')
        return [row_to_observation(row) for row in rows]

    async def list_by_incident(self, incident_id: str) -> List[Observation]:
        rows = await self._pool.fetch(
            'SELECT * FROM observations WHERE incident_id = $1 ORDER BY "timestamp"',
            incident_id,
        )
        return [row_to_observation(row) for row in rows]
