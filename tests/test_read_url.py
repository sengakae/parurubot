import asyncio
from types import SimpleNamespace as NS

import utils.tools as tools

URL = "https://example.com/apple-ipad-ad"


def ctx():
    return tools.ToolContext(bot=NS(), message=NS())


def response(text, statuses):
    metadata = NS(url_metadata=[
        NS(retrieved_url=URL, url_retrieval_status=s) for s in statuses
    ]) if statuses is not None else None
    part = NS(text=text, thought=False)
    candidate = NS(content=NS(parts=[part]), url_context_metadata=metadata)
    return NS(text=text, candidates=[candidate])


def fake_generate(resp, calls):
    def generate(**kwargs):
        calls.append(kwargs)
        return resp
    return generate


def test_read_url_returns_page_summary(monkeypatch):
    calls = []
    resp = response("apple pulled the ad after complaints", ["URL_RETRIEVAL_STATUS_SUCCESS"])
    monkeypatch.setattr(tools, "_generate_with_error_retry", fake_generate(resp, calls))
    result = asyncio.run(tools.read_url(ctx(), url=URL, question="why was it pulled?"))
    assert result == {"result": "apple pulled the ad after complaints"}
    assert URL in calls[0]["contents"]
    assert "why was it pulled?" in calls[0]["contents"]
    assert calls[0]["config"].tools[0].url_context is not None


def test_read_url_reports_failed_fetch(monkeypatch):
    # A page the tool couldn't open must not come back as a confident summary.
    resp = response("probably about an ad", ["URL_RETRIEVAL_STATUS_ERROR"])
    monkeypatch.setattr(tools, "_generate_with_error_retry", fake_generate(resp, []))
    result = asyncio.run(tools.read_url(ctx(), url=URL))
    assert "error" in result


def test_read_url_reports_missing_metadata(monkeypatch):
    resp = response("probably about an ad", None)
    monkeypatch.setattr(tools, "_generate_with_error_retry", fake_generate(resp, []))
    result = asyncio.run(tools.read_url(ctx(), url=URL))
    assert "error" in result


def test_read_url_rejects_non_http(monkeypatch):
    calls = []
    monkeypatch.setattr(tools, "_generate_with_error_retry", fake_generate(None, calls))
    result = asyncio.run(tools.read_url(ctx(), url="file:///etc/passwd"))
    assert "error" in result
    assert calls == []


def test_read_url_is_declared():
    assert "read_url" in tools.HANDLERS
    assert any(d["name"] == "read_url" for d in tools.TOOL_DECLARATIONS)
