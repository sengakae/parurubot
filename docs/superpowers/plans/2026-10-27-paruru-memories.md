# Paruru Memories Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Paruru learns memories about server members from conversation, recalls them in chat, and lets each person manage their own with `!aboutme`.

**Architecture:** A cog queues guild messages in memory. When the bot is called, it sends each channel's unprocessed batch to Gemini Flash-Lite in one structured-output call. Code then applies the returned ops to a Postgres `memories` table, enforcing every rule. Pure logic (queue, rules, prompt/parse, view helpers) lives in `utils/` modules tested against a fake store. Discord wiring lives in `cogs/memories.py`, and chat tools are added to `utils/tools.py`.

**Tech Stack:** Python 3.10, discord.py, asyncpg, google-genai, pytest.

**Spec:** `docs/superpowers/specs/2026-10-27-paruru-memories-design.md`

## Global Constraints

- Same `parurubot` Postgres database. The new table is created in `db.init_db()` with `CREATE TABLE IF NOT EXISTS`.
- Memories are scoped per guild (`guild_id` on every row and query).
- Names are casefolded. Unique index on `(guild_id, content) WHERE kind = 'name'`.
- Locked names: `{"sengakae": PARTNER_USER_ID}` (from `config.py`). Never linked to another ID, and never removable via `!aboutme`.
- Admin = `int(os.getenv("OWNER_ID"))`, the same as `cogs/update.py`.
- Batching never runs on a timer or count. It is triggered only when the bot is called (a `paruru,` chat, or any `!` message that runs a command or quote), and covers every channel in that guild with pending messages.
- Queue cap: 100 messages per channel. Holdback: 3. Context: 5 already-processed messages.
- Every op carries a `message_id`. An op whose message is no longer pending is dropped.
- Reaction: `📝` on the source message of each applied `add`, `update`, or `link`. Never on `forget`. Reaction failures are ignored.
- Learner model: `gemini-2.5-flash-lite`, with no personality prompt.
- The joke flag is never shown in the embed or the dropdown.
- Removals don't stick, so there are no tombstones.
- Queue and processed markers live in memory and reset on restart.
- New prompts live in code. Do not edit `config.py`, which is deployed by hand.
- Requires the Server Members Intent: set `intents.members = True` in code, and enable it in the Developer Portal (deploy step).
- No emojis in bot messages except the `📝` reaction.

## Review Focus

1. **Messy names from the model** (`"Ben "`, `"@ben"`, `"<@123>"`, `"BEN"`). Names should normalize to `ben`. Mention syntax and empty names should be rejected, not stored. Test: Task 3, `test_normalize_name`.
2. **Bad model output** (invalid JSON, unknown op, missing fields, a `memory_id` from another guild). Bad entries should be skipped, valid ones still applied, and nothing should crash. If the model call itself raises, the batch stays unprocessed so it is retried on the next call. Tests: Task 4 `test_parse_ops_skips_invalid`, Task 3 `test_foreign_memory_id_rejected`, Task 5 manual check.
3. **Two calls in quick succession** in the same guild. Only one run per guild at a time; the second trigger is skipped, not queued. Test: Task 5, `test_process_guild_single_flight`.
4. **Long memory text** (over 100 characters). Dropdown labels are truncated to 100 characters and the embed stays under 4096 characters. Test: Task 6, `test_select_label_truncates`.
5. **DMs and threads.** DMs are never queued. Threads are queued like channels. Test: Task 5, `test_should_queue`.

---

### Task 1: Memories table and queries

**Files:**
- Modify: `db.py` (add a table in `init_db`, and new functions at the end)
- Create: `scripts/check_memories_db.py` (manual verification against `DATABASE_URL`)

