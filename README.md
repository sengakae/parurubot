# parurubot

Discord bot. Use "!" prefix to issue commands.

## Setup
Create a `.env` file at the root folder. It should contain the following
```
DISCORD_TOKEN=[from: https://discord.com/developers/applications]
WEATHER_TOKEN=[from: https://home.openweathermap.org/api_keys]
```

List of available commands.

### !add [keyword] [quote]
- Adds [quote] into an auto-generated `quotes.json` file

### !rquote
- Randomly selects a saved quote and prints it

### ![keyword]
- Prints the quote saved with [keyword]

### !purge [number]
- Deletes the last [number] lines in the channel

### !weather [city_name]
- Returns the weather information of [city_name]
