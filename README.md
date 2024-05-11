# parurubot

Discord bot. Use "!" prefix to issue commands.

## Setup
Create a `.env` file at the root folder. It should contain the following
```
DISCORD_TOKEN=[from: https://discord.com/developers/applications]
WEATHER_TOKEN=[from: https://home.openweathermap.org/api_keys]
GEMINI_API_KEY=[from: https://aistudio.google.com/app/apikey]
```

List of available commands.

### !add [keyword] [quote]
- Adds [quote] into an auto-generated `quotes.json` file

### !rm [keyword]
- Removes the quote associated with [keyword]

### !showquotes
- Prints a list of all keywords

### !rquote
- Randomly selects a saved quote and prints it

### ![keyword]
- Prints the quote saved with [keyword]

### !purge [number]
- Deletes the last [number] lines in the channel

### !weather [city_name]
- Returns the weather information of [city_name]

### !gs [gear stat values]
- Returns the calculated gear score value
  - For atk%, def%, eff%, er%, input the number
  - For cc%, use [num]cc
  - For cd%, use [num]cd
  - For spd, use [num]s
  - For flat values: use [num]atk, [num]def, [num]hp

### @bot [prompt]
- Chat with an AI bot
