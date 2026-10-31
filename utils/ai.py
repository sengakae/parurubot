import asyncio
import io
import json
import logging
import re
import time
from datetime import datetime, timezone

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

url_context_config = types.GenerateContentConfig(
    tools=[types.Tool(url_context=types.UrlContext())],
    thinking_config=types.ThinkingConfig(include_thoughts=False),
)

client = genai.Client(api_key=GEMINI_API_KEY)
MODEL = "gemini-2.5-flash"
MAX_API_RETRIES = 3
# Gemini sometimes finishes normally but returns no text, mostly when a web search
# finds nothing (e.g. obscure handles). Retry it, then fall back to no search.
MAX_EMPTY_RETRIES = 2
EMPTY_RESPONSE_TEXT = "No response generated."
MAX_TOOL_STEPS = 5


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
        text = extract_response_text(_generate_with_error_retry(**kwargs))
        if text:
            return text
        logger.warning(
            f"Empty Gemini response (attempt {attempt + 1}/{MAX_EMPTY_RETRIES})"
        )

    config = kwargs.get("config")
    if config is not None and config.tools:
        logger.warning("Retrying Gemini request without web search")
        no_search = config.model_copy(update={"tools": None})
        text = extract_response_text(
            _generate_with_error_retry(**{**kwargs, "config": no_search})
        )
        if text:
            return text

    return EMPTY_RESPONSE_TEXT


def _generate_with_error_retry(**kwargs):
    last_error = None
    for attempt in range(MAX_API_RETRIES):
        try:
            return client.models.generate_content(**kwargs)
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


def summarize_channel(messages, char_limit=CHAR_LIMIT):
    """
    Summarize a list of Discord messages (dicts with author/content/timestamp).
    Keeps summary under char_limit characters.
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
        f"Keep the summary under {char_limit} characters and organize it as a clear, structured list.\n\n"
        f"Here's the conversation:\n\n{conversation_text}"
    )

    text = generate_content_with_retry(
        model=MODEL,
        contents=summary_prompt,
        config=types.GenerateContentConfig(
            thinking_config=types.ThinkingConfig(include_thoughts=False)
        ),
    )

    if len(text) > char_limit:
        text = text[: char_limit - 3] + "..."
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


async def chat_with_ai(
    cleaned_content,
    tool_ctx,
    history_context="",
    notes_context="",
    images=None,
    videos=None,
):
    """Generate a response from the AI, letting it call the tools in utils.tools."""
    # Imported here because utils.tools imports this module.
    from utils.tools import TOOL_DECLARATIONS, TOOL_GUIDANCE, run_tool

    system_message = f"{SYSTEM_PROMPT}{notes_context}"

    if images:
        logger.info(f"Processing {len(images)} images")

    if videos:
        logger.info(f"Processing {len(videos)} YouTube videos")
        system_message = VIDEO_SUMMARY_PROMPT

    now = datetime.now(timezone.utc).isoformat(timespec="minutes")
    system_message += f"{TOOL_GUIDANCE}\nThe current time is {now} UTC."

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

    config = types.GenerateContentConfig(
        tools=[types.Tool(function_declarations=TOOL_DECLARATIONS)],
        system_instruction=system_message,
        thinking_config=types.ThinkingConfig(include_thoughts=False),
    )

    # Same declarations (the conversation may hold earlier calls) but no new calls allowed.
    no_tools_config = config.model_copy(
        update={
            "tool_config": types.ToolConfig(
                function_calling_config=types.FunctionCallingConfig(
                    mode=types.FunctionCallingConfigMode.NONE
                )
            )
        }
    )

    current_config = config
    final_text = None
    tool_steps = 0
    empty_responses = 0
    while True:
        response = await asyncio.to_thread(
            _generate_with_error_retry,
            model=MODEL,
            contents=conversation,
            config=current_config,
        )
        calls = response.function_calls if current_config is config else None
        if calls:
            results = await asyncio.gather(
                *(run_tool(tool_ctx, call.name, call.args) for call in calls)
            )
            conversation.append(response.candidates[0].content)
            conversation.append(
                types.Content(
                    role="user",
                    parts=[
                        types.Part.from_function_response(name=call.name, response=result)
                        for call, result in zip(calls, results)
                    ],
                )
            )
            tool_steps += 1
            if tool_steps >= MAX_TOOL_STEPS:
                logger.warning(f"Reached {MAX_TOOL_STEPS} tool steps; answering without tools")
                current_config = no_tools_config
            continue

        final_text = extract_response_text(response)
        if final_text or current_config is no_tools_config:
            break
        empty_responses += 1
        if empty_responses >= MAX_EMPTY_RETRIES:
            logger.warning("Retrying Gemini request without tools")
            current_config = no_tools_config
        else:
            logger.warning(
                f"Empty Gemini response (attempt {empty_responses}/{MAX_EMPTY_RETRIES})"
            )

    final_text = final_text or EMPTY_RESPONSE_TEXT

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

    return _lower_keep_timestamps(final_text).strip()


# Discord timestamp styles are case-sensitive (<t:123:R> works, <t:123:r> doesn't).
_DISCORD_TIMESTAMP_RE = re.compile(r"(<t:\d+(?::[tTdDfFR])?>)")


def _lower_keep_timestamps(text: str) -> str:
    return "".join(
        part if _DISCORD_TIMESTAMP_RE.fullmatch(part) else part.lower()
        for part in _DISCORD_TIMESTAMP_RE.split(text)
    )


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