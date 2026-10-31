import os

os.environ.setdefault("OWNER_ID", "1")

import pytest  # noqa: E402

import cogs.update as up  # noqa: E402

STEPS = ["Pulling latest changes", "Checking for changes", "Restarting service"]


def test_checklist_markers():
    e = up.build_update_embed(["Pulling latest changes"], "Checking for changes", steps=STEPS)
    assert e.description == "✓ Pulling latest changes\n● Checking for changes...\n○ Restarting service"


@pytest.mark.parametrize("state, color, title", [
    ("running", 0x5865F2, "Updating paruru"),
    ("success", 0x57F287, "Update complete"),
    ("failed", 0xED4245, "Update failed"),
    ("uptodate", 0x99AAB5, "Already up to date"),
])
def test_state_colors_and_titles(state, color, title):
    e = up.build_update_embed([], steps=STEPS, state=state)
    assert e.colour.value == color
    assert e.title == title


def test_fields_in_order_with_version_shortened():
    e = up.build_update_embed(
        STEPS, steps=STEPS, state="success", old_hash="7d306ddabc", new_hash="c0765abdef",
        deps="Skipped (requirements.txt unchanged)", changes=" a.py | 2 +-",
    )
    assert [f.name for f in e.fields] == ["Version", "Dependencies", "Changes"]
    assert e.fields[0].value == "`7d306dd` -> `c0765ab`"
    assert e.fields[0].inline and e.fields[1].inline
    assert e.fields[1].value == "Skipped (requirements.txt unchanged)"
    # diffstat lines all start with a space; keep it so the first line stays aligned
    assert e.fields[2].value == "```diff\n a.py | 2 +-\n```"


def test_error_field_is_fenced():
    e = up.build_update_embed([], steps=STEPS, state="failed", error="fatal: not a repo")
    assert [f.name for f in e.fields] == ["Error"]
    assert e.fields[0].value == "```\nfatal: not a repo\n```"


def test_note_appended_under_checklist():
    e = up.build_update_embed([], steps=STEPS, state="failed", note="git pull timed out.")
    assert e.description.endswith("\n\ngit pull timed out.")


def test_footer_and_success_timestamp():
    assert up.build_update_embed([], steps=STEPS, started_by="sengakae").footer.text == "Started by sengakae"
    assert up.build_update_embed(STEPS, steps=STEPS, state="success").timestamp is not None
    assert up.build_update_embed([], steps=STEPS).timestamp is None


def test_long_changes_truncated_within_field_limit():
    e = up.build_update_embed(STEPS, steps=STEPS, changes="x" * 5000)
    assert len(e.fields[0].value) <= 1024
    assert e.fields[0].value.startswith("```diff\n") and e.fields[0].value.endswith("\n```")


def test_backticks_in_error_do_not_break_block():
    e = up.build_update_embed([], steps=STEPS, state="failed", error="bad ```oops``` here")
    assert e.fields[0].value.count("```") == 2


def test_empty_values_add_no_field():
    assert up.build_update_embed([], steps=STEPS, changes="  \n", error="", deps="").fields == []


# --- restart flag ---

import asyncio  # noqa: E402
import json  # noqa: E402
from types import SimpleNamespace as NS  # noqa: E402

FULL_FLAG = {"channel_id": 1, "message_id": 2, "steps": STEPS, "old_hash": "7d306ddabc",
             "changes": " a.py | 2 +-", "deps": "Skipped (requirements.txt unchanged)", "started_by": "sengakae"}


def test_final_embed_from_flag():
    e = up.final_embed_from_flag(FULL_FLAG, "c0765abdef")
    assert e.title == "Update complete" and e.colour.value == 0x57F287
    assert e.description == "\n".join(f"✓ {s}" for s in STEPS)
    # Changes only shows while the update runs; FULL_FLAG still carries it, as flags
    # written by the previous version do
    assert [f.name for f in e.fields] == ["Version", "Dependencies"]
    assert e.fields[0].value == "`7d306dd` -> `c0765ab`"
    assert e.footer.text == "Started by sengakae"


def test_final_embed_from_old_flag():
    e = up.final_embed_from_flag({"channel_id": 1, "message_id": 2, "steps": STEPS}, "c0765ab")
    assert e.title == "Update complete"
    assert e.description == "\n".join(f"✓ {s}" for s in STEPS)
    assert e.fields == []


def test_on_ready_edits_with_embed_and_clears_content(tmp_path, monkeypatch):
    flag = tmp_path / ".restart_flag.json"
    flag.write_text(json.dumps(FULL_FLAG))
    monkeypatch.setattr(up, "RESTART_FLAG_PATH", str(flag))
    monkeypatch.setattr(up, "run_git", lambda args: NS(stdout="c0765ab\n"))
    edits = []

    async def edit(**kwargs):
        edits.append(kwargs)

    async def fetch_message(message_id):
        return NS(edit=edit)

    bot = NS(get_channel=lambda channel_id: NS(fetch_message=fetch_message))
    asyncio.run(up.Update(bot).on_ready())
    assert edits[0]["content"] is None
    assert edits[0]["embed"].title == "Update complete"
    assert not flag.exists()


def test_error_tail_keeps_last_line():
    # pip prints the actual "ERROR: ..." line last; the head of 1200 chars of noise is useless
    pip_output = "Collecting stuff\n" * 70 + "ERROR: No matching distribution found for nope"
    e = up.build_update_embed([], steps=STEPS, state="failed", error=pip_output, error_keep="tail")
    assert len(e.fields[0].value) <= 1024
    assert "ERROR: No matching distribution found for nope" in e.fields[0].value


# --- update() flow ---

import discord  # noqa: E402


def run_update(monkeypatch, tmp_path, edit):
    monkeypatch.setattr(up, "RESTART_FLAG_PATH", str(tmp_path / ".restart_flag.json"))
    hashes = iter(["7d306ddabc", "c0765abdef"])

    def run_git(args):
        if args == ["pull"]:
            return NS(returncode=0, stderr="", stdout="")
        if args[:2] == ["rev-parse", "HEAD"]:
            return NS(stdout=next(hashes) + "\n")
        if args[:2] == ["diff", "--stat"]:
            return NS(stdout=" a.py | 2 +-\n")
        return NS(stdout="a.py\n")

    restarts = []
    monkeypatch.setattr(up, "run_git", run_git)
    monkeypatch.setattr(up.subprocess, "Popen", lambda cmd, *a, **k: restarts.append(cmd))

    async def no_sleep(_):
        pass

    monkeypatch.setattr(up.asyncio, "sleep", no_sleep)

    async def send(**kwargs):
        return NS(id=99, edit=edit)

    ctx = NS(author=NS(id=up.OWNER_ID, display_name="sengakae"), channel=NS(id=5), send=send)
    asyncio.run(up.Update.update.callback(up.Update(None), ctx))
    return restarts


def test_restart_happens_even_if_status_edit_fails(monkeypatch, tmp_path):
    async def edit(**kwargs):
        raise discord.NotFound(NS(status=404, reason="Not Found"), "Unknown Message")

    restarts = run_update(monkeypatch, tmp_path, edit)
    assert restarts == [["sudo", "systemctl", "restart", "parurubot.service"]]
    assert (tmp_path / ".restart_flag.json").exists()
