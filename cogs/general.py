import logging
import re
from datetime import datetime, timedelta, timezone

from discord.ext import commands

import history
from utils.ai import summarize_channel

logger = logging.getLogger(__name__)


class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="purge", help="Purges the last [number] lines (max: 100)")
    async def purge(self, ctx, number: int = 10):
        lines = min(int(number), 100)
        await ctx.channel.purge(limit=lines)

    @commands.command(name="clear", help="Clears stored AI history for this channel or all channels. Usage: !clear")
    async def clear(self, ctx):
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        history.reset_history(ctx.channel.id)
        await ctx.send(f"Cleared stored history for this channel ({ctx.channel.name}).")

    @commands.command(name="summary", help="Generate AI summary of last 500 messages")
    async def summary(self, ctx, arg: str = None):
        try:
            if not ctx.channel.permissions_for(ctx.author).read_message_history:
                await ctx.send(
                    "You don't have permission to read message history in this channel"
                )
                return
            
            limit = 100
            duration = None
            after = None

            if arg:
                match = re.match(r"^(\d+)([hd])$", arg)
                if match:
                    duration = int(match.group(1))
                    unit = match.group(2)

                    now = datetime.now(timezone.utc)
                    if unit == "h":
                        after = now - timedelta(hours=duration)
                        prompt = f"Analyzing messages from the last {duration} hours..."
                    else:
                        after = now - timedelta(days=duration)
                        prompt = f"Analyzing messages from the last {duration} days..."

                    limit = 500
                else:
                    limit = max(10, min(int(arg), 500))
                    prompt = f"Analyzing the last {limit} messages..."
            else:
                prompt = f"Analyzing the last {limit} messages..."

            await ctx.send(prompt)

            messages = []
            async for message in ctx.channel.history(limit=limit, after=after):
                if message.author != self.bot.user and message.content.strip():
                    messages.append(
                        {
                            "author": message.author.display_name,
                            "content": message.content,
                            "timestamp": message.created_at.isoformat(),
                        }
                    )

            if not messages:
                await ctx.send("No messages found to summarize")
                return

            summary_text = summarize_channel(messages)
            await ctx.send(
                f"**Channel Summary ({len(messages)} messages analyzed):**\n{summary_text}"
            )

        except Exception as e:
            logger.exception(f"Error generating summary: {e}")
            await ctx.send("Oops, something went wrong while generating the summary")


async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
