import asyncio
import json
import os
import platform
import subprocess

import discord
from discord.ext import commands

OWNER_ID = int(os.getenv("OWNER_ID"))
REPO_PATH = os.getenv("REPO_PATH", "/home/sengakae/parurubot")
RESTART_FLAG_PATH = os.path.join(REPO_PATH, ".restart_flag.json")

STEPS = [
    "Pulling latest changes",
    "Checking for changes",
    "Installing dependencies",
    "Restarting service",
]


def run_git(args):
    return subprocess.run(
        ["git"] + args,
        cwd=REPO_PATH,
        capture_output=True,
        text=True,
        timeout=30,
    )


def get_venv_python():
    if platform.system() == "Windows":
        return os.path.join(REPO_PATH, "venv", "Scripts", "python.exe")
    return os.path.join(REPO_PATH, "venv", "bin", "python")


# edge color and title per state of the status embed
STATES = {
    "running": (0x5865F2, "Updating paruru"),
    "success": (0x57F287, "Update complete"),
    "failed": (0xED4245, "Update failed"),
    "uptodate": (0x99AAB5, "Already up to date"),
}
FIELD_LIMIT = 1024


def _clean(text):
    """Drop surrounding blank lines and trailing spaces, keep leading spaces (diffstat alignment)."""
    return (text or "").strip("\n").rstrip()


def _fenced(text, lang="", keep="head"):
    """Code block that fits one embed field; backtick fences inside the text can't close it early.
    keep="tail" cuts from the front instead, for output whose last lines matter most (pip)."""
    text = text.replace("```", "'''")
    room = FIELD_LIMIT - len(f"```{lang}\n\n```")
    if len(text) > room:
        text = "..." + text[-(room - 3):] if keep == "tail" else text[: room - 3] + "..."
    return f"```{lang}\n{text}\n```"


def build_update_embed(
    done_steps,
    current_step=None,
    *,
    steps=STEPS,
    state="running",
    old_hash=None,
    new_hash=None,
    deps=None,
    changes=None,
    error=None,
    error_keep="head",
    note=None,
    started_by=None,
):
    """The !update status message: a step checklist plus whichever details are known."""
    lines = []
    for step in steps:
        if step in done_steps:
            lines.append(f"✓ {step}")
        elif step == current_step:
            lines.append(f"● {step}...")
        else:
            lines.append(f"○ {step}")
    description = "\n".join(lines)
    if note:
        description += f"\n\n{note}"

    color, title = STATES[state]
    embed = discord.Embed(
        title=title,
        description=description[:4096],
        colour=color,
        timestamp=discord.utils.utcnow() if state == "success" else None,
    )
    if old_hash and new_hash:
        embed.add_field(name="Version", value=f"`{old_hash[:7]}` -> `{new_hash[:7]}`", inline=True)
    if _clean(deps):
        embed.add_field(name="Dependencies", value=_clean(deps)[:FIELD_LIMIT], inline=True)
    if _clean(changes).strip():
        embed.add_field(name="Changes", value=_fenced(_clean(changes), "diff"), inline=False)
    if _clean(error).strip():
        embed.add_field(name="Error", value=_fenced(_clean(error), keep=error_keep), inline=False)
    if started_by:
        embed.set_footer(text=f"Started by {started_by}")
    return embed


def final_embed_from_flag(data, new_hash):
    """The "Update complete" embed after the restart, from the flag the update wrote.
    Flags from before the embed only have the steps, so every other key is optional."""
    steps = data.get("steps", STEPS)
    return build_update_embed(
        steps,
        steps=steps,
        state="success",
        old_hash=data.get("old_hash"),
        new_hash=new_hash,
        deps=data.get("deps"),
        changes=data.get("changes"),
        started_by=data.get("started_by"),
    )


