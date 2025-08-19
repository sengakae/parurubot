import google.generativeai as genai

from config import CHAR_LIMIT, GEMINI_API_KEY, SYSTEM_PROMPT

genai.configure(api_key=GEMINI_API_KEY)

model = genai.GenerativeModel("gemini-2.0-flash")


def summarize_channel(messages):
    """
    Summarize a list of Discord messages (dicts with author/content/timestamp).
    Keeps summary under CHAR_LIMIT characters.
    """
    if not messages:
        return "No messages to summarize."

    conversation_text = (
        f"Here are the last {len(messages)} messages from this Discord channel:\n\n"
    )
    for msg in messages:
        conversation_text += f"{msg['author']}: {msg['content']}\n"

    summary_prompt = (
        "Please analyze this Discord conversation and provide a concise summary of the main topics discussed. "
        "Focus on:\n"
        "- Key themes and subjects\n"
        "- Important questions or decisions made\n"
        "- Any ongoing discussions or unresolved topics\n"
        "- General mood/tone of the conversation\n\n"
        f"Keep the summary under {CHAR_LIMIT} characters and organize it as a clear, structured list.\n\n"
        f"Here's the conversation:\n\n{conversation_text}"
    )

    response = model.generate_content(summary_prompt)
    text = response.text or "No summary generated."

    if len(text) > CHAR_LIMIT:
        text = text[: CHAR_LIMIT - 3] + "..."
    return text


def chat_with_ai(cleaned_content, history_context="", notes_context="", needs_web_search=False):
    """
    Generate a response from the AI given user input, optional history, and notes.
    Handles both regular and web-search style prompts.
    """
    if needs_web_search:
        print("Using web search model for current information")
        search_prompt = (
            f"{SYSTEM_PROMPT}{notes_context}\n\n"
            f"Now, please provide current and up-to-date information about: {cleaned_content}. "
            f"Use your knowledge to give the most recent and accurate information available. "
            f"Keep your response concise and under {CHAR_LIMIT} characters while maintaining your casual, friendly personality."
        )
        if history_context:
            search_prompt += f"\n\nContext from recent conversation:\n{history_context}"

        response = model.generate_content(search_prompt)
    else:
        print("Using regular model for conversation")
        conversation = [
            {"role": "user", "parts": [f"{SYSTEM_PROMPT}{notes_context}"]},
            {"role": "model", "parts": ["got it! i'll be casual and friendly in our chats"]},
        ]

        if history_context:
            conversation.append(
                {
                    "role": "user",
                    "parts": [f"Here's the recent conversation context:\n{history_context}"],
                }
            )

        conversation.append({"role": "user", "parts": [cleaned_content]})
        response = model.generate_content(conversation)

    text = response.text or "No response generated."

    if len(text) > CHAR_LIMIT:
        print(f"Response too long ({len(text)} chars), truncating to {CHAR_LIMIT}")
        truncated = text[: CHAR_LIMIT - 3]
        last_punct = max(truncated.rfind("."), truncated.rfind("!"), truncated.rfind("?"))
        if last_punct > CHAR_LIMIT * 0.8:
            text = truncated[: last_punct + 1]
        else:
            text = truncated + "..."

    return text