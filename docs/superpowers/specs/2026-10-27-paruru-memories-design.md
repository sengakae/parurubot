# Paruru Memories: Design

## Intent

Paruru builds a shared, playful memory of the people in the server. Everyone contributes, mostly without trying: paruru picks up facts (and running jokes) from normal conversation, about the speaker or about other people by name. People can ask paruru about someone ("where is ben going?") and manage what she knows about them with `!aboutme`.

The fun is that everyone can add notes about anyone else. Correctness is secondary to being lively, but each person stays in control of their own profile.

## Goals

- Learn passively from channel conversation, not only from `paruru,` chats.
- Memories about a **name** are saved even before that name is tied to a Discord account.
- Names get linked to Discord accounts by paruru (from conversation) or by the admin; a link takes effect immediately and merges that name's memories into the account.
- Jokes are kept, flagged as jokes, and brought up playfully, never stated as fact.
- `!aboutme` shows and edits your own memories (layout B, below). A chat tool shows the same thing.
- In chat, paruru recalls what she knows when asked about a person.
- A message that produced a memory gets a 📝 reaction.
- Cheap: each message is read by the learner once, in batches.

## Non-goals

- No `!aboutme @someone`. Nobody can view or edit another person's memories through commands.
- No confirmation step before a link applies.
- No memories about people outside the server (celebrities, game characters).
- Not storing raw messages. Only distilled memories are saved.
- No moving the database to a hosted provider as part of this (separate effort; the design must not prevent it).

## Anti-goals (failure even if it works)

- A memory survives after its source message was deleted before processing.
- A joke gets presented by paruru as a real fact.
- One person can delete or edit another person's memories via `!aboutme`.
- A wrong link can't be undone.

## Decisions

### Data model (Postgres, same `parurubot` database)

Created in `db.init_db()` with `CREATE TABLE IF NOT EXISTS`, like the existing tables.

`memories`
| Column | Notes |
|---|---|
| `id` | serial PK; used by the remove dropdown |
| `guild_id` | server the memory belongs to |
| `user_id` | Discord ID the memory is filed under; NULL while filed under a name only |
| `name` | the name the memory was said under (casefolded), e.g. `ben`; NULL for self-statements. Kept after a merge so a link can be undone |
| `kind` | `fact` or `name` (a name link is itself a memory) |
| `content` | memory text, e.g. `going to japan in march`; for `kind = name`, the name itself |
| `joke` | boolean |
| `added_by` | Discord ID of who said it (or the owner, via the Add button; or the admin) |
| `source_message_id` | message it came from, if any |
| `created_at`, `updated_at` | |

Name links are the `kind = 'name'` rows that have a `user_id`. A unique index on `(guild_id, content) WHERE kind = 'name'` enforces "no two people share a name".

### Who a memory is about

- **Self-statements** ("i'm moving to osaka"): filed under the speaker's Discord ID. No name needed.
- **About a name** ("ben is going to japan"):
  - If `ben` is linked: filed under the linked Discord ID, with `name = 'ben'`.
  - Otherwise: filed under the name only (`user_id` NULL).
- **Is the name someone in the server?** Keep it only if at least one is true: it is linked; it matches a current server member's nickname, display name, or username (casefolded; this is a signal only and does **not** create a link); or people address that name directly in chat. Otherwise drop it.
- **Server member vs famous person with the same name:** the model weighs, in order: the name belongs to someone here; how it is written (first name lowercase vs full name/surname); whether that person is part of the conversation; whether the claim is true of a well-known person. **When still unsure, save it to the member** (they can remove it).

### Linking and merging

