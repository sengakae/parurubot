"""In-memory stand-in for the memories functions in db.py (same names and signatures)."""

import itertools


class UniqueViolation(Exception):
    pass


class FakeStore:
    def __init__(self):
        self.rows: dict[int, dict] = {}
        self._ids = itertools.count(1)

    async def add_memory(
        self, guild_id, user_id, name, kind, content, joke, added_by, source_message_id
    ):
        if kind == "name" and any(
            r["guild_id"] == guild_id and r["kind"] == "name" and r["content"] == content
            for r in self.rows.values()
        ):
            raise UniqueViolation(content)
        memory_id = next(self._ids)
        self.rows[memory_id] = {
            "id": memory_id,
            "guild_id": guild_id,
            "user_id": user_id,
            "name": name,
            "kind": kind,
            "content": content,
            "joke": joke,
            "added_by": added_by,
            "source_message_id": source_message_id,
        }
        return memory_id

    async def update_memory(self, memory_id, content, joke):
        self.rows[memory_id].update(content=content, joke=joke)

    async def delete_memory(self, memory_id):
        self.rows.pop(memory_id, None)

    async def get_memory(self, memory_id):
        row = self.rows.get(memory_id)
        return dict(row) if row else None

    async def get_user_memories(self, guild_id, user_id):
        return [
            dict(r)
            for r in self.rows.values()
            if r["guild_id"] == guild_id and r["user_id"] == user_id
        ]

    async def get_name_memories(self, guild_id, name):
        return [
            dict(r)
            for r in self.rows.values()
            if r["guild_id"] == guild_id and r["name"] == name and r["user_id"] is None
        ]

    async def get_links(self, guild_id):
        return {
            r["content"]: r["user_id"]
            for r in self.rows.values()
            if r["guild_id"] == guild_id and r["kind"] == "name"
        }

    async def link_name(self, guild_id, name, user_id, added_by, source_message_id):
        memory_id = await self.add_memory(
            guild_id, user_id, name, "name", name, False, added_by, source_message_id
        )
        for r in self.rows.values():
            if r["guild_id"] == guild_id and r["name"] == name and r["user_id"] is None:
                r["user_id"] = user_id
        return memory_id

    async def unlink_name(self, guild_id, name):
        link = next(
            (
                r
                for r in self.rows.values()
                if r["guild_id"] == guild_id and r["kind"] == "name" and r["content"] == name
            ),
            None,
        )
        if link is None:
            return
        del self.rows[link["id"]]
        for r in self.rows.values():
            if (
                r["guild_id"] == guild_id
                and r["name"] == name
                and r["kind"] == "fact"
                and r["user_id"] == link["user_id"]
            ):
                r["user_id"] = None

    # test helpers
    def facts(self):
        return [r for r in self.rows.values() if r["kind"] == "fact"]
