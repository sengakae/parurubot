import asyncio
from datetime import datetime, timezone

import pytest

from config import PARTNER_USER_ID
from tests.fake_store import FakeStore
from utils.memory_queue import QueuedMessage
from utils.memory_rules import (ApplyContext, Op, admin_link, apply_ops,
                                normalize_name, recall, remove_for_owner)

G = 1
RORY, MILO, TESS = 100, 200, 300


def run(coro):
    return asyncio.run(coro)


def qmsg(message_id, author_id=RORY, content="hi", mention_ids=(), reply_to=None):
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
        member_names=members if members is not None else {"milo": MILO, "rory": RORY},
        is_pending=pending,
    )


def test_normalize_name():
    assert normalize_name(" Milo ") == "milo"
    assert normalize_name("@milo") == "milo"
    assert normalize_name("MILO") == "milo"
    assert normalize_name("<@123>") is None
    assert normalize_name("") is None
    assert normalize_name(None) is None
    assert normalize_name("x" * 33) is None


def test_self_add():
    store = FakeStore()
    m = qmsg(1, content="i'm moving to atlantis")
    run(apply_ops([Op("add", 1, subject="self", content="moving to atlantis")], context(m), store))
    [row] = store.facts()
    assert row["user_id"] == RORY and row["added_by"] == RORY and row["name"] is None


def test_name_add_member_name_links_to_that_member():
    store = FakeStore()
    m = qmsg(1, content="milo is going to the moon")
    run(apply_ops([Op("add", 1, subject="name", name="Milo", content="going to the moon")], context(m), store))
    [row] = store.facts()
    assert row["user_id"] == MILO and row["name"] == "milo" and row["added_by"] == RORY
    assert run(store.get_links(G)) == {"milo": MILO}


def test_name_add_ambiguous_member_name_stays_unlinked():
    store = FakeStore()
    m = qmsg(1, content="milo is going to the moon")
    run(apply_ops([Op("add", 1, subject="name", name="milo", content="going to the moon")],
                  context(m, members={"milo": None}), store))
    [row] = store.facts()
    assert row["user_id"] is None and run(store.get_links(G)) == {}


@pytest.mark.parametrize("subject, name, content, expected", [
    ("self", None, "goes by nova", RORY),
    ("name", "milo", "goes by nova", MILO),
    ("self", None, "Goes by Nova.", RORY),
])
def test_goes_by_becomes_a_name_link(subject, name, content, expected):
    store = FakeStore()
    m = qmsg(1, content="call me nova")
    run(apply_ops([Op("add", 1, subject=subject, name=name, content=content)], context(m), store))
    assert store.facts() == []
    assert run(store.get_links(G))["nova"] == expected


@pytest.mark.parametrize("members", [
    {"milo": MILO, "rory": RORY},
    {"milo": MILO, "rory": None},
])
def test_goes_by_cannot_take_another_members_name(members):
    store = FakeStore()
    m = qmsg(1, author_id=TESS, content="milo goes by rory now lmao")
    run(apply_ops([Op("add", 1, subject="name", name="milo", content="goes by rory")],
                  context(m, members=members), store))
    assert "rory" not in run(store.get_links(G))


def test_goes_by_without_an_account_is_dropped():
    store = FakeStore()
    m = qmsg(1, author_id=TESS, content="wren goes by jj", reply_to=None)
    reacts = run(apply_ops([Op("add", 1, subject="name", name="wren", content="goes by jj", addressed=True)],
                           context(m, members={}), store))
    assert store.facts() == [] and run(store.get_links(G)) == {} and reacts == []


@pytest.mark.parametrize("existing", ["plays croquet", "Plays Croquet.", "plays  croquet "])
def test_duplicate_fact_not_saved(existing):
    store = FakeStore()
    run(store.add_memory(G, RORY, None, "fact", existing, False, RORY, None))
    m = qmsg(1, content="i play croquet")
    reacts = run(apply_ops([Op("add", 1, subject="self", content="plays croquet")], context(m), store))
    assert len(store.facts()) == 1 and reacts == []


def test_same_fact_for_another_person_is_saved():
    store = FakeStore()
    run(store.add_memory(G, MILO, "milo", "fact", "plays croquet", False, MILO, None))
    m = qmsg(1, content="i play croquet")
    run(apply_ops([Op("add", 1, subject="self", content="plays croquet")], context(m), store))
    assert len(store.facts()) == 2


