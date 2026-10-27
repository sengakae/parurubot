import asyncio
from datetime import datetime, timezone

import pytest

from config import PARTNER_USER_ID
from tests.fake_store import FakeStore
from utils.memory_queue import QueuedMessage
from utils.memory_rules import (ApplyContext, Op, admin_link, apply_ops,
                                normalize_name, recall, remove_for_owner)

G = 1
ANTO, BEN, CHEUNG = 100, 200, 300


def run(coro):
    return asyncio.run(coro)


def qmsg(message_id, author_id=ANTO, content="hi", mention_ids=(), reply_to=None):
    return QueuedMessage(
        id=message_id,
        channel_id=10,
        guild_id=G,
        author_id=author_id,
        author_label=str(author_id),
        content=content,
        mention_ids=list(mention_ids),
        reply_to_author_id=reply_to,
        created_at=datetime.now(timezone.utc),
    )


def context(*messages, members=None, pending=lambda _id: True):
    return ApplyContext(
        guild_id=G,
        messages={m.id: m for m in messages},
        member_names=members if members is not None else {"ben": BEN, "anto": ANTO},
        is_pending=pending,
    )


def test_normalize_name():
    assert normalize_name(" Ben ") == "ben"
    assert normalize_name("@ben") == "ben"
    assert normalize_name("BEN") == "ben"
    assert normalize_name("<@123>") is None
    assert normalize_name("") is None
    assert normalize_name(None) is None
    assert normalize_name("x" * 33) is None


def test_self_add():
    store = FakeStore()
    m = qmsg(1, content="i'm moving to osaka")
    run(apply_ops([Op("add", 1, subject="self", content="moving to osaka")], context(m), store))
    [row] = store.facts()
    assert row["user_id"] == ANTO and row["added_by"] == ANTO and row["name"] is None


def test_name_add_unlinked_member():
    store = FakeStore()
    m = qmsg(1, content="ben is going to japan")
    run(apply_ops([Op("add", 1, subject="name", name="Ben", content="going to japan")], context(m), store))
    [row] = store.facts()
    assert row["user_id"] is None and row["name"] == "ben" and row["added_by"] == ANTO


def test_name_add_unknown_dropped():
    store = FakeStore()
    m = qmsg(1, content="elon is buying another company")
    reacts = run(apply_ops([Op("add", 1, subject="name", name="elon", content="buying a company")], context(m), store))
    assert store.facts() == [] and reacts == []


def test_name_add_addressed():
    store = FakeStore()
    m = qmsg(1, content="jules you coming tonight? jules got a new job")
    run(apply_ops([Op("add", 1, subject="name", name="jules", content="got a new job", addressed=True)], context(m), store))
    assert len(store.facts()) == 1


def test_name_add_addressed_requires_name_in_text():
    store = FakeStore()
    m = qmsg(1, content="someone got a new job")
    run(apply_ops([Op("add", 1, subject="name", name="jules", content="got a new job", addressed=True)], context(m), store))
    assert store.facts() == []


def test_name_add_linked_goes_to_user():
    store = FakeStore()
    run(store.link_name(G, "ben", BEN, BEN, None))
    m = qmsg(1, content="ben is going to japan")
    run(apply_ops([Op("add", 1, subject="name", name="ben", content="going to japan")], context(m), store))
    [row] = store.facts()
    assert row["user_id"] == BEN and row["name"] == "ben"


def test_locked_name_add():
    store = FakeStore()
    m = qmsg(1, content="sengakae is cooking tonight")
    run(apply_ops([Op("add", 1, subject="name", name="sengakae", content="cooking tonight")], context(m, members={}), store))
    [row] = store.facts()
    assert row["user_id"] == PARTNER_USER_ID


def test_link_merges():
    store = FakeStore()
    m1 = qmsg(1, content="ben is going to japan")
    m2 = qmsg(2, content="ben hates cilantro")
    m3 = qmsg(3, author_id=CHEUNG, content="that's ben", reply_to=BEN)
    ops = [
        Op("add", 1, subject="name", name="ben", content="going to japan"),
        Op("add", 2, subject="name", name="ben", content="hates cilantro"),
        Op("link", 3, name="ben", user_id=BEN),
    ]
    run(apply_ops(ops, context(m1, m2, m3), store))
    assert {r["user_id"] for r in store.facts()} == {BEN}
    assert run(store.get_links(G)) == {"ben": BEN}


def test_link_conflict_rejected():
    store = FakeStore()
    run(store.link_name(G, "ben", BEN, BEN, None))
    m = qmsg(1, author_id=CHEUNG, content="i'm ben")
    reacts = run(apply_ops([Op("link", 1, name="ben", user_id=CHEUNG)], context(m), store))
    assert run(store.get_links(G)) == {"ben": BEN} and reacts == []