**Interfaces:**
- Produces (all `async`, in `db.py`). A memory is a `dict` with keys `id, guild_id, user_id, name, kind, content, joke, added_by, source_message_id`.
  - `add_memory(guild_id: int, user_id: int | None, name: str | None, kind: str, content: str, joke: bool, added_by: int, source_message_id: int | None) -> int`
  - `update_memory(memory_id: int, content: str, joke: bool) -> None` (also sets `updated_at = NOW()`)
  - `delete_memory(memory_id: int) -> None`
  - `get_memory(memory_id: int) -> dict | None`
  - `get_user_memories(guild_id: int, user_id: int) -> list[dict]`, ordered by `created_at`
  - `get_name_memories(guild_id: int, name: str) -> list[dict]`, for rows with `user_id IS NULL AND name = $2`
  - `get_links(guild_id: int) -> dict[str, int]`, mapping name to user_id for `kind = 'name'` rows
  - `link_name(guild_id: int, name: str, user_id: int, added_by: int, source_message_id: int | None) -> int`. In one transaction: insert the name row (`kind='name'`, `content=name`, `name=name`), then `UPDATE memories SET user_id=$user WHERE guild_id=$g AND name=$name AND user_id IS NULL`.
  - `unlink_name(guild_id: int, name: str) -> None`. In one transaction: find the linked user, delete the name row, then `UPDATE memories SET user_id=NULL WHERE guild_id=$g AND name=$name AND kind='fact' AND user_id=$old`.

- [ ] **Step 1:** In `init_db`, add the `memories` table: columns as in the spec's Data model, `joke BOOLEAN NOT NULL DEFAULT FALSE`, timestamps `TIMESTAMPTZ DEFAULT NOW()`. Add the unique partial index `memories_name_idx` and an index on `(guild_id, user_id)`.
- [ ] **Step 2:** Implement the functions above in `db.py`, following the existing `pool.acquire()` style. Return rows as `dict(row)`.
- [ ] **Step 3:** Write `scripts/check_memories_db.py`. It loads `.env`, runs `init_db()`, uses guild_id `-1` and covers this sequence:
  - add a name-only fact about `ben`
  - `link_name("ben", 111)`, then assert the fact now has `user_id == 111`
  - `unlink_name`, then assert the fact has `user_id IS NULL`
  - a second `link_name("ben", 222)` after relinking to 111 raises `asyncpg.UniqueViolationError`
  - delete everything with `guild_id = -1` at the end
- [ ] **Step 4:** Run `venv/Scripts/python.exe scripts/check_memories_db.py` against a non-production `DATABASE_URL`. Expected output: `ok`.
- [ ] **Step 5:** Commit: `git add db.py scripts/check_memories_db.py && git commit -m "feat: memories table and queries"`

### Task 2: Pending message queue

**Files:**
- Create: `utils/memory_queue.py`, `tests/__init__.py`, `tests/test_memory_queue.py`, `requirements-dev.txt` (containing `pytest`)

**Interfaces:**
- Produces:
  - `@dataclass QueuedMessage`, with fields:
    - `id: int`, `channel_id: int`, `guild_id: int`
    - `author_id: int`, `author_label: str`, `content: str`
    - `mention_ids: list[int]`, `reply_to_author_id: int | None`
    - `created_at: datetime`
    - `reactions: dict[str, int] = {}`, mapping emoji to count
  - `class PendingQueue` with `MAX_PER_CHANNEL = 100`, `HOLDBACK = 3`, `CONTEXT = 5`, and methods:
    - `add(msg: QueuedMessage) -> None`
    - `edit(message_id: int, content: str) -> None`
    - `delete(message_id: int) -> None`
    - `react(message_id: int, emoji: str, delta: int) -> None`
    - `is_pending(message_id: int) -> bool`. True only for queued, unprocessed messages.
    - `channels_with_pending(guild_id: int) -> list[int]`
    - `take_batch(channel_id: int) -> tuple[list[QueuedMessage], list[QueuedMessage]]`. Returns `(context, batch)`, where batch is the unprocessed messages minus the newest `HOLDBACK`, and context is up to `CONTEXT` processed messages just before the batch. It does not mark anything.
    - `mark_processed(channel_id: int, up_to_message_id: int) -> None`

Each channel is a `deque(maxlen=100)` of all messages plus a processed-marker ID, so the oldest messages fall off once the cap is reached.

- [ ] **Step 1:** Write the failing tests:
  - `test_holdback`: add 40 messages; `take_batch` returns ids 1-37; `mark_processed(37)`; add 10 more; next batch is 38-47.
  - `test_context_is_processed_only`: after the above, the context of the second batch is ids 33-37.
  - `test_cap_drops_oldest`: add 150 messages; the batch starts at id 51.
  - `test_delete_removes`: deleting id 5 means it's absent from the batch and `is_pending(5) is False`.
  - `test_edit_updates_content`.
  - `test_reactions_counted`: `react(1, "💀", 1)` twice gives `reactions == {"💀": 2}`; `delta=-1` decrements, and the key is removed at 0.
  - `test_few_messages_no_batch`: 3 unprocessed gives an empty batch.
  - `test_channels_with_pending`: only channels of that guild that have a non-empty batch.
