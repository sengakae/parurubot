import discord
import json
import random
import requests
import os
import re
import google.generativeai as genai

from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
WEATHER_TOKEN = os.getenv("WEATHER_TOKEN")
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY')

# AI configs
genai.configure(api_key=GEMINI_API_KEY)
model = genai.GenerativeModel('gemini-pro')
char_limit = 2000
history_clients = {}

weather_url = "http://api.openweathermap.org/data/2.5/weather?"

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

# Add all commands into this list
command_list = ['rquote', 'purge', 'weather', 'add', 'rm', 'showquotes', 'gs']

@bot.event
async def on_ready():
    server_count = 0

    for server in bot.guilds:
        print(f"- {server.id} (name: {server.name})")

        server_count = server_count + 1

    print("ParuruBot is in " + str(server_count) + " server(s).")

@bot.command(name="rquote", help="randomly prints a saved quote")
async def rquote(ctx):
    try:
        with open("quotes.json", "r") as r:
            data = json.load(r)
    except:
        await ctx.send("No quotes found.")
        return
    
    await ctx.send(random.choice(list(data.values())))

@bot.command(name="purge", help="purges the last [number] lines (max: 100)")
async def purge(ctx, number = 10):
    lines = int(number)
    await ctx.channel.purge(limit=lines)

@bot.command(name="weather", help="prints weather information of city")
async def weather(ctx, *, city: str):
    url = f"{weather_url}appid={WEATHER_TOKEN}&q={city}"
    try:
        response = requests.get(url)
        data = response.json()

        # Show bot typing while awaiting response
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

                embed = discord.Embed(title=f"Weather in {city.capitalize()}, {country}",
                                    color=ctx.guild.me.top_role.color,
                                    timestamp=ctx.message.created_at,)
                embed.add_field(name="Description", value=f"**{description_main} - {description_info}**", inline=False)
                embed.add_field(name="Temperature", value=f"**{current_temp}°C**", inline=False)
                embed.add_field(name="Humidity", value=f"**{current_humidity}%**", inline=False)
                embed.set_footer(text=f"Requested by {ctx.author.display_name}")

                await ctx.send(embed=embed)
        else:
            await ctx.send("City not found.")
    except:
        await ctx.send("An error occurred while fetching weather info.")

@bot.command(name="gs", help="calculates e7 gear score")
async def gs(ctx, *, values):
    stats = values.split()
    gear_score = 0
    for stat in stats:
        if isinstance(stat, str):
            num_val = int(re.search(r'\d+', stat).group())
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

@bot.command(name="add", help="saves the following string as a quote")
async def add(ctx, keyword, *, quote):
    def add_quote(quote, file="quotes.json"):
        with open(file, "r+") as fw:
            data = json.load(fw)
            data[keyword] = quote
            with open(file, "w+") as wp:
                wp.write(json.dumps(data))

    try:
        with open("quotes.json", "r"):
            pass
    except:
        with open("quotes.json", "w+") as wp:
            wp.write("{}")
    finally:
        add_quote(quote)
        await ctx.send(f"Added: {quote}")

@bot.command(name="rm", help="deletes quote with keyword identifier")
async def rm(ctx, keyword):
    async def rm_quote(file="quotes.json"):
        with open(file, "r+") as fw:
            data = json.load(fw)
            if keyword in data:
                del data[keyword]
                with open(file, "w+") as wp:
                    wp.write(json.dumps(data))
                await ctx.send(f'"{keyword}" quote deleted.')
                return
            else:
                await ctx.send("Quote not found.")
                return

    try:
        with open("quotes.json", "r"):
            pass
    except:
        await ctx.send("quotes.json not found.")
        return
    finally:
        await rm_quote()
        return
    
@bot.command(name="showquotes", help="list all quote keywords")
async def showquotes(ctx):
    try:
        with open("quotes.json", "r") as file:
            data = json.load(file)

        if not data:
            await ctx.send("No quotes found.")
            return
        else:
            keywords = list(data.keys())
            keyword_list = "\n".join(keywords)
            output = f"```plaintext\n{keyword_list}```"
            await ctx.send(f"List of quote keywords:\n{output}")
    except:
        await ctx.send("quotes.json not found.")
        return

@bot.event
async def on_message(ctx):
    if ctx.author == bot.user:
        return
    
    content = ctx.content.split()

    # Respond using !commands
    if content[0].startswith("!"):
        keyword = content[0][1:]
        if keyword in command_list:
            await bot.process_commands(ctx)
            return
        else:
            with open("quotes.json", "r") as fw:
                quotes = json.load(fw)
                if keyword in quotes:
                    await ctx.channel.send(quotes[keyword])
    
    # Add ai chatbot on @mention
    elif bot.user.mentioned_in(ctx):
        if ctx.author not in history_clients:
            history_clients[ctx.author] = []

        mention = f"@{bot.user.name}"
        cleaned_content = ctx.clean_content.replace(mention, "").strip()

        try:
            print(f"Prompt: {cleaned_content}")
            chat = model.start_chat(history=history_clients[ctx.author])
            response = chat.send_message(cleaned_content)
            print(f"Response: {response.text}")
            history_clients[ctx.author] += chat.history
            if len(response.text) > char_limit:
                await ctx.channel.send("Too much thinking, I'm going to bed.")
            else:
                await ctx.channel.send(response.text)
        except Exception as e:
            print(f"Error generating response: {e}")

bot.run(DISCORD_TOKEN)