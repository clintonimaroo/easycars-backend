from pydantic import BaseModel
import pytest

from app.services import agent_provider


class ExampleResult(BaseModel):
    value: str


@pytest.mark.asyncio
@pytest.mark.parametrize(("schema", "search"), [
    (None, False),
    (None, True),
    (ExampleResult, False),
    (ExampleResult, True),
])
async def test_dealer_voice_and_parts_guidance_are_scoped_to_answer_requests(monkeypatch, schema, search):
    async def post(path, *, json):
        assert path == "responses"
        instructions = json["instructions"]
        assert ("capable colleague" in instructions) == (schema is None)
        for phrase in (
            "used OEM parts", "direct item or donor page", "confirmed part stock", "still unpriced",
            "shipping from local pickup", "item pickup location", "correct it directly",
        ):
            assert (phrase in instructions) == (schema is None and search)
        return {
            "status": "completed",
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"value": "done"}'}]}],
        }

    monkeypatch.setattr(agent_provider, "_post", post)
    await agent_provider.responses("Answer from the supplied evidence.", [], schema=schema, search=search)


@pytest.mark.parametrize(("suggestion", "expected"), [
    ("Which part should I check first?", "Which part should I check first?"),
    ("Send it now.", None),
    ("Look up person@example.invalid.", None),
    ("One line\nAnother line", None),
    ("a" * 181, None),
])
def test_reply_parser_preserves_citation_markdown_and_filters_suggestions(suggestion, expected):
    first = "I found a donor in town."
    findings = "[Check the listing](https://example.invalid/item)\n\nThe donor is listed at the yard."
    raw_reply = f"{first}\n{agent_provider.REPLY_MESSAGE_BREAK}\n{findings}\n{agent_provider.SUGGESTED_REPLY_MARKER}\n{suggestion}"
    assert agent_provider.parse_reply(raw_reply) == ([first, findings], expected)


@pytest.mark.asyncio
async def test_search_retries_token_limit_and_keeps_verified_citations(monkeypatch):
    calls = []
    url = "https://yard.example.invalid/vehicles"

    async def post(path, *, json):
        assert path == "responses"
        calls.append(json)
        if len(calls) == 1:
            return {
                "status": "incomplete",
                "incomplete_details": {"reason": "max_output_tokens"},
                "output": [{"type": "message", "content": [{"type": "output_text", "text": "Partial"}]}],
            }
        return {
            "status": "completed",
            "incomplete_details": None,
            "output": [{"type": "message", "content": [{
                "type": "output_text",
                "text": "Check the yard inventory.",
                "annotations": [{"type": "url_citation", "url": url, "title": "Yard inventory"}],
            }]}],
        }

    monkeypatch.setattr(agent_provider, "_post", post)
    result = await agent_provider.responses("Find public information.", [], search=True)
    assert result == ("Check the yard inventory.", [{"url": url, "title": "Yard inventory"}])
    assert len(calls) == 2
    assert calls[0]["max_output_tokens"] > 2400
    assert calls[1]["max_output_tokens"] > calls[0]["max_output_tokens"]
    assert calls[1] == {**calls[0], "max_output_tokens": calls[1]["max_output_tokens"]}
    assert calls[1]["store"] is False


@pytest.mark.asyncio
@pytest.mark.parametrize(("status", "reason", "search", "schema", "expected_calls", "error"), [
    ("incomplete", "max_output_tokens", True, None, 2, "fewer parts"),
    ("incomplete", "max_output_tokens", False, ExampleResult, 1, "output limit"),
    ("incomplete", "max_output_tokens", True, ExampleResult, 1, "response limit"),
    ("incomplete", "content_filter", True, None, 1, "could not complete"),
    ("failed", None, True, None, 1, "could not complete"),
])
async def test_responses_rejects_incomplete_output_without_repeated_retries(
    monkeypatch, status, reason, search, schema, expected_calls, error,
):
    calls = []

    async def post(path, *, json):
        calls.append(json)
        return {
            "status": status,
            "incomplete_details": {"reason": reason} if reason else None,
            "output": [{"type": "message", "content": [{"type": "output_text", "text": '{"value": "partial"}'}]}],
        }

    monkeypatch.setattr(agent_provider, "_post", post)
    with pytest.raises(agent_provider.AgentProviderError, match=error):
        await agent_provider.responses("Find public information.", [], schema=schema, search=search)
    assert len(calls) == expected_calls
