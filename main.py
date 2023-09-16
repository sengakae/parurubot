import discord
import json
import random
import os

from dotenv import load_dotenv
from discord.ext import commands

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = commands.Bot(command_prefix="!", intents=intents)

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
            quotes = data["quotes"]
    except:
        await ctx.send("No quotes found.")
        return
    
    await ctx.send(random.choice(quotes))

@bot.command(name="quote", help="prints the saved quote")
async def quote(ctx, number):
    try:
        with open("quotes.json", "r") as r:
            data = json.load(r)
            quotes = data["quotes"]
    except:
        await ctx.send("No quotes found.")
        return
    
    number = int(number)
    if 0 < number < len(quotes):
        await ctx.send(quotes[number - 1])
    else:
        await ctx.send("Invalid number.")

@bot.command(name="add", help="saves the following string as a quote")
async def add(ctx, quote):
    def add_quote(quote, file="quotes.json"):
        with open(file, "r+") as fw:
            j = json.load(fw)
            j["quotes"].append(quote)
            with open(file, "w+") as wp:
                wp.write(json.dumps(j))

    try:
        with open("quotes.json", "r"):
            pass
    except:
        with open("quotes.json", "w+") as wp:
            wp.write('{"quotes" : []}')
    finally:
        add_quote(quote)
        await ctx.send(f"Added: {quote}")

@bot.command(name="purge", help="purges the last [number] lines (max: 100)")
async def purge(ctx, number):
    lines = int(number)
    await ctx.channel.purge(limit=lines)

bot.run(DISCORD_TOKEN)