- A link is created when the learner sees a cue ("that's ben", "i'm ben", replying "ben" to someone), or when the admin says it in chat (`paruru, @xCooki3s is ben`).
- Applies immediately. On link: insert the `name` memory for that user, then `UPDATE memories SET user_id = <id> WHERE guild_id = ? AND name = 'ben' AND user_id IS NULL`.
- After a link, new memories about `ben` are filed directly under the linked ID.
- **Unlink** (removing the name memory in `!aboutme`): set `user_id = NULL` on that user's memories whose `name` is the removed name, moving them back under the name.
- A learner link op for a name already linked to a different user is rejected and logged.
- **Admin override** (builder's call): the admin's `link_name` may reassign a name already linked to someone else. It unlinks from the old user (memories with that `name` move back under the name) and then links to the new one. This plus the owner's own `!aboutme` removal is how a wrong link gets fixed.
- **Locked names:** `sengakae` is permanently bound to `PARTNER_USER_ID` (from `config.py`, already the source of the "verified" label in `speaker_label`). Keep locked names in a small mapping in code. The link is ensured at startup, cannot be removed via `!aboutme`, and any learner or admin op that would link a locked name to a different ID is rejected. Anything said about "sengakae" always files under that ID.
- Admin = the `OWNER_ID` in `.env` (same check as `!update`).
- **Removals don't stick** (user decision): a removed memory or link can be learned again if someone says it again. No tombstones.

### Background learner (approach 1: one structured call per batch)

- New cog keeps a per-channel **pending queue** of guild text messages: message ID, author ID, author display name, content, mentioned user IDs, replied-to message ID and author ID, timestamp. It is separate from `history.py` (which stays as is).
- Queued: every non-bot, non-command message with text, including `paruru,` messages, from any channel or thread the bot can read. No channel filtering, and recall can surface memories from any channel (user decision).
- Author labels given to the learner use `speaker_label` from `main.py`, so impersonation of the partner is already marked.
- **Trigger (user decision): never on a timer or a count.** Pending messages are processed only when the bot is next called (a `paruru,` chat or a `!` command) in that server. Builder's calls:
  - one call processes every channel in that server with pending messages (one batch per channel), so channels where paruru is never called still get learned from
  - it runs in the background and does not delay the reply, so a fact said in the last few messages may not be recallable in that same reply
  - each channel's queue is capped at the last 100 messages (matching her 100-message history); older unprocessed messages are dropped
- **Holdback:** process all but the last 3 messages of each channel; they carry into the next run so replies and reactions to a claim are visible.
- **Processed marker:** each channel keeps the ID of its last processed message. After a run, everything up to that point is marked processed and is never sent to the model again; the next run starts after the marker. The queue and markers live in memory, so both reset on restart (unprocessed messages from before a restart are lost, nothing is reprocessed).
- Example: 40 messages arrive overnight, then someone says `paruru, morning`. Messages 1-37 are processed and marked, 38-40 wait. Later 10 more arrive and someone runs `!weather tokyo`: 38-47 are processed, 48-50 wait.
- Each batch call also includes a few already-processed messages before the batch as read-only context.
- **Edits:** `on_message_edit` (or raw) updates the queued content. **Deletes:** `on_raw_message_delete` removes it from the queue.
- **Model input:** the batch (with IDs), reactions on each message (fetched at processing time), the server members' names, current links, and existing memories for every name/ID that appears in the batch (with memory IDs).
- **Model output:** structured JSON (response schema), a list of ops. Every op carries the `message_id` that caused it:
  - `add {message_id, subject: self|name, name?, content, joke}`
  - `update {message_id, memory_id, content, joke}`
  - `forget {message_id, memory_id}`
  - `link {message_id, name, user_id}` (user_id must be one of the IDs present in the batch)
- **Applied in code**, which enforces every rule:
  - drop any op whose `message_id` is no longer pending (deleted mid-call), so a deleted message never causes any change
  - resolve self/name/link as above, and check the server-member rule
  - reject link conflicts and locked-name violations
  - validate memory IDs belong to the guild
  - **owner-added memories are protected** (builder's call): `update`/`forget` are rejected on rows where `added_by` equals `user_id` and the op's message author is someone else
- **Reaction:** add 📝 to the source message for every applied `add`, `update`, and `link`. Not for `forget`. Failures (deleted message, missing permission) are ignored.
- **Model:** the same model as chat (`gemini-2.5-flash`, via `utils.ai.MODEL`), no personality prompt. Flash-Lite models were unavailable to this API key.
- Pending queue lives in memory; whatever is pending at restart is lost (my call: acceptable at this scale).

### `!aboutme` (layout B)

- No arguments; shows the invoker's memories for the current server: everything with `user_id = invoker`.
- Embed titled like "what i know about <display name>", numbered list; name links shown (e.g. `goes by ben`). Jokes look like any other memory: the joke flag is never shown in the embed or the remove dropdown, it only affects how paruru talks about them.
- Always-visible "Remove a memory..." select (up to 25 options = the current page); picking removes immediately and refreshes the embed. Removing a `name` memory performs the unlink above.
- "Add memory" button opens a modal; saved as a self memory with `added_by` = owner.
- ◀ ▶ page buttons when more than 25 memories.
- Only the owner can use the components; anyone else gets an ephemeral "this isn't yours".
- The message is posted in the channel (not ephemeral).

### Chat tools (added to `utils/tools.py`)

- `recall_person(name)`: resolves `me`/the speaker or a name (linked → that ID's memories; unlinked → name-only memories). Returns memories with joke flags. Guidance: use when asked about a person; bring jokes up playfully, never as fact.
- `show_my_memories()`: posts the `!aboutme` embed for the speaker.
- `link_name(name)`: admin only; the user comes from the first non-bot @mention in the triggering message. Non-admins get an error result.

## Approaches considered

1. **One structured call per batch, applied by code (chosen).** One round trip per batch; all rules (member check, links, merges, deletions, ownership) enforced in code.
2. **Batch via tool calls like chat.** Reuses the tool loop, but several round trips per batch and rules checked call by call, which makes the deletion and merge cases messier.
3. **Store memories in ChromaDB.** Dropped: no uniqueness on names, awkward listing/paging, and the user wants Postgres.

The user chose 1.

## Constraints

- Discord: 25 options per select, 5 component rows per message, 4096-char embed description.
- **Server Members Intent** must be enabled in the Discord Developer Portal and `intents.members = True`, or the member-name list is empty.
- The bot needs Add Reactions permission for 📝.
- `config.py` is gitignored and deployed by hand; keep new prompts in code.
- Chat replies are lowercased (Discord timestamps preserved).
- Gemini 2.5 Flash; chat already uses a manual tool loop.

## Left to the builder

- Exact prompts for the learner and the tool guidance.
- Embed styling, page size handling, view persistence/timeouts (existing persistent-view pattern in `cogs/signup.py` / `cogs/tasklist.py` is available).
- How many already-processed messages to include as context; exact holdback (3 suggested).
- Index choices beyond the unique name index.
- Logging detail.
