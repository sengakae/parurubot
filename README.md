# Parurubot

A feature-rich Discord bot built with Python and Discord.py, featuring AI-powered chat, personal notes management, and utility commands.

## Features

### AI-Powered Chat
- **Natural Conversations**: Chat naturally with "paruru, " followed by your message
- **Context Awareness**: Remembers the last 20 messages in each channel for contextual responses
- **Personal Notes Integration**: Searches through your personal notes to provide relevant information
- **Smart Response Types**: Automatically detects when to provide current information vs. conversational responses
- **Image Analysis**: Attach images to your messages for AI-powered analysis and description
- **YouTube Integration**: Share YouTube videos for AI discussion and analysis

### Personal Notes Management
- **Vector Database**: Automatically loads and indexes `.txt`, `.csv`, and `.json` files from the `notes/` folder
- **Semantic Search**: Finds relevant information from your notes using AI-powered search
- **Multiple Formats**: Supports both text files and CSV data with automatic chunking and indexing

### Utility Commands
- **Quote Management**: Save, retrieve, and manage quotes with keywords
- **Weather Information**: Get current weather data for any city
- **Gear Score Calculator**: Calculate Epic Seven gear scores with various stat types
- **Channel Management**: Purge messages and view conversation history
- **AI Summaries**: Generate intelligent summaries of channel conversations
- **Interactive Checklists**: Build a to-do list with checkboxes and numbered toggle buttons; lists stay interactive after a bot restart
- **Signup Sheets**: Create event signup lists with optional caps and +1 guests; sheets stay interactive after a bot restart
- **Reminders**: Schedule channel reminders with countdowns or datetimes with timezone abbreviations

## Quick Start

### Prerequisites
- Python 3.8+
- PostgreSQL database
- Discord Bot Token
- Google Gemini API Key
- OpenWeatherMap API Key

### Installation

1. **Clone the repository**

2. **Set up virtual environment**
   ```bash
   python -m venv venv
   # On Windows
   venv\Scripts\activate
   # On macOS/Linux
   source venv/bin/activate
   ```

3. **Install dependencies**
   ```bash
   pip install -r requirements.txt
   ```

4. **Environment Configuration**
   Create a `.env` file in the root directory:
   ```env
   DISCORD_TOKEN=your_discord_bot_token
   DATABASE_URL=postgresql://user:password@localhost:5432/discord_bot
   WEATHER_TOKEN=your_openweathermap_api_key
   GEMINI_API_KEY=your_gemini_api_key
   ```

5. **Database Setup**
   - Ensure PostgreSQL is running and `DATABASE_URL` is set in `.env`
   - On first run the bot creates tables for quotes, reminders, task lists, and signup sheets

6. **Run the bot**
   ```bash
   python main.py
   ```

## Project Structure

```
discord-bot/
├── cogs/                   # Bot command modules
│   ├── general.py         # General commands (purge, history, summary)
│   ├── weather.py         # Weather information commands
│   ├── gs.py             # Gear score calculator
│   ├── quotes.py         # Quote management commands
│   ├── tasklist.py       # Interactive checklist (persistent)
│   ├── signup.py         # Signup sheets (persistent)
│   └── remindme.py       # Scheduled reminders (persistent)
├── utils/                  # Utility modules
│   ├── ai.py             # AI chat and summarization
│   ├── notes.py          # Personal notes management
│   └── chroma_client.py  # Vector database client
├── notes/                  # Personal notes folder (auto-indexed)
├── chroma_db/             # Vector database storage
├── main.py                # Main bot entry point
├── config.py              # Configuration and system prompts
├── db.py                  # Database operations
├── history.py             # Message history management
└── requirements.txt       # Python dependencies
```

## Commands

### AI Chat
- **`paruru, [message]`** - Chat with the AI assistant

### Quote Management
- **`!add [keyword] [quote]`** - Save a quote with a keyword
- **`!rm [keyword]`** - Remove a quote by keyword
- **`!showquotes`** - List all quote keywords
- **`!rquote`** - Display a random saved quote
- **`!quote [keyword]`** - Display a specific quote by keyword
- **`![keyword]`** - Shortcut to display a specific quote by keyword

### Utility Commands
- **`!purge [number]`** - Delete the last N messages (max 100) [Requires admin permissions]
- **`!weather [city]`** - Get weather information for a city
- **`!gs [stats]`** - Calculate Epic Seven gear score
  - **Percentage stats**: `[num]atk`, `[num]def`, `[num]hp`, `[num]eff`, `[num]er`
  - **Critical stats**: `[num]cc`, `[num]cd`
  - **Speed**: `[num]s`
  - **Flat stats**: `[num]atk`, `[num]def`, `[num]hp`

