import json
from datetime import datetime, timezone

from utils.memory_learner import LEARNER_INSTRUCTIONS, build_learner_prompt, parse_ops
from utils.memory_queue import QueuedMessage


def qmsg(message_id, content, author_id=100, label="anto", mentions=(), reply_to=None, reactions=None):
    return QueuedMessage(
        id=message_id,
        channel_id=10,
        guild_id=1,
        author_id=author_id,
        author_label=label,
        content=content,
        mention_ids=list(mentions),
        reply_to_author_id=reply_to,
        created_at=datetime.now(timezone.utc),
        reactions=reactions or {},
    )


def test_build_prompt_includes_ids_and_reactions():
    batch = [qmsg(555, "ben is the global chairman of kpmg", mentions=[200], reply_to=300, reactions={"💀": 2})]
    prompt = build_learner_prompt([], batch, ["ben", "anto"], {"ben": 200}, [])
    assert "[555] anto (100): ben is the global chairman of kpmg" in prompt
    assert "mentions: 200" in prompt
    assert "reply to: 300" in prompt
    assert "reactions: 💀x2" in prompt
    assert "ben -> 200" in prompt


def test_build_prompt_marks_context():
    context = [qmsg(1, "old message")]
    batch = [qmsg(2, "new message")]
    prompt = build_learner_prompt(context, batch, [], {}, [])
    context_part, batch_part = prompt.split("NEW MESSAGES")
    assert "old message" in context_part and "read-only" in context_part
    assert "new message" in batch_part


def test_build_prompt_lists_memories_with_ids():
    memories = [{"id": 7, "user_id": 200, "name": "ben", "kind": "fact", "content": "plays sekiro", "joke": True}]
    prompt = build_learner_prompt([], [qmsg(1, "hi")], [], {}, memories)
    assert "#7" in prompt and "plays sekiro" in prompt and "joke" in prompt


def test_parse_ops_valid():
    raw = json.dumps({"ops": [
        {"op": "add", "message_id": "555", "subject": "name", "name": "ben", "content": "global chairman of kpmg", "joke": True},
        {"op": "add", "message_id": "556", "subject": "self", "content": "likes tea"},
        {"op": "update", "message_id": "557", "memory_id": "7", "content": "plays lies of p"},
        {"op": "forget", "message_id": "558", "memory_id": "8"},
        {"op": "link", "message_id": "559", "name": "ben", "user_id": "200"},
    ]})
    ops = parse_ops(raw)
    assert [o.op for o in ops] == ["add", "add", "update", "forget", "link"]
    assert ops[0].message_id == 555 and ops[0].joke is True and ops[0].name == "ben"
    assert ops[1].subject == "self" and ops[1].joke is False
    assert ops[2].memory_id == 7
    assert ops[4].user_id == 200


def test_parse_ops_skips_invalid():
    raw = json.dumps({"ops": [
        {"op": "explode", "message_id": "1"},
        {"op": "forget", "message_id": "2"},
        {"op": "add", "message_id": "abc", "subject": "self", "content": "x"},
        {"op": "add", "message_id": "3", "subject": "name", "content": "no name"},
        {"op": "link", "message_id": "4", "name": "ben"},
        {"op": "add", "message_id": "5", "subject": "self", "content": "kept"},
    ]})
    ops = parse_ops(raw)
    assert [(o.op, o.message_id) for o in ops] == [("add", 5)]
    assert parse_ops("not json") == []
    assert parse_ops(json.dumps([1, 2])) == []


def test_build_prompt_marks_direct_messages():
    m = qmsg(1, "paruru, ben is the chairman")
    m.direct = True
    prompt = build_learner_prompt([], [m, qmsg(2, "lol")], [], {}, [])
    assert "[1] (to paruru) anto (100): paruru, ben is the chairman" in prompt
    assert "[2] anto (100): lol" in prompt


def test_instructions_exclude_requests_to_paruru():
    # "paruru, cheap chinese food in richmond" was being saved as a memory about the asker
    text = LEARNER_INSTRUCTIONS.lower()
    assert "questions and requests" in text
    assert "not facts" in text
