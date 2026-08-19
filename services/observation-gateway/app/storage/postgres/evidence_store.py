"""PostgresEvidenceStore — satisfies the EvidenceStore Protocol
(app/storage/interfaces.py) against the real `evidence` table."""

from typing import List

import asyncpg

from shared.models import Evidence, EvidenceType, ObservationSource, RawReference


def row_to_evidence(row) -> Evidence:
    return Evidence(
        evidence_id=row["evidence_id"],
        incident_id=row["incident_id"],
        type=EvidenceType(row["type"]),
        source=ObservationSource(row["source"]),
        timestamp=row["timestamp"],
        service=row["service"],
        resource=row["resource"],
        summary=row["summary"],
        observation_id=row["observation_id"],
        raw_reference=RawReference(**(row["raw_reference"] or {})),
    )


class PostgresEvidenceStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save(self, evidence: Evidence) -> None:
        await self._pool.execute(
            """
            INSERT INTO evidence (
                evidence_id, incident_id, type, source, "timestamp",
                service, resource, summary, observation_id, raw_reference
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10)
            ON CONFLICT (evidence_id) DO UPDATE SET
                summary = EXCLUDED.summary,
                raw_reference = EXCLUDED.raw_reference
            """,
            evidence.evidence_id,
            evidence.incident_id,
            evidence.type.value,
            evidence.source.value,
            evidence.timestamp,
            evidence.service,
            evidence.resource,
            evidence.summary,
            evidence.observation_id,
            evidence.raw_reference.model_dump(),
        )

    async def list_by_incident(self, incident_id: str) -> List[Evidence]:
        rows = await self._pool.fetch(
            'SELECT * FROM evidence WHERE incident_id = $1 ORDER BY "timestamp"',
            incident_id,
        )
        return [row_to_evidence(row) for row in rows]
