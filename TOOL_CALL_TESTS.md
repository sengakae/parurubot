# Tool Call Test Messages

Manual test prompts for the `paruru,` chat tool calls. Every message must start with `paruru,` (with the comma) to reach the AI.

While testing, watch the bot logs for lines like `Tool call: get_weather({'city': 'Tokyo'})` to confirm which tool the model called.

## Weather (`get_weather`)
- `paruru, what's the weather in tokyo?`
- `paruru, should i bring a jacket in seattle today?` (should infer that it needs weather)
- `paruru, weather in asdfghjkl` (should say the city wasn't found)

## Web search (`search_web`)
- `paruru, who won the most recent world series?`
- `paruru, what's the latest news about the nintendo switch 2?`
- `paruru, how are you today?` (casual chat, should not search)

## Notes (`search_notes`)
- `paruru, what do my notes say about <topic in your notes folder>?`
- Ask something only your notes answer, without mentioning notes.

## Setting reminders (`set_reminder`)
- `paruru, remind me to stretch in 2 minutes` (wait for it to fire and mention you)
- `paruru, remind me to drink water every day` (repeats; first one is a day from now)
- `paruru, remind me to call mom tomorrow at 9am` (no timezone, should ask for one)
- `paruru, remind me to call mom tomorrow at 9am pst`
- `paruru, remind me to pay rent on the 1st of every month at 9am pst`
- `paruru, remind me about something yesterday` (past time, should refuse)

Confirmations should show the reminder ID and a Discord timestamp that renders in your local time.

## Listing and cancelling reminders (`list_my_reminders`, `cancel_reminder`)
- `paruru, what reminders do i have?` (three or more should come back as a list)
- `paruru, cancel my stretch reminder` (should list first to find the ID, then cancel; confirm with `!myreminders`)
- `paruru, cancel reminder zzzzzz` (bad ID, should say it can't find it)
- Have a second person run `paruru, cancel <your reminder id>`. It should fail, because cancelling only works on the sender's own reminders.

## Channel recap (`read_recent_messages`)
- `paruru, what did people talk about in the last hour?`
- `paruru, summarize the last 30 messages`
- `paruru, did anyone mention dinner plans today?`

## Several tools in one message
- `paruru, what's the weather in osaka and remind me to pack an umbrella in 1h if it's raining` (should call `get_weather`, then `set_reminder` only if it's raining)
- `paruru, look up when the next full moon is and remind me that morning at 8am pst`

## Regressions (should behave as before)
- `paruru,` with an image attached
- `paruru,` with a YouTube link
- `!remindme test 1m`, `!cancelreminder <id>`, `!weather tokyo`, `!summary 1h`

## Memories (`recall_person`, `show_my_memories`, `link_name`, background learner)
Messages are only read when the bot is called, and the newest 3 in each channel wait for the next call. After chatting, send any `paruru,` message or `!` command, then check the logs for `Memories: channel ...` lines.

- Say `i'm flying to osaka next month`, chat a few more lines, then call the bot. Your message should get a 📝 and appear in `!aboutme`.
- Have someone say `ben is the global chairman of kpmg` and react with 💀. It should be saved as a joke; `paruru, what does ben do?` should treat it as a bit, not a fact.
- `ben affleck is directing a new movie` (famous name, should not be saved)
- Reply to someone with `that's ben`. The name should link and show as `goes by ben` in their `!aboutme`.
- Admin: `paruru, @someone is ben` (should link); a non-admin trying the same should be refused.
- Someone other than sengakae says `i'm sengakae` (should not link)
- `paruru, where is ben going?` (should call `recall_person`)
- `paruru, show me my memories` (should post the `!aboutme` embed)
- `!aboutme`: remove a memory with the dropdown, add one with Add memory, and page with ◀ ▶ once you have more than 25
- Have someone else click your `!aboutme` controls (should get "this isn't yours")
- Post `ben hates cilantro`, delete it, then call the bot (should not be saved)