def test_link_same_user_noop():
    store = FakeStore()
    run(store.link_name(G, "ben", BEN, BEN, None))
    m = qmsg(1, author_id=BEN, content="i'm ben")
    reacts = run(apply_ops([Op("link", 1, name="ben", user_id=BEN)], context(m), store))
    assert reacts == []


def test_link_locked_rejected():
    store = FakeStore()
    m = qmsg(1, author_id=CHEUNG, content="i'm sengakae")
    run(apply_ops([Op("link", 1, name="sengakae", user_id=CHEUNG)], context(m), store))
    assert run(store.get_links(G)) == {}


def test_link_user_must_be_in_batch():
    store = FakeStore()
    m = qmsg(1, content="that's ben")
    run(apply_ops([Op("link", 1, name="ben", user_id=999)], context(m), store))
    assert run(store.get_links(G)) == {}


def test_deleted_message_ops_dropped():
    store = FakeStore()
    existing = run(store.add_memory(G, BEN, "ben", "fact", "old", False, CHEUNG, None))
    m = qmsg(1, content="ben is going to japan", mention_ids=[BEN])
    ops = [
        Op("add", 1, subject="name", name="ben", content="going to japan"),
        Op("update", 1, memory_id=existing, content="new", joke=False),
        Op("forget", 1, memory_id=existing),
        Op("link", 1, name="ben", user_id=BEN),
    ]
    reacts = run(apply_ops(ops, context(m, pending=lambda _id: False), store))
    assert reacts == []
    assert store.rows[existing]["content"] == "old"
    assert len(store.rows) == 1


def test_owner_protected():
    store = FakeStore()
    own = run(store.add_memory(G, BEN, None, "fact", "plays sekiro", False, BEN, None))
    m = qmsg(1, author_id=CHEUNG, content="ben doesn't play sekiro anymore")
    run(apply_ops([Op("forget", 1, memory_id=own)], context(m), store))
    assert own in store.rows


def test_owner_can_change_own():
    store = FakeStore()
    own = run(store.add_memory(G, BEN, None, "fact", "plays sekiro", False, BEN, None))
    m = qmsg(1, author_id=BEN, content="i stopped playing sekiro")
    run(apply_ops([Op("forget", 1, memory_id=own)], context(m), store))
    assert own not in store.rows


def test_foreign_memory_id_rejected():
    store = FakeStore()
    other = run(store.add_memory(2, BEN, None, "fact", "other guild", False, CHEUNG, None))
    m = qmsg(1)
    run(apply_ops([Op("update", 1, memory_id=other, content="hacked", joke=False)], context(m), store))
    assert store.rows[other]["content"] == "other guild"


def test_update_name_row_rejected():
    store = FakeStore()
    run(store.link_name(G, "ben", BEN, CHEUNG, None))
    link_id = next(r["id"] for r in store.rows.values() if r["kind"] == "name")
    m = qmsg(1)
    run(apply_ops([Op("update", 1, memory_id=link_id, content="benny", joke=False)], context(m), store))
    assert store.rows[link_id]["content"] == "ben"


def test_forget_name_row_unlinks():
    store = FakeStore()
    run(store.add_memory(G, None, "ben", "fact", "going to japan", False, CHEUNG, None))
    run(store.link_name(G, "ben", BEN, CHEUNG, None))
    link_id = next(r["id"] for r in store.rows.values() if r["kind"] == "name")
    m = qmsg(1, author_id=CHEUNG, content="wait ben isn't him")
    run(apply_ops([Op("forget", 1, memory_id=link_id)], context(m), store))
    assert run(store.get_links(G)) == {}
    assert store.facts()[0]["user_id"] is None


def test_react_ids():
    store = FakeStore()
    existing = run(store.add_memory(G, BEN, "ben", "fact", "old", False, CHEUNG, None))
    gone = run(store.add_memory(G, BEN, "ben", "fact", "gone", False, CHEUNG, None))
    msgs = [qmsg(1, content="i like tea"), qmsg(2), qmsg(3, mention_ids=[BEN]), qmsg(4)]
    ops = [
        Op("add", 1, subject="self", content="likes tea"),
        Op("update", 2, memory_id=existing, content="new", joke=False),
        Op("link", 3, name="ben", user_id=BEN),
        Op("forget", 4, memory_id=gone),
    ]
    reacts = run(apply_ops(ops, context(*msgs), store))
    assert reacts == [1, 2, 3]