### History & Analysis
- **`!summary [number|duration]`** - Generate AI summary of last N messages or messages from last X hours/days (e.g., `!summary 50`, `!summary 2h`, `!summary 1d`)
- **`!clear`** - Clear stored AI history for the current channel

### Language Learning Quiz
- **`!v [level] [category]`** - Generate a language quiz question
  - **Levels**: N1-N5 (JLPT), TOPIK1-6, HSK1-9
  - **Categories**: vocab, grammar, reading

### Interactive Checklist
- **`!tasklist`** - Open an interactive checklist builder, then publish a shared to-do list
  - **Add task** - Opens a modal to add one task at a time (up to 25 tasks)
  - **Edit a task** - Dropdown to pick a task and edit it before publishing
  - **Finish** - Publishes the checklist with numbered buttons to mark tasks complete
  - **Cancel** - Aborts without creating the list
  - Only the user who ran the command can use the builder controls
  - Published lists are saved to the database and keep working after the bot restarts
  - The creator gets an ephemeral **Edit list** panel to change tasks on a published list

### Signup Sheets
- **`!signup [cap] [title]`** - Create a signup sheet for an event or activity
  - Optional **cap** limits total headcount (signups + guests), e.g. `!signup 20 Game night`
  - **Guests (+1s)** - Dropdown to select 0–9 additional guests before signing up
  - **Sign up** - Adds your display name to the list (updates your +1 count if you sign up again)
  - **Leave** - Removes yourself from the list
  - **Delete sheet** - Removes the signup sheet (only the creator who ran `!signup`)
  - Sheets are saved to the database and keep working after the bot restarts

### Reminders
- **`!remindme <message> <time>`** - Schedule a reminder in the current channel
  - **Countdown**: `30m`, `2h15m`, `1d2h` (supports combinations of days, hours, minutes, and seconds)
  - **Datetime with timezone**: `YYYY-MM-DDTHH:MM[TZ]` (seconds are optional, e.g., `2026-07-25T16:00PDT` or `2026-09-12T09:30:00KST`)
  - **Supported Timezones**: 
    - Full ISO offsets (`Z` or `+00:00`)
    - Regional abbreviations mapped dynamically to geographical `ZoneInfo` standard locations (e.g., `PDT`/`PST` maps to `America/Los_Angeles`, `CEST`/`CET` to `Europe/Paris`, `JST` to `Asia/Tokyo`, etc.)
    - Fallback fixed hour offsets (`_FIXED_OFFSET_HOURS`) if specific timezone database entries are unavailable
  - Reminders are securely saved to the database and will be delivered even if the bot restarts.
- **`!timers`** - List the next 5 upcoming reminders across all channels, complete with an interactive Discord relative countdown timestamp, the original message, the target user, and the channel location.

## Configuration

### Personal Notes
- Place `.txt` and `.csv` files in the `notes/` folder
- Files are automatically indexed and searchable by the AI
- Supports both text content and structured CSV data

### System Prompts
- Customize the bot's personality and behavior in `config.py`
- Adjust character limits, history size, and web search triggers
- Modify the system prompt for different AI behaviors

## Development

### Adding New Commands
1. Create a new cog in the `cogs/` folder
2. Inherit from `commands.Cog`
3. Add your command methods with the `@commands.command()` decorator
4. Include a `setup(bot)` function for cog loading
5. Cogs in `cogs/` are loaded automatically on startup (`load_cogs()` in `main.py`)

### Example Cog Structure
```python
from discord.ext import commands

class MyCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
    
    @commands.command(name="example", help="Example command")
    async def example(self, ctx):
        await ctx.send("Example response")

async def setup(bot):
    await bot.add_cog(MyCog(bot))
```

## Dependencies

- **discord.py** - Discord bot framework
- **google-generativeai** - Google Gemini AI integration
- **chromadb** - Vector database for notes
- **pandas** - CSV data processing
- **requests** - HTTP requests for weather API
- **python-dotenv** - Environment variable management
- **asyncpg** - PostgreSQL async driver (quotes, reminders, task lists, signup sheets)
- **sentence-transformers** - Text embedding models
- **tzdata** - Timezone data for reminder parsing

## Security Notes

- Never commit your `.env` file or API keys
- The `config.py` file is gitignored by default
- Personal notes in the `notes/` folder are indexed but not automatically shared
- Database credentials should be kept secure