def test_name_add_unknown_dropped():
    store = FakeStore()
    m = qmsg(1, content="zorblax is buying another company")
    reacts = run(apply_ops([Op("add", 1, subject="name", name="zorblax", content="buying a company")], context(m), store))
    assert store.facts() == [] and reacts == []


def test_name_add_addressed():
    store = FakeStore()
    m = qmsg(1, content="wren you coming tonight? wren got a new job")
    run(apply_ops([Op("add", 1, subject="name", name="wren", content="got a new job", addressed=True)], context(m), store))
    assert len(store.facts()) == 1


def test_name_add_addressed_requires_name_in_text():
    store = FakeStore()
    m = qmsg(1, content="someone got a new job")
    run(apply_ops([Op("add", 1, subject="name", name="wren", content="got a new job", addressed=True)], context(m), store))
    assert store.facts() == []


def test_name_add_linked_goes_to_user():
    store = FakeStore()
    run(store.link_name(G, "milo", MILO, MILO, None))
    m = qmsg(1, content="milo is going to the moon")
    run(apply_ops([Op("add", 1, subject="name", name="milo", content="going to the moon")], context(m), store))
    [row] = store.facts()
    assert row["user_id"] == MILO and row["name"] == "milo"


def test_locked_name_add():
    store = FakeStore()
    m = qmsg(1, content="quinn is cooking tonight")
    run(apply_ops([Op("add", 1, subject="name", name="quinn", content="cooking tonight")], context(m, members={}), store))
    [row] = store.facts()
    assert row["user_id"] == PARTNER_USER_ID


def test_link_merges():
    store = FakeStore()
    m1 = qmsg(1, content="milo is going to the moon")
    m2 = qmsg(2, content="milo hates cilantro")
    m3 = qmsg(3, author_id=TESS, content="that's milo", reply_to=MILO)
    ops = [
        Op("add", 1, subject="name", name="milo", content="going to the moon"),
        Op("add", 2, subject="name", name="milo", content="hates cilantro"),
        Op("link", 3, name="milo", user_id=MILO),
    ]
    run(apply_ops(ops, context(m1, m2, m3), store))
    assert {r["user_id"] for r in store.facts()} == {MILO}
    assert run(store.get_links(G)) == {"milo": MILO}


def test_link_conflict_rejected():
    store = FakeStore()
    run(store.link_name(G, "milo", MILO, MILO, None))
    m = qmsg(1, author_id=TESS, content="i'm milo")
    reacts = run(apply_ops([Op("link", 1, name="milo", user_id=TESS)], context(m), store))
    assert run(store.get_links(G)) == {"milo": MILO} and reacts == []


def test_link_same_user_noop():
    store = FakeStore()
    run(store.link_name(G, "milo", MILO, MILO, None))
    m = qmsg(1, author_id=MILO, content="i'm milo")
    reacts = run(apply_ops([Op("link", 1, name="milo", user_id=MILO)], context(m), store))
    assert reacts == []


def test_link_locked_rejected():
    store = FakeStore()
    m = qmsg(1, author_id=TESS, content="i'm quinn")
    run(apply_ops([Op("link", 1, name="quinn", user_id=TESS)], context(m), store))
    assert run(store.get_links(G)) == {}


def test_link_user_must_be_in_batch():
    store = FakeStore()
    m = qmsg(1, content="that's milo")
    run(apply_ops([Op("link", 1, name="milo", user_id=999)], context(m), store))
    assert run(store.get_links(G)) == {}


def test_link_rejected_when_message_not_tied_to_user():
    store = FakeStore()
    m1 = qmsg(1, author_id=TESS, content="my cactus is named steve")
    m2 = qmsg(2, author_id=RORY, content="lets goo milo")
    reacts = run(apply_ops([Op("link", 2, name="milo", user_id=TESS)], context(m1, m2), store))
    assert run(store.get_links(G)) == {} and reacts == []


@pytest.mark.parametrize("answer", [
    qmsg(2, author_id=TESS, content="is it a tuesday"),
    qmsg(2, author_id=TESS, content="yea", reply_to=RORY),
])
def test_link_rejected_when_user_only_answers(answer):
    store = FakeStore()
    m1 = qmsg(1, author_id=RORY, content="your cactus looks thirsty, milo")
    run(apply_ops([Op("link", 1, name="milo", user_id=TESS)], context(m1, answer), store))
    assert run(store.get_links(G)) == {}


