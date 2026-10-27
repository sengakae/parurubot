import asyncio

import discord
from discord.ext import commands

from utils.weather import fetch_weather


class WeatherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="weather", help="prints weather information of city")
    async def weather(self, ctx, *, city: str):
        try:
            weather = await asyncio.to_thread(fetch_weather, city)

            if weather is not None:
                async with ctx.message.channel.typing():
                    embed = discord.Embed(
                        title=f"Weather in {weather['city']}, {weather['country']}",
                        color=ctx.guild.me.top_role.color,
                        timestamp=ctx.message.created_at,
                    )
                    embed.add_field(
                        name="Description",
                        value=f"**{weather['main']} - {weather['description']}**",
                        inline=False,
                    )
                    embed.add_field(
                        name="Temperature",
                        value=f"**{weather['temp_c']}°C**",
                        inline=False,
                    )
                    embed.add_field(
                        name="Humidity",
                        value=f"**{weather['humidity']}%**",
                        inline=False,
                    )
                    embed.set_footer(text=f"Requested by {ctx.author.display_name}")

                    await ctx.send(embed=embed)
            else:
                await ctx.send("City not found.")
        except:
            await ctx.send("An error occurred while fetching weather info.")


async def setup(bot):
    await bot.add_cog(WeatherCog(bot))