- [ ] **Step 2:** Run `venv/Scripts/python.exe -m pytest tests/test_memory_queue.py -v`. Expected: FAIL (module missing). Install the dev requirements first with `venv/Scripts/python.exe -m pip install -r requirements-dev.txt`.
- [ ] **Step 3:** Implement `utils/memory_queue.py`.
- [ ] **Step 4:** Run the tests again. Expected: all PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: pending message queue for memory learner"`

### Task 3: Rules and op application

**Files:**
- Create: `utils/memory_rules.py`, `tests/fake_store.py`, `tests/test_memory_rules.py`

**Interfaces:**
- Consumes: Task 1's `db` function names and signatures (the store), and Task 2's `QueuedMessage`.
- Produces:
  - `LOCKED_NAMES: dict[str, int] = {"sengakae": PARTNER_USER_ID}`
  - `normalize_name(raw: str | None) -> str | None`. Strips whitespace and a leading `@`, then casefolds. Returns `None` if the result is empty, contains `<@`, or is longer than 32 characters.
  - `@dataclass Op`:
    - `op: str` (`add|update|forget|link`)
    - `message_id: int`
    - `subject: str | None = None` (`self|name`)
    - `name: str | None = None`
    - `content: str | None = None`
    - `joke: bool = False`
    - `memory_id: int | None = None`
    - `user_id: int | None = None`
    - `addressed: bool = False`
  - `@dataclass ApplyContext`:
    - `guild_id: int`
    - `messages: dict[int, QueuedMessage]` (the batch, by id)
    - `member_names: dict[str, int]` (casefolded name to user id)
    - `is_pending: Callable[[int], bool]`
  - `async apply_ops(ops: list[Op], ctx: ApplyContext, store=db) -> list[int]`. Returns the message ids to react to.
  - `async admin_link(guild_id: int, name: str, user_id: int, admin_id: int, store=db) -> str`. Returns `"linked"`, `"relinked"` or `"unchanged"`. Raises `ValueError` for a locked name pointing at a different ID, or an invalid name.
  - `async remove_for_owner(guild_id: int, memory_id: int, owner_id: int, store=db) -> bool`. For a name row, unlinks; for a fact, deletes. Returns False if it isn't the owner's or the name is locked.
  - `async ensure_locked_links(guild_id: int, store=db) -> None`
  - `async recall(guild_id: int, name: str | None, speaker_id: int, store=db) -> list[dict]`. A `None` name, `"me"` or `"myself"` means the speaker. A linked or locked name returns that user's memories; an unlinked name returns name-only memories.

Rules that `apply_ops` enforces, in order:
1. Skip the op unless `ctx.is_pending(op.message_id)` and the message id is in `ctx.messages`.
2. **`add`:**
   - With `subject == "self"`, store under the author with `added_by = author`.
   - With a name, normalize it, then resolve it: a locked name goes to its ID; else a linked name goes to the linked ID; else, if the name is in `member_names` or (`addressed` and the name appears in the message content), store it name-only with `user_id=None`; otherwise drop it.
   - `added_by` is the message author, and `name` is stored on the row.
3. **`update` / `forget`:**
   - The memory must exist with this `guild_id`.
   - Reject if `added_by == user_id` and the op's author is not `user_id` (owner-protected).
   - `update` on a `kind == "name"` row is rejected.
   - `forget` on a name row unlinks it, unless the name is locked.
4. **`link`:**
   - `user_id` must be one of the batch's author, mention or reply-author IDs.
   - A locked name may only link to its own ID.
   - A name linked to a different user is rejected and logged; one linked to the same user is a no-op with no reaction.
5. React ids are collected for each applied `add`, `update` and `link`.

`FakeStore` in `tests/fake_store.py` implements Task 1's functions in memory, with the same names and signatures, including the unique-name violation and merge behavior.