def test_admin_link_relinks():
    store = FakeStore()
    run(store.add_memory(G, None, "ben", "fact", "going to japan", False, CHEUNG, None))
    run(store.link_name(G, "ben", ANTO, CHEUNG, None))
    assert run(admin_link(G, "ben", BEN, 1, store)) == "relinked"
    assert run(store.get_links(G)) == {"ben": BEN}
    assert store.facts()[0]["user_id"] == BEN
    assert run(admin_link(G, "ben", BEN, 1, store)) == "unchanged"
    assert run(admin_link(G, "jules", CHEUNG, 1, store)) == "linked"


def test_admin_link_locked_rejected():
    store = FakeStore()
    with pytest.raises(ValueError):
        run(admin_link(G, "sengakae", BEN, 1, store))
    with pytest.raises(ValueError):
        run(admin_link(G, "<@1>", BEN, 1, store))


def test_remove_for_owner():
    store = FakeStore()
    fact = run(store.add_memory(G, BEN, None, "fact", "plays sekiro", False, BEN, None))
    assert run(remove_for_owner(G, fact, CHEUNG, store)) is False
    assert run(remove_for_owner(G, fact, BEN, store)) is True
    assert fact not in store.rows

    run(store.add_memory(G, None, "ben", "fact", "going to japan", False, CHEUNG, None))
    run(store.link_name(G, "ben", BEN, CHEUNG, None))
    link_id = next(r["id"] for r in store.rows.values() if r["kind"] == "name")
    assert run(remove_for_owner(G, link_id, BEN, store)) is True
    assert run(store.get_links(G)) == {}
    assert store.facts()[0]["user_id"] is None

    run(store.link_name(G, "sengakae", PARTNER_USER_ID, PARTNER_USER_ID, None))
    locked_id = next(r["id"] for r in store.rows.values() if r["content"] == "sengakae")
    assert run(remove_for_owner(G, locked_id, PARTNER_USER_ID, store)) is False


def test_recall():
    store = FakeStore()
    run(store.add_memory(G, ANTO, None, "fact", "likes tea", False, ANTO, None))
    run(store.add_memory(G, None, "jules", "fact", "new job", False, ANTO, None))
    run(store.link_name(G, "ben", BEN, CHEUNG, None))
    run(store.add_memory(G, BEN, "ben", "fact", "going to japan", True, CHEUNG, None))
    assert [m["content"] for m in run(recall(G, "me", ANTO, store))] == ["likes tea"]
    assert [m["content"] for m in run(recall(G, None, ANTO, store))] == ["likes tea"]
    assert "going to japan" in [m["content"] for m in run(recall(G, "Ben", ANTO, store))]
    assert [m["content"] for m in run(recall(G, "jules", ANTO, store))] == ["new job"]


def mention_msg(message_id, content, names):
    m = qmsg(message_id, content=content, mention_ids=list(names.values()))
    m.mention_names = names
    return m


def test_mention_add_links_and_files_under_user():
    store = FakeStore()
    run(store.add_memory(G, None, "benjamin", "fact", "hates cilantro", False, CHEUNG, None))
    m = mention_msg(1, "@Benjamin is going to japan", {"benjamin": BEN})
    reacts = run(apply_ops([Op("add", 1, subject="name", name="@Benjamin", content="going to japan")], context(m, members={}), store))
    assert run(store.get_links(G)) == {"benjamin": BEN}
    assert {r["content"]: r["user_id"] for r in store.facts()} == {
        "hates cilantro": BEN, "going to japan": BEN
    }
    assert reacts == [1]


def test_mention_name_linked_to_other_files_under_mentioned():
    store = FakeStore()
    run(store.link_name(G, "ben", ANTO, CHEUNG, None))
    m = mention_msg(1, "@ben is going to japan", {"ben": BEN})
    run(apply_ops([Op("add", 1, subject="name", name="ben", content="going to japan")], context(m), store))
    assert run(store.get_links(G)) == {"ben": ANTO}
    [row] = store.facts()
    assert row["user_id"] == BEN


def test_mention_locked_name_not_taken():
    store = FakeStore()
    m = mention_msg(1, "@sengakae is cooking", {"sengakae": CHEUNG})
    run(apply_ops([Op("add", 1, subject="name", name="sengakae", content="cooking")], context(m, members={}), store))
    assert run(store.get_links(G)) == {}
    [row] = store.facts()
    assert row["user_id"] == CHEUNG


def test_direct_name_add_creates_profile():
    store = FakeStore()
    m = qmsg(1, content="paruru, ben is the global chairman of cover corp")
    m.direct = True
    reacts = run(apply_ops([Op("add", 1, subject="name", name="ben", content="global chairman of cover corp", joke=True)], context(m, members={}), store))
    [row] = store.facts()
    assert row["name"] == "ben" and row["user_id"] is None
    assert reacts == [1]
    assert [m["content"] for m in run(recall(G, "ben", ANTO, store))] == ["global chairman of cover corp"]