class Update(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.Cog.listener()
    async def on_ready(self):
        if not os.path.exists(RESTART_FLAG_PATH):
            return

        try:
            with open(RESTART_FLAG_PATH, "r") as f:
                data = json.load(f)
            channel = self.bot.get_channel(data["channel_id"])
            if channel:
                message = await channel.fetch_message(data["message_id"])
                new_hash = run_git(["rev-parse", "--short", "HEAD"]).stdout.strip()
                # content=None clears the plain-text checklist older versions posted
                await message.edit(content=None, embed=final_embed_from_flag(data, new_hash))
        except Exception as e:
            print(f"Failed to send restart notification: {e}")
        finally:
            os.remove(RESTART_FLAG_PATH)

    @commands.command(name="update")
    async def update(self, ctx):
        if ctx.author.id != OWNER_ID:
            return

        started_by = ctx.author.display_name

        async def show(done_steps, current_step=None, **details):
            # best effort: a deleted status message or a Discord error must not stop the update
            try:
                await status_msg.edit(
                    content=None,
                    embed=build_update_embed(done_steps, current_step, started_by=started_by, **details),
                )
            except discord.HTTPException as e:
                print(f"Failed to update !update status message: {e}")

        status_msg = await ctx.send(
            embed=build_update_embed([], "Pulling latest changes", started_by=started_by)
        )

        before = run_git(["rev-parse", "HEAD"])
        old_hash = before.stdout.strip()

        try:
            pull_result = run_git(["pull"])
        except subprocess.TimeoutExpired:
            await show([], state="failed", note="git pull timed out.")
            return

        if pull_result.returncode != 0:
            await show([], state="failed", note="git pull failed.", error=pull_result.stderr[:1500])
            return

        await show(["Pulling latest changes"], "Checking for changes")

        after = run_git(["rev-parse", "HEAD"])
        new_hash = after.stdout.strip()

        if old_hash == new_hash:
            await show(
                ["Pulling latest changes", "Checking for changes"],
                steps=["Pulling latest changes", "Checking for changes"],
                state="uptodate",
                note="No changes to pull.",
            )
            return

        diffstat = run_git(["diff", "--stat", f"{old_hash}..{new_hash}"])
        # rstrip only: every diffstat line starts with a space that keeps the columns aligned
        stat_output = diffstat.stdout.rstrip() or "(no diffstat available)"

        changed_files = run_git(
            ["diff", "--name-only", f"{old_hash}..{new_hash}"]
        )
        dependencies_changed = "requirements.txt" in changed_files.stdout.splitlines()
        update_steps = STEPS if dependencies_changed else [
            step for step in STEPS if step != "Installing dependencies"
        ]

        versions = {"old_hash": old_hash, "new_hash": new_hash}
        done_steps = ["Pulling latest changes", "Checking for changes"]
        if dependencies_changed:
            venv_python = get_venv_python()
            if not os.path.isfile(venv_python):
                await show(
                    done_steps,
                    steps=update_steps,
                    state="failed",
                    note=(
                        "Project virtual environment not found; service was not restarted. "
                        f"Expected Python at `{venv_python}`."
                    ),
                    **versions,
                )
                return

            await show(done_steps, "Installing dependencies", steps=update_steps, **versions)
            try:
                install_process = await asyncio.create_subprocess_exec(
                    venv_python,
                    "-m",
                    "pip",
                    "install",
                    "-r",
                    "requirements.txt",
                    cwd=REPO_PATH,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                stdout, stderr = await asyncio.wait_for(
                    install_process.communicate(), timeout=600
                )
            except asyncio.TimeoutError:
                install_process.kill()
                await install_process.communicate()
                await show(
                    done_steps,
                    steps=update_steps,
                    state="failed",
                    note="Dependency installation timed out; service was not restarted.",
                    **versions,
                )
                return

            if install_process.returncode != 0:
                error_output = (stderr or stdout).decode(errors="replace")
                await show(
                    done_steps,
                    steps=update_steps,
                    state="failed",
                    note="Dependency installation failed; service was not restarted.",
                    error=error_output[-1200:],
                    error_keep="tail",
                    **versions,
                )
                return
            done_steps.append("Installing dependencies")

        deps = (
            "Installed from requirements.txt"
            if dependencies_changed
            else "Skipped (requirements.txt unchanged)"
        )
        await show(
            done_steps,
            "Restarting service",
            steps=update_steps,
            deps=deps,
            changes=stat_output,
            **versions,
        )

        with open(RESTART_FLAG_PATH, "w") as f:
            json.dump(
                {
                    "channel_id": ctx.channel.id,
                    "message_id": status_msg.id,
                    "steps": update_steps,
                    "old_hash": old_hash,
                    "changes": stat_output,
                    "deps": deps,
                    "started_by": started_by,
                },
                f,
            )

        await asyncio.sleep(1)
        subprocess.Popen(["sudo", "systemctl", "restart", "parurubot.service"])


async def setup(bot):
    await bot.add_cog(Update(bot))