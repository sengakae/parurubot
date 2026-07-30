import asyncio
import os
import subprocess

from discord.ext import commands

OWNER_ID = int(os.getenv("OWNER_ID"))
REPO_PATH = os.getenv("REPO_PATH", "/home/sengakae/parurubot")


def run_git(args):
    return subprocess.run(
        ["git"] + args,
        cwd=REPO_PATH,
        capture_output=True,
        text=True,
        timeout=30,
    )


class Update(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="update")
    async def update(self, ctx):
        if ctx.author.id != OWNER_ID:
            return

        await ctx.send("Pulling latest changes...")

        before = run_git(["rev-parse", "HEAD"])
        old_hash = before.stdout.strip()

        try:
            pull_result = run_git(["pull"])
        except subprocess.TimeoutExpired:
            await ctx.send("`git pull` timed out.")
            return

        if pull_result.returncode != 0:
            await ctx.send(f"git pull failed:\n```{pull_result.stderr[:1900]}```")
            return

        after = run_git(["rev-parse", "HEAD"])
        new_hash = after.stdout.strip()

        if old_hash == new_hash:
            await ctx.send("Already up to date — no changes.")
            return

        diffstat = run_git(["diff", "--stat", f"{old_hash}..{new_hash}"])
        stat_output = diffstat.stdout.strip() or "(no diffstat available)"

        await ctx.send(
            f"Updated `{old_hash[:7]}` → `{new_hash[:7]}`:\n```{stat_output[:1900]}```"
        )

        await ctx.send("Restarting service...")
        await asyncio.sleep(1)
        subprocess.Popen(["sudo", "systemctl", "restart", "parurubot.service"])


async def setup(bot):
    await bot.add_cog(Update(bot))