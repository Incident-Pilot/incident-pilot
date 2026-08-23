"""PostgresSourceStatusStore — satisfies the SourceStatusStore Protocol
(app/storage/interfaces.py) against the real `incident_source_status`
table."""

from typing import List

import asyncpg

from app.collectors.base import SourceCollectionStatus, SourceStatus


def row_to_status(row) -> SourceCollectionStatus:
    return SourceCollectionStatus(
        source=row["source"],
        status=SourceStatus(row["status"]),
        error=row["error"],
        observation_count=row["observation_count"],
    )


class PostgresSourceStatusStore:
    def __init__(self, pool: asyncpg.Pool):
        self._pool = pool

    async def save_many(self, incident_id: str, statuses: List[SourceCollectionStatus]) -> None:
        async with self._pool.acquire() as conn:
            async with conn.transaction():
                await conn.execute(
                    "DELETE FROM incident_source_status WHERE incident_id = $1", incident_id
                )
                await conn.executemany(
                    """
                    INSERT INTO incident_source_status (
                        incident_id, source, status, error, observation_count
                    ) VALUES ($1,$2,$3,$4,$5)
                    """,
                    [
                        (incident_id, s.source, s.status.value, s.error, s.observation_count)
                        for s in statuses
                    ],
                )

    async def list_by_incident(self, incident_id: str) -> List[SourceCollectionStatus]:
        rows = await self._pool.fetch(
            "SELECT * FROM incident_source_status WHERE incident_id = $1 ORDER BY source",
            incident_id,
        )
        return [row_to_status(row) for row in rows]
