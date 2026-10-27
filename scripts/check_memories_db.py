"""Manual check of the memories queries against DATABASE_URL (use a non-production DB).

Run: venv/Scripts/python.exe scripts/check_memories_db.py
"""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import asyncpg
from dotenv import load_dotenv

load_dotenv()

import db

GUILD = -1


async def main():
    await db.init_db()
    try:
        fact_id = await db.add_memory(
            GUILD, None, "ben", "fact", "going to japan", False, 1, None
        )
        assert (await db.get_memory(fact_id))["user_id"] is None

        await db.link_name(GUILD, "ben", 111, 1, None)
        assert (await db.get_memory(fact_id))["user_id"] == 111
        assert (await db.get_links(GUILD)) == {"ben": 111}
        assert fact_id in [m["id"] for m in await db.get_user_memories(GUILD, 111)]

        await db.unlink_name(GUILD, "ben")
        assert (await db.get_memory(fact_id))["user_id"] is None
        assert [m["id"] for m in await db.get_name_memories(GUILD, "ben")] == [fact_id]
        assert (await db.get_links(GUILD)) == {}

        await db.link_name(GUILD, "ben", 111, 1, None)
        try:
            await db.link_name(GUILD, "ben", 222, 1, None)
        except asyncpg.UniqueViolationError:
            pass
        else:
            raise AssertionError("duplicate name link was allowed")

        await db.update_memory(fact_id, "going to osaka", True)
        updated = await db.get_memory(fact_id)
        assert updated["content"] == "going to osaka" and updated["joke"] is True

        await db.delete_memory(fact_id)
        assert await db.get_memory(fact_id) is None
        print("ok")
    finally:
        async with db.pool.acquire() as conn:
            await conn.execute("DELETE FROM memories WHERE guild_id = $1", GUILD)


asyncio.run(main())
