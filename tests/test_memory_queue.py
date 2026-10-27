from datetime import datetime, timezone

from utils.memory_queue import PendingQueue, QueuedMessage


def msg(message_id, channel_id=10, guild_id=1, content=None):
    return QueuedMessage(
        id=message_id,
        channel_id=channel_id,
        guild_id=guild_id,
        author_id=100,
        author_label="anto",
        content=content or f"message {message_id}",
        mention_ids=[],
        reply_to_author_id=None,
        created_at=datetime.now(timezone.utc),
    )


def fill(queue, ids, channel_id=10, guild_id=1):
    for i in ids:
        queue.add(msg(i, channel_id, guild_id))


def ids(messages):
    return [m.id for m in messages]


def test_holdback():
    q = PendingQueue()
    fill(q, range(1, 41))
    _, batch = q.take_batch(10)
    assert ids(batch) == list(range(1, 38))
    q.mark_processed(10, 37)
    fill(q, range(41, 51))
    _, batch = q.take_batch(10)
    assert ids(batch) == list(range(38, 48))


def test_context_is_processed_only():
    q = PendingQueue()
    fill(q, range(1, 41))
    q.take_batch(10)
    q.mark_processed(10, 37)
    fill(q, range(41, 51))
    context, _ = q.take_batch(10)
    assert ids(context) == [33, 34, 35, 36, 37]


def test_cap_drops_oldest():
    q = PendingQueue()
    fill(q, range(1, 151))
    _, batch = q.take_batch(10)
    assert batch[0].id == 51
    assert not q.is_pending(50)


def test_delete_removes():
    q = PendingQueue()
    fill(q, range(1, 11))
    q.delete(5)
    _, batch = q.take_batch(10)
    assert 5 not in ids(batch)
    assert q.is_pending(5) is False


def test_edit_updates_content():
    q = PendingQueue()
    fill(q, range(1, 11))
    q.edit(2, "edited")
    _, batch = q.take_batch(10)
    assert batch[1].content == "edited"


def test_reactions_counted():
    q = PendingQueue()
    fill(q, range(1, 11))
    q.react(1, "💀", 1)
    q.react(1, "💀", 1)
    _, batch = q.take_batch(10)
    assert batch[0].reactions == {"💀": 2}
    q.react(1, "💀", -1)
    q.react(1, "💀", -1)
    _, batch = q.take_batch(10)
    assert batch[0].reactions == {}


def test_few_messages_no_batch():
    q = PendingQueue()
    fill(q, range(1, 4))
    _, batch = q.take_batch(10)
    assert batch == []


def test_processed_not_pending():
    q = PendingQueue()
    fill(q, range(1, 11))
    q.mark_processed(10, 7)
    assert q.is_pending(7) is False
    assert q.is_pending(8) is True


def test_channels_with_pending():
    q = PendingQueue()
    fill(q, range(1, 11), channel_id=10, guild_id=1)
    fill(q, range(11, 14), channel_id=20, guild_id=1)  # only holdback, no batch
    fill(q, range(14, 24), channel_id=30, guild_id=2)
    assert q.channels_with_pending(1) == [10]
    assert q.channels_with_pending(2) == [30]
