import io

import requests
from google import genai
from google.genai import types
from PIL import Image

from config import CHAR_LIMIT, GEMINI_API_KEY, SYSTEM_PROMPT

grounding_tool = types.Tool(
    google_search=types.GoogleSearch()
)

grounding_config = types.GenerateContentConfig(
    tools=[grounding_tool]
)

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL = "gemini-2.0-flash"


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

    response = client.models.generate_content(
        model=MODEL,
        contents=summary_prompt
    )
    text = response.text or "No summary generated."

    if len(text) > CHAR_LIMIT:
        text = text[: CHAR_LIMIT - 3] + "..."
    return text


async def download_image_from_url(url):
    """Download and return PIL Image from URL"""
    try:
        response = requests.get(url)
        response.raise_for_status()
        return Image.open(io.BytesIO(response.content))
    except Exception as e:
        print(f"Error downloading image: {e}")
        return None


def convert_pil_to_part(pil_image):
    """Convert PIL Image to a Google GenAI Part object."""
    if pil_image.mode in ('RGBA', 'P'):
        pil_image = pil_image.convert('RGB')

    img_byte_arr = io.BytesIO()
    pil_image.save(img_byte_arr, format='JPEG')
    img_byte_arr.seek(0)
    
    return {
        'inline_data': {
            'mime_type': 'image/jpeg',
            'data': img_byte_arr.getvalue()
        }
    }


def chat_with_ai(cleaned_content, history_context="", notes_context="", needs_web_search=False, images = None):
    """
    Generate a response from the AI given user input, optional history, and notes.
    Handles both regular and web-search style prompts.
    """

    system_message = f"{SYSTEM_PROMPT}{notes_context}"

    if images:
        print(f"Processing {len(images)} images")
        system_message += "\n\nYou can see and analyze images that users share. Describe what you see and respond naturally to any questions about the images."

    conversation = []
    conversation.append({
        "role": "user",
        "parts": [{"text": system_message}]
    })

    conversation.append({
        "role": "model", 
        "parts": [{"text": "got it! i'll be casual and friendly in our chats"}]
    })

    if history_context:
        conversation.append({
            "role": "user",
            "parts": [{"text": f"Here's the recent conversation context:\n{history_context}"}]
        })

    current_prompt = [{"text": cleaned_content}]
    if images:
        for img in images:
            current_prompt.append(convert_pil_to_part(img)) 
    
    conversation.append({
        "role": "user",
        "parts": current_prompt
    })

    if needs_web_search:
        print("Using web search model for current information")
        grounding_tool = types.Tool(google_search=types.GoogleSearch())
        config = types.GenerateContentConfig(tools=[grounding_tool])

        response = client.models.generate_content(
            model=MODEL,
            contents=conversation,
            config=config
        )
    else:
        print("Using regular model for conversation")

        response = client.models.generate_content(
            model=MODEL,
            contents=conversation
        )

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