- [ ] **Step 1:** Write the failing tests:
  - `test_normalize_name`: `" Ben "` becomes `"ben"`, `"@ben"` becomes `"ben"`, `"<@123>"` becomes `None`, `""` becomes `None`.
  - `test_self_add`: stored under the author, with `added_by` equal to the author.
  - `test_name_add_unlinked_member`: name-only row, `user_id is None`.
  - `test_name_add_unknown_dropped`: `"elon"` is not a member, so no row is stored and no react id is returned.
  - `test_name_add_addressed`: with `addressed=True` and the name in the content, it is stored.
  - `test_name_add_linked_goes_to_user`.
  - `test_locked_name_add`: `"sengakae"` stores under `PARTNER_USER_ID`.
  - `test_link_merges`: two name-only facts, then link, then both have the `user_id`.
  - `test_link_conflict_rejected` and `test_link_locked_rejected`.
  - `test_link_user_must_be_in_batch`.
  - `test_deleted_message_ops_dropped`: `is_pending` returns False, so nothing is applied for any op type.
  - `test_owner_protected`: someone else's `forget` on a self-added row is rejected.
  - `test_foreign_memory_id_rejected`: a memory from another guild.
  - `test_forget_name_row_unlinks`: the facts move back to name-only.
  - `test_react_ids`: `add` + `update` + `link` + `forget` gives 3 ids, and no id for the forget.
  - `test_admin_link_relinks`: "ben" linked to A; `admin_link` to B returns `"relinked"`; A's ben-facts move to B.
  - `test_remove_for_owner`: not the owner gives False; a locked name gives False; a name row unlinks.
  - `test_recall`: covers "me", a linked name, and an unlinked name.
- [ ] **Step 2:** Run `venv/Scripts/python.exe -m pytest tests/test_memory_rules.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `utils/memory_rules.py` (async tests use `asyncio.run`).
- [ ] **Step 4:** Run the tests again. Expected: all PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: memory rules and op application"`

### Task 4: Learner prompt, schema and parsing

**Files:**
- Create: `utils/memory_learner.py`, `tests/test_memory_learner.py`

**Interfaces:**
- Consumes: `QueuedMessage` (Task 2), `Op` (Task 3), and `_generate_with_error_retry` and `extract_response_text` from `utils/ai.py`.
- Produces:
  - `LEARNER_MODEL = "gemini-2.5-flash-lite"`
  - `LEARNER_INSTRUCTIONS: str`
  - `OPS_SCHEMA: dict`. A JSON schema: an object with `ops`, an array of objects with the `Op` fields.
  - `build_learner_prompt(context: list[QueuedMessage], batch: list[QueuedMessage], member_names: list[str], links: dict[str, int], memories: list[dict]) -> str`. One line per message: `[id] author_label (author_id): content`, plus `| mentions: ... | reply to: ... | reactions: 💀x2` when present. Context lines are marked read-only. Memories are listed with their ids.
  - `parse_ops(raw: str) -> list[Op]`. Skips entries with an unknown `op`, a non-int `message_id`, or missing required fields per op type. Invalid JSON returns `[]`.
  - `async extract_ops(prompt: str) -> list[Op]`. Calls `asyncio.to_thread(_generate_with_error_retry, model=LEARNER_MODEL, contents=prompt, config=GenerateContentConfig(system_instruction=LEARNER_INSTRUCTIONS, response_mime_type="application/json", response_schema=OPS_SCHEMA))`, then `parse_ops`. API errors propagate, so the caller leaves the batch unprocessed.

`LEARNER_INSTRUCTIONS` must state these rules, taken from the spec:
- Only lasting facts or running jokes about people; most messages produce nothing, and returning `[]` is normal.
- Use `self` for statements about the speaker; otherwise use the name as written.
- Only people in the server: linked names, member names, or names spoken to directly. Weigh, in order: whether the name belongs to someone here, how it is written (first name lowercase vs full name or surname), whether that person is in the conversation, and whether the claim is true of a famous person. If still unsure, use the member.
- Flag jokes with `joke: true`, judged from reactions, replies like "lmao" or denials, and implausibility against existing memories. Keep them.
- Use `update` or `forget` with existing memory ids when something changes; never duplicate an existing memory.
- Use `link` on cues like "that's ben", "i'm ben", or replying "ben" to someone, with a `user_id` taken from the message metadata.
- Never take facts from context-only lines.
- Content is short, lowercase, third person without the name (`going to japan in march`).

- [ ] **Step 1:** Write the failing tests:
  - `test_build_prompt_includes_ids_and_reactions`
  - `test_build_prompt_marks_context`
  - `test_parse_ops_valid`: one of each op type.
  - `test_parse_ops_skips_invalid`: an unknown op, a missing `memory_id` on forget, a string `message_id`, and non-JSON input giving `[]`.
