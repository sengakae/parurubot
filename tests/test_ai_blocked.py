import asyncio
from types import SimpleNamespace as NS

from google.genai import types

import utils.ai as ai

BLOCKED = NS(text=None, candidates=[], function_calls=None,
             prompt_feedback=NS(block_reason="OTHER"))
OK = NS(text="a summary", candidates=[], function_calls=None, prompt_feedback=None)


def fake_generate(responses, calls):
    def generate(**kwargs):
        calls.append(kwargs)
        return responses.pop(0)

    return generate


def test_blocked_chat_is_not_retried(monkeypatch):
    # a blocked 33-minute video was re-sent twice and then hit the 1M tokens/minute limit
    calls = []
    monkeypatch.setattr(ai, "_generate_with_error_retry", fake_generate([BLOCKED], calls))
    text = asyncio.run(ai.chat_with_ai("summarize", None, videos=["https://youtu.be/x"]))
    assert len(calls) == 1
    assert text == ai.BLOCKED_RESPONSE_TEXT


def test_blocked_generate_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "_generate_with_error_retry", fake_generate([BLOCKED], calls))
    config = types.GenerateContentConfig(tools=[types.Tool(google_search=types.GoogleSearch())])
    assert ai.generate_content_with_retry(model="m", contents="x", config=config) == ai.BLOCKED_RESPONSE_TEXT
    assert len(calls) == 1


def test_videos_use_low_media_resolution(monkeypatch):
    calls = []
    monkeypatch.setattr(ai, "_generate_with_error_retry", fake_generate([OK, OK], calls))
    asyncio.run(ai.chat_with_ai("summarize", None, videos=["https://youtu.be/x"]))
    asyncio.run(ai.chat_with_ai("hi", None))
    assert calls[0]["config"].media_resolution == types.MediaResolution.MEDIA_RESOLUTION_LOW
    assert calls[1]["config"].media_resolution is None  # images and text keep the default
