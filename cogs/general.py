import logging

from discord.ext import commands

from utils.ai import summarize_channel

logger = logging.getLogger(__name__)


class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="purge", help="Purges the last [number] lines (max: 100)")
    async def purge(self, ctx, number: int = 10):
        lines = min(int(number), 100)
        await ctx.channel.purge(limit=lines)

    @commands.command(name="summary", help="Generate AI summary of last 500 messages")
    async def summary(self, ctx):
        try:
            if not ctx.channel.permissions_for(ctx.author).read_message_history:
                await ctx.send(
                    "You don't have permission to read message history in this channel"
                )
                return

            await ctx.send(
                "Analyzing the last 500 messages... this might take a moment"
            )

            messages = []
            async for message in ctx.channel.history(limit=500):
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
    await bot.add_cog(GeneralCog(bot))
