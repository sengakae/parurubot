import asyncio
import logging
import re
from datetime import datetime, timedelta, timezone

from discord.ext import commands

import history
from utils.ai import generate_quiz_question, summarize_channel

logger = logging.getLogger(__name__)


class GeneralCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="purge", help="Purges the last [number] lines (max: 100)")
    async def purge(self, ctx, number: int = 10):
        lines = min(int(number), 100)
        await ctx.channel.purge(limit=lines)

    @commands.command(name="clear", help="Clears stored AI history for this channel")
    async def clear(self, ctx):
        if ctx.guild is None:
            await ctx.send("This command can only be used in a server.")
            return

        history.reset_history(ctx.channel.id)
        logger.info("Channel history cleared.")
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

    @commands.command(name="v")
    async def quiz(self, ctx, level: str, category: str):
        """
        Generates a language quiz.
        
        Parameters:
        level: The level you want to study (e.g., n1-n5, topik1-6, or hsk1-9)
        category: The type of question (vocab, grammar, or reading)
        """
        level = level.lower()
        category = category.lower()

        valid_levels = [
            'n1', 'n2', 'n3', 'n4', 'n5', 
            'topik1', 'topik2', 'topik3', 'topik4', 'topik5', 'topik6',
            'hsk1', 'hsk2', 'hsk3', 'hsk4', 'hsk5', 'hsk6', 'hsk7', 'hsk8', 'hsk9'
        ]
        
        if level not in valid_levels:
            return await ctx.send(f"**{level}** is not a valid level. Please use N1-5, TOPIK1-6, or HSK1-9.")
        
        emoji_map = {"A": "🇦", "B": "🇧", "C": "🇨", "D": "🇩"}
        reverse_map = {v: k for k, v in emoji_map.items()}

        loading_msg = await ctx.send(f"Generating a **{level.upper()} {category}** question...")

        try:
            q_data = await asyncio.to_thread(generate_quiz_question, level, category)

            if not q_data or 'question' not in q_data:
                raise ValueError("Incomplete data received from AI")

            quiz_text = (
                f"**{level.upper()} {category.capitalize()} Quiz**\n"
                f"```\n{q_data['question']}\n```\n"
                f"**A)** {q_data['options']['A']}\n"
                f"**B)** {q_data['options']['B']}\n"
                f"**C)** {q_data['options']['C']}\n"
                f"**D)** {q_data['options']['D']}\n\n"
            )
            
            await loading_msg.edit(content=quiz_text)
            for emoji in emoji_map.values():
                await loading_msg.add_reaction(emoji)

            def check(reaction, user):
                return (
                    not user.bot and 
                    str(reaction.emoji) in emoji_map.values() and 
                    reaction.message.id == loading_msg.id
                )

            try:
                reaction, user = await self.bot.wait_for("reaction_add", timeout=60.0, check=check)
                user_choice = reverse_map[str(reaction.emoji)]

                if user_choice == q_data['correct']:
                    result_msg = f"**Correct!** The answer was **{q_data['correct']}**."
                else:
                    result_msg = f"**Incorrect.** The correct answer was **{q_data['correct']}**."

                await ctx.send(f"{ctx.user.mention} {result_msg}\n> {q_data['explanation']}")

            except asyncio.TimeoutError:
                await ctx.send(f"Time's up! The answer was **{q_data['correct']}**.")

        except Exception as e:
            logger.exception(f"Quiz Error: {e}")
            await loading_msg.edit(content="Failed to generate a question. Please try again.")

async def setup(bot):
    await bot.add_cog(GeneralCog(bot))
