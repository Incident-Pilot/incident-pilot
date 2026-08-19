"""
asyncpg pool creation + schema init.

Registers a jsonb codec on every connection so Python dict/list values pass
straight through to JSONB columns and come back as dict/list — the store
implementations never touch json.dumps/loads directly.
"""

import json
from pathlib import Path

import asyncpg

_SCHEMA_PATH = Path(__file__).parent / "schema.sql"


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.set_type_codec(
        "jsonb",
        encoder=json.dumps,
        decoder=json.loads,
        schema="pg_catalog",
    )


async def create_pool(dsn: str, *, min_size: int = 1, max_size: int = 5) -> asyncpg.Pool:
    return await asyncpg.create_pool(
        dsn=dsn, min_size=min_size, max_size=max_size, init=_init_connection
    )


async def init_schema(pool: asyncpg.Pool) -> None:
    sql = _SCHEMA_PATH.read_text()
    async with pool.acquire() as conn:
        await conn.execute(sql)