def test_link_accepted_when_message_replies_to_user_by_name():
    store = FakeStore()
    m = qmsg(1, author_id=RORY, content="are you entering the pie contest tess", reply_to=TESS)
    run(apply_ops([Op("link", 1, name="tess", user_id=TESS)], context(m), store))
    assert run(store.get_links(G)) == {"tess": TESS}


def test_deleted_message_ops_dropped():
    store = FakeStore()
    existing = run(store.add_memory(G, MILO, "milo", "fact", "old", False, TESS, None))
    m = qmsg(1, content="milo is going to the moon", mention_ids=[MILO])
    ops = [
        Op("add", 1, subject="name", name="milo", content="going to the moon"),
        Op("update", 1, memory_id=existing, content="new", joke=False),
        Op("forget", 1, memory_id=existing),
        Op("link", 1, name="milo", user_id=MILO),
    ]
    reacts = run(apply_ops(ops, context(m, pending=lambda _id: False), store))
    assert reacts == []
    assert store.rows[existing]["content"] == "old"
    assert len(store.rows) == 1


def test_owner_protected():
    store = FakeStore()
    own = run(store.add_memory(G, MILO, None, "fact", "plays hopscotch", False, MILO, None))
    m = qmsg(1, author_id=TESS, content="milo doesn't play hopscotch anymore")
    run(apply_ops([Op("forget", 1, memory_id=own)], context(m), store))
    assert own in store.rows


def test_owner_can_change_own():
    store = FakeStore()
    own = run(store.add_memory(G, MILO, None, "fact", "plays hopscotch", False, MILO, None))
    m = qmsg(1, author_id=MILO, content="i stopped playing hopscotch")
    run(apply_ops([Op("forget", 1, memory_id=own)], context(m), store))
    assert own not in store.rows


def test_foreign_memory_id_rejected():
    store = FakeStore()
    other = run(store.add_memory(2, MILO, None, "fact", "other guild", False, TESS, None))
    m = qmsg(1)
    run(apply_ops([Op("update", 1, memory_id=other, content="hacked", joke=False)], context(m), store))
    assert store.rows[other]["content"] == "other guild"


def test_update_name_row_rejected():
    store = FakeStore()
    run(store.link_name(G, "milo", MILO, TESS, None))
    link_id = next(r["id"] for r in store.rows.values() if r["kind"] == "name")
    m = qmsg(1)
    run(apply_ops([Op("update", 1, memory_id=link_id, content="benny", joke=False)], context(m), store))
    assert store.rows[link_id]["content"] == "milo"


def test_forget_name_row_unlinks():
    store = FakeStore()
    run(store.add_memory(G, None, "milo", "fact", "going to the moon", False, TESS, None))
    run(store.link_name(G, "milo", MILO, TESS, None))
    link_id = next(r["id"] for r in store.rows.values() if r["kind"] == "name")
    m = qmsg(1, author_id=TESS, content="wait milo isn't him")
    run(apply_ops([Op("forget", 1, memory_id=link_id)], context(m), store))
    assert run(store.get_links(G)) == {}
    assert store.facts()[0]["user_id"] is None


def test_react_ids():
    store = FakeStore()
    existing = run(store.add_memory(G, MILO, "milo", "fact", "old", False, TESS, None))
    gone = run(store.add_memory(G, MILO, "milo", "fact", "gone", False, TESS, None))
    msgs = [qmsg(1, content="i like tea"), qmsg(2), qmsg(3, mention_ids=[MILO]), qmsg(4)]
    ops = [
        Op("add", 1, subject="self", content="likes tea"),
        Op("update", 2, memory_id=existing, content="new", joke=False),
        Op("link", 3, name="milo", user_id=MILO),
        Op("forget", 4, memory_id=gone),
    ]
    reacts = run(apply_ops(ops, context(*msgs), store))
    assert reacts == [1, 2, 3]


def test_admin_link_relinks():
    store = FakeStore()
    run(store.add_memory(G, None, "milo", "fact", "going to the moon", False, TESS, None))
    run(store.link_name(G, "milo", RORY, TESS, None))
    assert run(admin_link(G, "milo", MILO, 1, store)) == "relinked"
    assert run(store.get_links(G)) == {"milo": MILO}
    assert store.facts()[0]["user_id"] == MILO
    assert run(admin_link(G, "milo", MILO, 1, store)) == "unchanged"
    assert run(admin_link(G, "wren", TESS, 1, store)) == "linked"


