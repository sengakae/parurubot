import asyncio
import json
import os
import platform
import subprocess

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


def render_steps(done_steps, current_step=None, extra_lines=None, steps=None):
    """Build the status block: [x] for done, [~] for current, [ ] for pending."""
    lines = []
    for step in steps or STEPS:
        if step in done_steps:
            lines.append(f"[x] {step}")
        elif step == current_step:
            lines.append(f"[~] {step}...")
        else:
            lines.append(f"[ ] {step}")
    block = "\n".join(lines)
    if extra_lines:
        block += "\n\n" + extra_lines
    return block


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
                final_block = render_steps(
                    data.get("steps", STEPS),
                    extra_lines=f"Restart complete — now on `{new_hash}`",
                )
                await message.edit(content=final_block)
        except Exception as e:
            print(f"Failed to send restart notification: {e}")
        finally:
            os.remove(RESTART_FLAG_PATH)

    @commands.command(name="update")
    async def update(self, ctx):
        if ctx.author.id != OWNER_ID:
            return

        status_msg = await ctx.send(render_steps([], current_step="Pulling latest changes"))

        before = run_git(["rev-parse", "HEAD"])
        old_hash = before.stdout.strip()

        try:
            pull_result = run_git(["pull"])
        except subprocess.TimeoutExpired:
            await status_msg.edit(
                content=render_steps([], extra_lines="git pull timed out.")
            )
            return

        if pull_result.returncode != 0:
            await status_msg.edit(
                content=render_steps(
                    [],
                    extra_lines=f"git pull failed:\n```{pull_result.stderr[:1500]}```",
                )
            )
            return

        await status_msg.edit(
            content=render_steps(
                ["Pulling latest changes"], current_step="Checking for changes"
            )
        )

        after = run_git(["rev-parse", "HEAD"])
        new_hash = after.stdout.strip()

        if old_hash == new_hash:
            await status_msg.edit(
                content=render_steps(
                    ["Pulling latest changes", "Checking for changes"],
                    extra_lines="Already up to date — no changes.",
                )
            )
            return

        diffstat = run_git(["diff", "--stat", f"{old_hash}..{new_hash}"])
        stat_output = diffstat.stdout.strip() or "(no diffstat available)"

        changed_files = run_git(
            ["diff", "--name-only", f"{old_hash}..{new_hash}"]
        )
        dependencies_changed = "requirements.txt" in changed_files.stdout.splitlines()
        update_steps = STEPS if dependencies_changed else [
            step for step in STEPS if step != "Installing dependencies"
        ]

        done_steps = ["Pulling latest changes", "Checking for changes"]
        if dependencies_changed:
            venv_python = get_venv_python()
            if not os.path.isfile(venv_python):
                await status_msg.edit(
                    content=render_steps(
                        done_steps,
                        extra_lines=(
                            "Project virtual environment not found; service was not restarted. "
                            f"Expected Python at `{venv_python}`."
                        ),
                        steps=update_steps,
                    )
                )
                return

            await status_msg.edit(
                content=render_steps(
                    done_steps,
                    current_step="Installing dependencies",
                    steps=update_steps,
                )
            )
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
                await status_msg.edit(
                    content=render_steps(
                        done_steps,
                        extra_lines="Dependency installation timed out; service was not restarted.",
                        steps=update_steps,
                    )
                )
                return

            if install_process.returncode != 0:
                error_output = (stderr or stdout).decode(errors="replace")
                await status_msg.edit(
                    content=render_steps(
                        done_steps,
                        extra_lines=(
                            "Dependency installation failed; service was not restarted:\n"
                            f"```\n{error_output[-1200:]}\n```"
                        ),
                        steps=update_steps,
                    )
                )
                return
            done_steps.append("Installing dependencies")

        await status_msg.edit(
            content=render_steps(
                done_steps,
                current_step="Restarting service",
                extra_lines=(
                    f"Updated `{old_hash[:7]}` -> `{new_hash[:7]}`:\n"
                    f"```diff\n{stat_output[:1200]}\n```"
                    + (
                        "\nDependency installation skipped (requirements.txt unchanged)."
                        if not dependencies_changed
                        else ""
                    )
                ),
                steps=update_steps,
            )
        )

        with open(RESTART_FLAG_PATH, "w") as f:
            json.dump(
                {
                    "channel_id": ctx.channel.id,
                    "message_id": status_msg.id,
                    "steps": update_steps,
                },
                f,
            )

        await asyncio.sleep(1)
        subprocess.Popen(["sudo", "systemctl", "restart", "parurubot.service"])


async def setup(bot):
    await bot.add_cog(Update(bot))