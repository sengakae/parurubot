import io
import json
import logging
import time

from google import genai
from google.genai import types

from config import (CHAR_LIMIT, GEMINI_API_KEY, SYSTEM_PROMPT,
                    VIDEO_SUMMARY_PROMPT)

logger = logging.getLogger(__name__)

grounding_tool = types.Tool(google_search=types.GoogleSearch())

grounding_config = types.GenerateContentConfig(
    tools=[grounding_tool],
    thinking_config=types.ThinkingConfig(include_thoughts=False),
)

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL = "gemini-2.5-flash"
MAX_API_RETRIES = 3
# Gemini sometimes finishes normally but returns no text, mostly when a web search
# finds nothing (e.g. obscure handles). Retry it, then fall back to no search.
MAX_EMPTY_RETRIES = 2
EMPTY_RESPONSE_TEXT = "No response generated."


def extract_response_text(response) -> str | None:
    try:
        text = response.text
        if text:
            return text
    except (ValueError, AttributeError) as e:
        logger.warning(f"Could not read response.text: {e}")

    for candidate in getattr(response, "candidates", None) or []:
        content = getattr(candidate, "content", None)
        if not content or not content.parts:
            continue
        text_parts = [
            part.text
            for part in content.parts
            if getattr(part, "text", None) and not getattr(part, "thought", False)
        ]
        if text_parts:
            return "".join(text_parts)

    candidates = getattr(response, "candidates", None) or []
    logger.warning(
        "Gemini returned no text: finish_reasons=%s prompt_feedback=%s",
        [getattr(c, "finish_reason", None) for c in candidates],
        getattr(response, "prompt_feedback", None),
    )
    return None


def _is_retryable_error(error: Exception) -> bool:
    message = str(error).lower()
    retry_markers = (
        "429",
        "500",
        "502",
        "503",
        "504",
        "resource_exhausted",
        "rate limit",
        "timeout",
        "temporarily unavailable",
        "internal error",
    )
    return any(marker in message for marker in retry_markers)


def generate_content_with_retry(**kwargs) -> str:
    for attempt in range(MAX_EMPTY_RETRIES):
        text = _generate_with_error_retry(**kwargs)
        if text:
            return text
        logger.warning(
            f"Empty Gemini response (attempt {attempt + 1}/{MAX_EMPTY_RETRIES})"
        )

    config = kwargs.get("config")
    if config is not None and config.tools:
        logger.warning("Retrying Gemini request without web search")
        no_search = config.model_copy(update={"tools": None})
        text = _generate_with_error_retry(**{**kwargs, "config": no_search})
        if text:
            return text

    return EMPTY_RESPONSE_TEXT


def _generate_with_error_retry(**kwargs) -> str | None:
    last_error = None
    for attempt in range(MAX_API_RETRIES):
        try:
            response = client.models.generate_content(**kwargs)
            return extract_response_text(response)
        except Exception as e:
            last_error = e
            if attempt == MAX_API_RETRIES - 1 or not _is_retryable_error(e):
                raise
            delay = 2**attempt
            logger.warning(
                f"Gemini request failed (attempt {attempt + 1}/{MAX_API_RETRIES}): {e}. "
                f"Retrying in {delay}s..."
            )
            time.sleep(delay)

    raise last_error


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

    text = generate_content_with_retry(
        model=MODEL,
        contents=summary_prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(include_thoughts=False)
        ),
    )

    if len(text) > CHAR_LIMIT:
        text = text[: CHAR_LIMIT - 3] + "..."
    return text


def convert_pil_to_part(pil_image):
    """Convert PIL Image to a Google GenAI Part object."""
    # JPEG can't store modes like LA or I;16, so normalize everything to RGB.
    if pil_image.mode != "RGB":
        pil_image = pil_image.convert("RGB")

    img_byte_arr = io.BytesIO()
    pil_image.save(img_byte_arr, format="JPEG")
    img_byte_arr.seek(0)

    return {"inline_data": {"mime_type": "image/jpeg", "data": img_byte_arr.getvalue()}}


def convert_video_to_part(url):
    """Create a part for YouTube video content"""
    return {"file_data": {"mime_type": "video/*", "fileUri": url}}


def chat_with_ai(
    cleaned_content,
    history_context="",
    notes_context="",
    images=None,
    videos=None,
):
    """Generate a response from the AI given user input, optional history, and notes."""

    system_message = f"{SYSTEM_PROMPT}{notes_context}"

    if images:
        logger.info(f"Processing {len(images)} images")

    if videos:
        logger.info(f"Processing {len(videos)} YouTube videos")
        system_message = VIDEO_SUMMARY_PROMPT

    conversation = []

    if history_context:
        if isinstance(history_context, dict):
            conversation.append(history_context)
        elif isinstance(history_context, list):
            conversation.extend(history_context)

    current_prompt = [{"text": cleaned_content}]
    if images:
        for img in images:
            current_prompt.append(convert_pil_to_part(img))

    if videos:
        for url in videos:
            current_prompt.append(convert_video_to_part(url))

    conversation.append({"role": "user", "parts": current_prompt})

    logger.info("Using web search for response")
    config = types.GenerateContentConfig(
        tools=[types.Tool(google_search=types.GoogleSearch())],
        system_instruction=system_message,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )

    final_text = generate_content_with_retry(
        model=MODEL,
        contents=conversation,
        config=config,
    )

    if "tool_code" in final_text or "print(" in final_text:
        logger.warning("Internal tool strings leaked, stripping out code remnants.")
        lines = final_text.split("\n")
        clean_lines = [
            l
            for l in lines
            if not any(
                x in l
                for x in ["tool_code", "print(", "google_search.search", "thought"]
            )
        ]
        final_text = "\n".join(clean_lines).strip()

    return final_text.lower().strip()


def generate_quiz_question(level: str, category: str):
    """
    Generates a JLPT/TOPIK/HSK question using Gemini.
    Returns a dict with: question, options (dict), correct (char), explanation.
    """

    system_instruction = (
        "You are an expert language tutor for JLPT, TOPIK, and HSK. "
        "Your task is to provide a multiple-choice question in JSON format. "
        "The question and options should be in the target language (Japanese, Korean, or Chinese), "
        "but the 'explanation' field MUST always be written in English. "
        "The JSON must strictly follow this schema:\n"
        "{\n"
        "  'question': 'string',\n"
        "  'options': {'A': 'string', 'B': 'string', 'C': 'string', 'D': 'string'},\n"
        "  'correct': 'A, B, C, or D',\n"
        "  'explanation': 'string'\n"
        "}"
    )

    user_prompt = f"Generate a {level.upper()} {category} question."

    try:
        response = client.models.generate_content(
            model=MODEL,
            contents=user_prompt,
            config=types.GenerateContentConfig(
                system_instruction=system_instruction,
                response_mime_type="application/json",
                thinking_config=types.ThinkingConfig(include_thoughts=False),
            ),
        )

        raw_content = response.text

        data = json.loads(raw_content)

        required_keys = ["question", "options", "correct", "explanation"]
        if all(key in data for key in required_keys):
            return data
        else:
            raise ValueError("Missing keys in AI response")

    except Exception as e:
        logger.error(f"Quiz Generation Error: {e}")
        return {
            "question": "Could not generate a question at this time.",
            "options": {"A": "Error", "B": "Error", "C": "Error", "D": "Error"},
            "correct": "A",
            "explanation": f"The AI encountered an issue: {str(e)}",
        }