def test_admin_link_locked_rejected():
    store = FakeStore()
    with pytest.raises(ValueError):
        run(admin_link(G, "quinn", MILO, 1, store))
    with pytest.raises(ValueError):
        run(admin_link(G, "<@1>", MILO, 1, store))


def test_remove_for_owner():
    store = FakeStore()
    fact = run(store.add_memory(G, MILO, None, "fact", "plays hopscotch", False, MILO, None))
    assert run(remove_for_owner(G, fact, TESS, store)) is False
    assert run(remove_for_owner(G, fact, MILO, store)) is True
    assert fact not in store.rows

    run(store.add_memory(G, None, "milo", "fact", "going to the moon", False, TESS, None))
    run(store.link_name(G, "milo", MILO, TESS, None))
    link_id = next(r["id"] for r in store.rows.values() if r["kind"] == "name")
    assert run(remove_for_owner(G, link_id, MILO, store)) is True
    assert run(store.get_links(G)) == {}
    assert store.facts()[0]["user_id"] is None

    run(store.link_name(G, "quinn", PARTNER_USER_ID, PARTNER_USER_ID, None))
    locked_id = next(r["id"] for r in store.rows.values() if r["content"] == "quinn")
    assert run(remove_for_owner(G, locked_id, PARTNER_USER_ID, store)) is False


def test_recall():
    store = FakeStore()
    run(store.add_memory(G, RORY, None, "fact", "likes tea", False, RORY, None))
    run(store.add_memory(G, None, "wren", "fact", "new job", False, RORY, None))
    run(store.link_name(G, "milo", MILO, TESS, None))
    run(store.add_memory(G, MILO, "milo", "fact", "going to the moon", True, TESS, None))
    assert [m["content"] for m in run(recall(G, "me", RORY, store))] == ["likes tea"]
    assert [m["content"] for m in run(recall(G, None, RORY, store))] == ["likes tea"]
    assert "going to the moon" in [m["content"] for m in run(recall(G, "Milo", RORY, store))]
    assert [m["content"] for m in run(recall(G, "wren", RORY, store))] == ["new job"]


def mention_msg(message_id, content, names):
    m = qmsg(message_id, content=content, mention_ids=list(names.values()))
    m.mention_names = names
    return m


def test_mention_add_links_and_files_under_user():
    store = FakeStore()
    run(store.add_memory(G, None, "milton", "fact", "hates cilantro", False, TESS, None))
    m = mention_msg(1, "@Milton is going to the moon", {"milton": MILO})
    reacts = run(apply_ops([Op("add", 1, subject="name", name="@Milton", content="going to the moon")], context(m, members={}), store))
    assert run(store.get_links(G)) == {"milton": MILO}
    assert {r["content"]: r["user_id"] for r in store.facts()} == {
        "hates cilantro": MILO, "going to the moon": MILO
    }
    assert reacts == [1]


def test_mention_name_linked_to_other_files_under_mentioned():
    store = FakeStore()
    run(store.link_name(G, "milo", RORY, TESS, None))
    m = mention_msg(1, "@milo is going to the moon", {"milo": MILO})
    run(apply_ops([Op("add", 1, subject="name", name="milo", content="going to the moon")], context(m), store))
    assert run(store.get_links(G)) == {"milo": RORY}
    [row] = store.facts()
    assert row["user_id"] == MILO


def test_mention_locked_name_not_taken():
    store = FakeStore()
    m = mention_msg(1, "@quinn is cooking", {"quinn": TESS})
    run(apply_ops([Op("add", 1, subject="name", name="quinn", content="cooking")], context(m, members={}), store))
    assert run(store.get_links(G)) == {}
    [row] = store.facts()
    assert row["user_id"] == TESS


def test_direct_name_add_creates_profile():
    store = FakeStore()
    m = qmsg(1, content="paruru, milo is the keeper of the royal llamas")
    m.direct = True
    reacts = run(apply_ops([Op("add", 1, subject="name", name="milo", content="keeper of the royal llamas", joke=True)], context(m, members={}), store))
    [row] = store.facts()
    assert row["name"] == "milo" and row["user_id"] is None
    assert reacts == [1]
    assert [m["content"] for m in run(recall(G, "milo", RORY, store))] == ["keeper of the royal llamas"]
