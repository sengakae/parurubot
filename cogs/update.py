import asyncio
import os
import subprocess

from discord.ext import commands

OWNER_ID = int(os.getenv("OWNER_ID"))
REPO_PATH = os.getenv("REPO_PATH", "/home/sengakae/parurubot")


class Update(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="update")
    async def update(self, ctx):
        if ctx.author.id != OWNER_ID:
            return

        await ctx.send("Pulling latest changes...")

        try:
            result = subprocess.run(
                ["git", "pull"],
                cwd=REPO_PATH,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except subprocess.TimeoutExpired:
            await ctx.send("`git pull` timed out.")
            return

        if result.returncode != 0:
            await ctx.send(f"git pull failed:\n```{result.stderr[:1900]}```")
            return

        output = result.stdout.strip() or "Already up to date."
        await ctx.send(f"Pulled:\n```{output[:1900]}```")

        if "Already up to date." in output:
            await ctx.send("No changes — skipping restart.")
            return

        await ctx.send("Restarting service...")
        await asyncio.sleep(1)
        subprocess.Popen(["sudo", "systemctl", "restart", "parurubot.service"])


async def setup(bot):
    await bot.add_cog(Update(bot))