import os
import random

import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")


async def init_db():
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS quotes (
            id SERIAL PRIMARY KEY,
            key TEXT UNIQUE NOT NULL,
            value TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT NOW()
        )
    """)
    await conn.close()


async def add_quote(key, value):
    conn = await asyncpg.connect(DATABASE_URL)
    await conn.execute(
        "INSERT INTO quotes(key, value) VALUES($1, $2) ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
        key, value
    )
    await conn.close()


async def remove_quote(key):
    conn = await asyncpg.connect(DATABASE_URL)
    result = await conn.execute("DELETE FROM quotes WHERE key = $1", key)
    await conn.close()
    return result == "DELETE 1"


async def get_all_keys():
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT key FROM quotes") 
    await conn.close()
    return [row["key"] for row in rows]


async def get_random_quote() -> dict | None:
    conn = await asyncpg.connect(DATABASE_URL)
    rows = await conn.fetch("SELECT key, value FROM quotes")
    await conn.close()
    if not rows:
        return None
    return dict(rows[random.randint(0, len(rows)-1)])


async def get_quote_by_key(key):
    conn = await asyncpg.connect(DATABASE_URL)
    row = await conn.fetchrow("SELECT value FROM quotes WHERE key = $1", key)
    await conn.close()
    return row["value"] if row else None