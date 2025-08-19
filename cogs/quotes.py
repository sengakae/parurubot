from discord.ext import commands

from db import add_quote, get_all_keys, get_quote_by_key, get_random_quote, remove_quote


class QuoteCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="add", help="Add a quote")
    async def add(self, ctx, keyword: str, *, quote: str):
        await add_quote(keyword, quote)
        await ctx.send(f"Quote '{keyword}' added successfully!")

    @commands.command(name="rm", help="Remove a quote by keyword")
    async def rm(self, ctx, keyword: str):
        removed = await remove_quote(keyword)
        if removed:
            await ctx.send(f"Quote '{keyword}' removed successfully!")
        else:
            await ctx.send(f"No quote found for keyword '{keyword}'.")

    @commands.command(name="showquotes", help="List all quote keywords")
    async def showquotes(self, ctx):
        keywords = await get_all_keys()
        if keywords:
            output = "```plaintext\n" + "\n".join(keywords) + "\n```"
            await ctx.send(f"All quote keywords:\n{output}")
        else:
            await ctx.send("No quotes found in the database.")

    @commands.command(name="rquote", help="Print a random saved quote")
    async def rquote(self, ctx):
        quote_row = await get_random_quote()
        if quote_row:
            await ctx.send(f"**{quote_row['key']}**: {quote_row['value']}")
        else:
            await ctx.send("No quotes found in the database.")

    @commands.command(name="quote", help="Show a quote by its keyword")
    async def quote(self, ctx, keyword: str):
        value = await get_quote_by_key(keyword)
        if value:
            await ctx.send(f"**{keyword}**: {value}")
        else:
            await ctx.send(f"No quote found for keyword '{keyword}'.")


async def setup(bot):
    await bot.add_cog(QuoteCog(bot))
