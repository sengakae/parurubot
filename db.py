import os
import random
import asyncpg

DATABASE_URL = os.getenv("DATABASE_URL")
pool = None


async def init_db():
    global pool
    pool = await asyncpg.create_pool(DATABASE_URL)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id SERIAL PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)


async def add_quote(key, value):
    async with pool.acquire() as conn:
        await conn.execute(
            "INSERT INTO quotes(key, value) VALUES($1, $2) "
            "ON CONFLICT (key) DO UPDATE SET value = EXCLUDED.value",
            key, value
        )


async def remove_quote(key):
    async with pool.acquire() as conn:
        result = await conn.execute("DELETE FROM quotes WHERE key = $1", key)
        return result == "DELETE 1"


async def get_all_keys():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key FROM quotes")
        return [row["key"] for row in rows]


async def get_random_quote():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT key, value FROM quotes")
        if not rows:
            return None
        return random.choice(rows)["value"]


async def get_quote_by_key(key):
    async with pool.acquire() as conn:
        row = await conn.fetchrow("SELECT value FROM quotes WHERE key = $1", key)
        return row["value"] if row else None
