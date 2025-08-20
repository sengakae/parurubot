# parurubot

A feature-rich Discord bot built with Python and Discord.py, featuring AI-powered chat, personal notes management, and utility commands.

## Features

### AI-Powered Chat
- **Natural Conversations**: Chat naturally with "paruru, " followed by your message
- **Context Awareness**: Remembers the last 20 messages in each channel for contextual responses
- **Personal Notes Integration**: Searches through your personal notes to provide relevant information
- **Smart Response Types**: Automatically detects when to provide current information vs. conversational responses
- **Image Attachment**: Detects when you attach an image, parses it, and sends it to Gemini AI for analysis or conversation

### Personal Notes Management
- **Vector Database**: Automatically loads and indexes `.txt` and `.csv` files from the `notes/` folder
- **Semantic Search**: Finds relevant information from your notes using AI-powered search
- **Multiple Formats**: Supports both text files and CSV data with automatic chunking and indexing

### Utility Commands
- **Quote Management**: Save, retrieve, and manage quotes with keywords
- **Weather Information**: Get current weather data for any city
- **Gear Score Calculator**: Calculate Epic Seven gear scores with various stat types
- **Channel Management**: Purge messages and view conversation history
- **AI Summaries**: Generate intelligent summaries of channel conversations

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
   WEATHER_TOKEN=your_openweathermap_api_key
   GEMINI_API_KEY=your_gemini_api_key
   ```

5. **Database Setup**
   - Ensure PostgreSQL is running
   - The bot will automatically initialize the database on first run

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
│   └── quotes.py         # Quote management commands
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
- **`![keyword]`** - Display a specific quote by keyword

### Utility Commands
- **`!purge [number]`** - Delete the last N messages (max 100) [Requires admin permissions]
- **`!weather [city]`** - Get weather information for a city
- **`!gs [stats]`** - Calculate Epic Seven gear score
  - **Percentage stats**: `[num]atk`, `[num]def`, `[num]hp`, `[num]eff`, `[num]er`
  - **Critical stats**: `[num]cc`, `[num]cd`
  - **Speed**: `[num]s`
  - **Flat stats**: `[num]atk`, `[num]def`, `[num]hp`

### History & Analysis
- **`!summary`** - Generate AI summary of last 500 messages

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
5. Add the cog to the `load_cogs()` function in `main.py`

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
- **sentence-transformers** - Text embedding models

## Security Notes

- Never commit your `.env` file or API keys
- The `config.py` file is gitignored by default
- Personal notes in the `notes/` folder are indexed but not automatically shared
- Database credentials should be kept secure
