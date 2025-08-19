import re

from discord.ext import commands


class GSCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="gs", help="calculates e7 gear score")
    async def gs(self, ctx, *, values):
        stats = values.split()
        gear_score = 0
        for stat in stats:
            if isinstance(stat, str):
                num_val = int(re.search(r"\d+", stat).group())
                if "cc" in stat:
                    gear_score += num_val * 1.6
                elif "cd" in stat:
                    gear_score += num_val * 1.14
                elif "s" in stat:
                    gear_score += num_val * 2
                elif "atk" in stat:
                    gear_score += num_val * 3.46 / 39
                elif "def" in stat:
                    gear_score += num_val * 4.99 / 31
                elif "hp" in stat:
                    gear_score += num_val * 3.09 / 174
                else:
                    gear_score += int(stat)

        await ctx.send(f"Gear score: {round(gear_score, 2)}")


async def setup(bot):
    await bot.add_cog(GSCog(bot))
