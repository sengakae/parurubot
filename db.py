import json
import os
import random

import asyncpg

pool = None


async def init_db():
    global pool
    db_url = os.getenv("DATABASE_URL")
    if not db_url:
        raise RuntimeError("DATABASE_URL is not set. Did you load .env?")
    
    pool = await asyncpg.create_pool(dsn=db_url)
    async with pool.acquire() as conn:
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS quotes (
                id SERIAL PRIMARY KEY,
                key TEXT UNIQUE NOT NULL,
                value TEXT NOT NULL,
                created_at TIMESTAMP DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS reminders (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                channel_id BIGINT NOT NULL,
                message TEXT NOT NULL,
                remind_at TIMESTAMPTZ NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        """)
        await conn.execute("""
            CREATE TABLE IF NOT EXISTS tasklists (
                message_id BIGINT PRIMARY KEY,
                channel_id BIGINT NOT NULL,
                author_id BIGINT NOT NULL,
                author_name TEXT NOT NULL,
                tasks JSONB NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
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


async def add_reminder(user_id, channel_id, message, remind_at):
    async with pool.acquire() as conn:
        return await conn.fetchval(
            """
            INSERT INTO reminders (user_id, channel_id, message, remind_at)
            VALUES ($1, $2, $3, $4)
            RETURNING id
            """,
            user_id,
            channel_id,
            message,
            remind_at,
        )


async def delete_reminder(reminder_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM reminders WHERE id = $1", reminder_id)


async def get_all_reminders():
    async with pool.acquire() as conn:
        return await conn.fetch("SELECT * FROM reminders ORDER BY remind_at")


def _parse_tasklist_tasks(raw):
    if isinstance(raw, str):
        return json.loads(raw)
    return list(raw)


async def save_tasklist(message_id, channel_id, author_id, author_name, tasks):
    async with pool.acquire() as conn:
        await conn.execute(
            """
            INSERT INTO tasklists (message_id, channel_id, author_id, author_name, tasks)
            VALUES ($1, $2, $3, $4, $5::jsonb)
            ON CONFLICT (message_id) DO UPDATE SET
                channel_id = EXCLUDED.channel_id,
                author_id = EXCLUDED.author_id,
                author_name = EXCLUDED.author_name,
                tasks = EXCLUDED.tasks
            """,
            message_id,
            channel_id,
            author_id,
            author_name,
            json.dumps(tasks),
        )


async def get_tasklist(message_id):
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM tasklists WHERE message_id = $1", message_id
        )
        if row is None:
            return None
        return {
            "message_id": row["message_id"],
            "channel_id": row["channel_id"],
            "author_id": row["author_id"],
            "author_name": row["author_name"],
            "tasks": _parse_tasklist_tasks(row["tasks"]),
        }


async def get_all_tasklists():
    async with pool.acquire() as conn:
        rows = await conn.fetch("SELECT * FROM tasklists ORDER BY created_at")
        return [
            {
                "message_id": row["message_id"],
                "channel_id": row["channel_id"],
                "author_id": row["author_id"],
                "author_name": row["author_name"],
                "tasks": _parse_tasklist_tasks(row["tasks"]),
            }
            for row in rows
        ]


async def delete_tasklist(message_id):
    async with pool.acquire() as conn:
        await conn.execute("DELETE FROM tasklists WHERE message_id = $1", message_id)
