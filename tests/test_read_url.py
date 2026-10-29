import asyncio
from types import SimpleNamespace as NS

import utils.tools as tools

URL = "https://example.com/apple-ipad-ad"


class FakeChannel:
    """fetch_message returns each stored version of a message in turn (Discord
    fills in link previews a moment after a message is posted)."""

    def __init__(self, *messages):
        self.versions = {}
        self.order = []
        for m in messages:
            versions = m if isinstance(m, list) else [m]
            self.versions[versions[0].id] = list(versions)
            self.order.append(versions[0].id)

    async def fetch_message(self, message_id):
        versions = self.versions[message_id]
        return versions.pop(0) if len(versions) > 1 else versions[0]

    async def history(self, limit=100):
        for message_id in reversed(self.order[-limit:]):
            yield self.versions[message_id][-1]


def msg(id, content, embeds=(), reply_to=None):
    reference = NS(message_id=reply_to) if reply_to else None
    return NS(id=id, content=content, embeds=list(embeds), reference=reference)


def embed(url=URL, title="Apple pulls iPad mini ad", description="The pinch gesture..."):
    return NS(url=url, title=title, description=description,
              provider=NS(name="Tech Times"), author=NS(name=None))


def ctx(current=None, *others):
    current = current or msg(1, "paruru, what's this")
    first = current[0] if isinstance(current, list) else current
    return tools.ToolContext(bot=NS(), message=NS(
        id=first.id, content=first.content, reference=first.reference,
        channel=FakeChannel(*others, current),
    ))


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


FAILED = ["URL_RETRIEVAL_STATUS_PAYWALL"]


def patch_failed_read(monkeypatch):
    monkeypatch.setattr(tools, "_generate_with_error_retry",
                        fake_generate(response("guess", FAILED), []))
    monkeypatch.setattr(tools, "PREVIEW_RETRY_DELAY", 0)


def test_failed_read_falls_back_to_preview_on_current_message(monkeypatch):
    patch_failed_read(monkeypatch)
    current = msg(1, f"paruru, what's this {URL}", [embed()])
    result = asyncio.run(tools.read_url(ctx(current), url=URL))
    assert result["preview"] == {
        "site": "Tech Times",
        "title": "Apple pulls iPad mini ad",
        "description": "The pinch gesture...",
    }
    assert "preview" in result["note"]
    assert "error" not in result


def test_preview_waits_for_discord_to_attach_embed(monkeypatch):
    patch_failed_read(monkeypatch)
    current = [msg(1, f"paruru, what's this {URL}"),
               msg(1, f"paruru, what's this {URL}", [embed()])]
    result = asyncio.run(tools.read_url(ctx(current), url=URL))
    assert result["preview"]["title"] == "Apple pulls iPad mini ad"


def test_preview_from_replied_message(monkeypatch):
    patch_failed_read(monkeypatch)
    linked = msg(5, f"look {URL}", [embed()])
    current = msg(9, "paruru, what's this link talking about", reply_to=5)
    result = asyncio.run(tools.read_url(ctx(current, linked), url=URL))
    assert result["preview"]["title"] == "Apple pulls iPad mini ad"


def test_preview_from_recent_channel_history(monkeypatch):
    patch_failed_read(monkeypatch)
    linked = msg(5, f"look {URL}", [embed()])
    chatter = msg(6, "lol")
    current = msg(9, "paruru, what was ben's link about")
    result = asyncio.run(tools.read_url(ctx(current, linked, chatter), url=URL))
    assert result["preview"]["title"] == "Apple pulls iPad mini ad"


def test_preview_tolerates_tracking_params(monkeypatch):
    patch_failed_read(monkeypatch)
    shared = f"{URL}?s=19&utm_source=x"
    current = msg(1, f"paruru {shared} and https://other.com/x",
                  [embed(url="https://other.com/x", title="Other"), embed(url=URL + "/")])
    result = asyncio.run(tools.read_url(ctx(current), url=shared))
    assert result["preview"]["title"] == "Apple pulls iPad mini ad"


def test_no_preview_for_a_different_link(monkeypatch):
    patch_failed_read(monkeypatch)
    current = msg(1, f"paruru {URL} https://other.com/x",
                  [embed(url="https://other.com/x", title="Other")])
    result = asyncio.run(tools.read_url(ctx(current), url=URL))
    assert "error" in result


def test_failed_read_without_any_preview_is_an_error(monkeypatch):
    patch_failed_read(monkeypatch)
    current = msg(1, f"paruru, what's this {URL}")
    result = asyncio.run(tools.read_url(ctx(current), url=URL))
    assert "error" in result
    assert "preview" not in result


def test_preview_when_model_drops_tracking_params(monkeypatch):
    patch_failed_read(monkeypatch)
    current = msg(1, f"paruru {URL}?s=19", [embed()])
    result = asyncio.run(tools.read_url(ctx(current), url=URL))
    assert result["preview"]["title"] == "Apple pulls iPad mini ad"