- [ ] **Step 2:** Run `venv/Scripts/python.exe -m pytest tests/test_memory_learner.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `utils/memory_learner.py`.
- [ ] **Step 4:** Run the tests again. Expected: PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: memory learner prompt and op parsing"`

### Task 5: Memories cog (queueing, triggering, processing)

**Files:**
- Create: `utils/identity.py` (move `speaker_label` here from `main.py`), `cogs/memories.py`, `tests/test_memories_cog.py`
- Modify: `main.py` (import `speaker_label` from `utils.identity`; set `intents.members = True`; call `bot.dispatch("paruru_called", message)` before handling a command, quote or `paruru,` chat)

**Interfaces:**
- Consumes: Tasks 1-4.
- Produces, in `cogs/memories.py`:
  - `should_queue(message) -> bool`. True when the message has a guild, the author is not a bot, the stripped content is non-empty, and it doesn't start with `!`.
  - `member_name_map(guild) -> dict[str, int]`. Built from each member's `display_name`, `nick`, `name` and `global_name`, casefolded and skipping `None`.
  - `class MemoriesCog(commands.Cog)` with:
    - `queue: PendingQueue`
    - listeners `on_message` (queue it), `on_raw_message_edit`, `on_raw_message_delete`, `on_raw_reaction_add/remove` and `on_ready` (calls `ensure_locked_links` for each guild)
    - `on_paruru_called(message)`
    - `async process_guild(guild) -> None`

`on_paruru_called` does nothing for DMs. If the guild's `asyncio.Lock` is already held, it returns. Otherwise it starts `asyncio.create_task(self.process_guild(guild))`.

`process_guild` runs under the guild lock. For each channel in `channels_with_pending`:
1. Call `take_batch`.
2. Collect names and IDs from the batch and gather their memories via `store.get_user_memories` / `get_name_memories`, with `get_links` and the member names.
3. Call `build_learner_prompt` and then `extract_ops`. On an exception, log and continue with the next channel; the batch is not marked.
4. Call `apply_ops`, then `mark_processed(channel, batch[-1].id)`.
5. Run `channel.get_partial_message(id).add_reaction("📝")` for each returned id, suppressing `discord.HTTPException`.

- [ ] **Step 1:** Write the failing tests, using `SimpleNamespace` fakes:
  - `test_should_queue`: covers a DM, a bot author, a `!cmd` message, empty text, a normal guild message, and a thread message.
  - `test_member_name_map`.
  - `test_process_guild_single_flight`: with the lock held, `on_paruru_called` doesn't create a task.
  - `test_process_guild_marks_and_reacts`: `extract_ops` monkeypatched to return one add; FakeStore as the store; asserts the batch is marked processed and `add_reaction("📝")` is called once.
  - `test_process_guild_failure_keeps_pending`: `extract_ops` raises, so `is_pending` is still True for the batch.
