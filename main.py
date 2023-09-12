import discord
import os

from dotenv import load_dotenv

load_dotenv()

DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")

intents = discord.Intents.default()
intents.message_content = True

bot = discord.Client(intents=intents)

@bot.event
async def on_ready():
    server_count = 0

    for server in bot.guilds:
        print(f"- {server.id} (name: {server.name})")

        server_count = server_count + 1

    print("ParuruBot is in " + str(server_count) + " server(s).")

@bot.event
async def on_message(message):
    if message.author == bot.user:
        return
    
    user = str(message.author)
    content = str(message.content)
    print(user)
    if content == "hello":
        await message.channel.send(f"hey {user}")

bot.run(DISCORD_TOKEN)