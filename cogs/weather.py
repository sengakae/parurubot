import os

import discord
import requests
from discord.ext import commands

weather_url = "http://api.openweathermap.org/data/2.5/weather?"

WEATHER_TOKEN = os.getenv("WEATHER_TOKEN")


class WeatherCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @commands.command(name="weather", help="prints weather information of city")
    async def weather(self, ctx, *, city: str):
        url = f"{weather_url}appid={WEATHER_TOKEN}&q={city}"
        try:
            response = requests.get(url)
            data = response.json()

            channel = ctx.message.channel
            if data.get("cod") == 200:
                async with channel.typing():
                    weather = data["main"]
                    current_temp = str(round(weather["temp"] - 273.15))
                    current_humidity = weather["humidity"]
                    description = data["weather"][0]
                    country = data["sys"]["country"]
                    description_main = description["main"]
                    description_info = description["description"]

                    embed = discord.Embed(
                        title=f"Weather in {city.capitalize()}, {country}",
                        color=ctx.guild.me.top_role.color,
                        timestamp=ctx.message.created_at,
                    )
                    embed.add_field(
                        name="Description",
                        value=f"**{description_main} - {description_info}**",
                        inline=False,
                    )
                    embed.add_field(
                        name="Temperature", value=f"**{current_temp}°C**", inline=False
                    )
                    embed.add_field(
                        name="Humidity", value=f"**{current_humidity}%**", inline=False
                    )
                    embed.set_footer(text=f"Requested by {ctx.author.display_name}")

                    await ctx.send(embed=embed)
            else:
                await ctx.send("City not found.")
        except:
            await ctx.send("An error occurred while fetching weather info.")


async def setup(bot):
    await bot.add_cog(WeatherCog(bot))