- [ ] **Step 2:** Run `venv/Scripts/python.exe -m pytest tests/test_memories_cog.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement `utils/identity.py`, `cogs/memories.py` and the `main.py` changes. The cog takes `store=db` in `__init__` for tests.
- [ ] **Step 4:** Run `venv/Scripts/python.exe -m pytest -v`. Expected: all PASS. Also run `venv/Scripts/python.exe -c "import main"`. Expected: no error.
- [ ] **Step 5:** Commit: `git commit -m "feat: memories cog with call-triggered batch learning"`

### Task 6: `!aboutme` (layout B)

**Files:**
- Modify: `cogs/memories.py` (add the view, modal, command and `send_aboutme`)
- Create: `tests/test_aboutme.py`

**Interfaces:**
- Consumes: `remove_for_owner`, `LOCKED_NAMES` (Task 3), and `store.get_user_memories` / `add_memory`.
- Produces:
  - `PAGE_SIZE = 25`
  - `page_slice(memories: list[dict], page: int) -> tuple[list[dict], int]`. Returns the items and the total page count (minimum 1).
  - `format_memory_line(index: int, memory: dict) -> str`. A name row shows `goes by <name>`; a fact shows its content. The joke flag is never shown.
  - `select_label(index: int, memory: dict) -> str`. Built from `format_memory_line` and truncated to 100 characters.
  - `async send_aboutme(channel, member) -> None`. Shared by the command and the chat tool (Task 7).
  - `class AboutMeView(discord.ui.View)`, with `timeout=600`. Its `interaction_check` sends any non-owner an ephemeral `"this isn't yours"` and returns False.

Embed details:
- Title: `what i know about <display_name>`.
- Description: a numbered list, using the absolute index across pages.
- Footer: `<n> memories · page x/y`.

Components:
- **Row 0: select** `"Remove a memory..."` listing the page's memories, except name rows for locked names. It's omitted when nothing on the page is removable. Choosing one runs `remove_for_owner` and edits the message with the refreshed embed and view.
- **Row 1: "Add memory" button** (green), which opens a modal with one text input (max 200 characters). On submit it calls `add_memory(guild, owner, None, "fact", text.strip(), False, owner, None)` and refreshes.
- **◀ ▶ buttons** (grey), shown only when there are more than 25 memories.

When the user has no memories, show the description `"i don't know anything about you yet"` with only the Add button.

The `!aboutme` command takes no arguments, is server only, and calls `send_aboutme(ctx.channel, ctx.author)`.

- [ ] **Step 1:** Write the failing tests:
  - `test_page_slice`: 30 memories gives 2 pages; page 2 has 5 items; 0 memories gives 1 page.
  - `test_format_memory_line_name_and_joke`: a joke fact shows no tag.
  - `test_select_label_truncates`: 150 characters truncates to 100.
  - `test_locked_name_not_removable`: the option builder excludes the locked name row.
- [ ] **Step 2:** Run `venv/Scripts/python.exe -m pytest tests/test_aboutme.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the view, the modal, `send_aboutme` and the command.
- [ ] **Step 4:** Run `venv/Scripts/python.exe -m pytest -v`. Expected: all PASS.
- [ ] **Step 5:** Commit: `git commit -m "feat: !aboutme memories embed with remove, add and paging"`

### Task 7: Chat tools, docs, and end-to-end check

**Files:**
- Modify: `utils/tools.py` (add 3 declarations, handlers, `HANDLERS` entries and a `TOOL_GUIDANCE` addition), `README.md` (a Memories section), `TOOL_CALL_TESTS.md` (a Memories section)
- Create: `tests/test_memory_tools.py`

**Interfaces:**
- Consumes: `recall`, `admin_link` (Task 3), `send_aboutme` (Task 6), and `ToolContext`.
- Produces these handlers:
  - `recall_person(ctx, name=None)` returns `{"memories": [{"content", "joke"}]}`, or `{"result": "nothing remembered"}`.
  - `show_my_memories(ctx)` calls `send_aboutme(ctx.message.channel, ctx.message.author)`, appends `"showed memories"` to `ctx.actions`, and returns `{"result": "shown"}`.
  - `link_name(ctx, name)` checks admin first: a non-admin gets `{"error": "only the admin can link names"}`. The user is the first non-bot entry in `ctx.message.mentions`, with an error if there is none. It returns `{"result": <admin_link status>}`.

`TOOL_GUIDANCE` addition: use `recall_person` when asked about a person (or "me"). Bring up jokes playfully and never state them as fact. Use `show_my_memories` when someone asks to see their memories. Use `link_name` when the admin tells you "@user is <name>".

- [ ] **Step 1:** Write the failing tests:
  - `test_link_name_non_admin_rejected`
  - `test_link_name_requires_mention`
  - `test_recall_person_me` (FakeStore patched in)
  - `test_declarations_match_handlers`: the set of declaration names equals the `HANDLERS` keys.
- [ ] **Step 2:** Run `venv/Scripts/python.exe -m pytest tests/test_memory_tools.py -v`. Expected: FAIL.
- [ ] **Step 3:** Implement the tools and add the README and `TOOL_CALL_TESTS.md` sections. The test messages should cover passive learning with a 📝 reaction, a joke, a famous-name drop, a `that's ben` link, an admin link, recall, `!aboutme` remove/add/paging, a non-owner click, and deleting a message before a call.
- [ ] **Step 4:** Run `venv/Scripts/python.exe -m pytest -v`. Expected: all PASS.
- [ ] **Step 5:** Manual end-to-end on a test server, after turning on the Server Members Intent in the Developer Portal. Run the `TOOL_CALL_TESTS.md` Memories section and check the logs for `Tool call:` lines and learner op counts.
- [ ] **Step 6:** Commit: `git commit -m "feat: memory chat tools and docs"`
