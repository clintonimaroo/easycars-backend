import json

from fastapi import HTTPException
import pytest

from app.services import agent_runtime


@pytest.mark.asyncio
@pytest.mark.parametrize(("photo_opt_in", "private_file_opt_in"), [(True, False), (False, True), (False, False)])
async def test_parts_research_isolates_private_photos_from_web_search(monkeypatch, photo_opt_in, private_file_opt_in):
    photo_analysis_enabled = photo_opt_in or private_file_opt_in
    monkeypatch.setattr(agent_runtime.settings, "AGENT_ALLOW_VEHICLE_PHOTO_ANALYSIS", photo_opt_in)
    monkeypatch.setattr(agent_runtime.settings, "AGENT_ALLOW_PRIVATE_FILE_INPUTS", private_file_opt_in)
    photo = {"id": "private-photo-id", "type": "image/jpeg", "name": "private.jpeg"}
    document = {"id": "private-doc-id", "type": "application/pdf", "name": "private.pdf"}
    turn = {
        "text": "Find parts in Dallas based on these photos. Contact example@example.invalid.",
        "attachments": [photo, document],
    }
    image_content = {"type": "input_image", "image_url": "data:image/jpeg;base64,cGhvdG8="}
    stages = []
    calls = []
    source = {"url": "https://yard.example.invalid/inventory", "title": "Yard inventory"}

    async def history(*args):
        return []

    async def persist(db, turn, **changes):
        turn.update(changes)
        if "stage" in changes:
            stages.append(changes["stage"])

    async def files(db, user, attachments):
        assert attachments == [photo]
        return [{"type": "input_text", "text": photo["id"]}, image_content]

    async def provider(instructions, content, schema=None, search=False):
        calls.append((schema, search))
        if schema is agent_runtime.Intent:
            return schema(kind="research", query="2015 Honda Accord Dallas", continues_previous=False)
        if schema is agent_runtime.PhotoResearchPlan:
            assert photo_analysis_enabled and not search
            assert content[1:] == [image_content]
            assert json.loads(content[0]["text"]) == {"public_search_terms": "2015 Honda Accord Dallas"}
            return schema(
                query="2015 Honda Accord front bumper Dallas",
                visible_parts=["front bumper cover"],
                question=None,
            )
        assert search
        assert content == agent_runtime.agent_provider.text_content({
            "query": "2015 Honda Accord front bumper Dallas",
            "possible_parts_from_photos": ["front bumper cover"],
        })
        assert "matching donor vehicle from confirmed part availability" in instructions
        return "Check these potential donor vehicles.", [source]

    monkeypatch.setattr(agent_runtime, "_history", history)
    monkeypatch.setattr(agent_runtime, "_persist", persist)
    monkeypatch.setattr(agent_runtime, "_file_content", files)
    monkeypatch.setattr(agent_runtime, "_payload_for_turn", lambda turn: {"request": turn["text"]})
    monkeypatch.setattr(agent_runtime.agent_provider, "responses", provider)

    if photo_analysis_enabled:
        reply, sources, actions, needs_input = await agent_runtime._plan(None, {}, turn)
        assert (reply, sources, actions, needs_input) == ("Check these potential donor vehicles.", [source], [], False)
        assert calls == [(agent_runtime.Intent, False), (agent_runtime.PhotoResearchPlan, False), (None, True)]
        assert stages == ["Understanding your request", "Reading 1 photo", "Searching the web"]
        assert [item["text"] for item in turn["progress_messages"]] == [
            "I’ll look through the photos first.", "I’m checking current sources.",
        ]
        assert all(item["created_at"].endswith("+00:00") for item in turn["progress_messages"])
    else:
        with pytest.raises(HTTPException) as exc:
            await agent_runtime._plan(None, {}, turn)
        assert exc.value.status_code == 503
        assert calls == [(agent_runtime.Intent, False)]
