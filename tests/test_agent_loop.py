import json

import pytest

from app.services import agent_jobs, agent_loop, agent_memory, agent_tools


@pytest.mark.asyncio
async def test_loop_calls_tools_until_a_final_reply(monkeypatch):
    calls = []
    audits = []

    async def summary(db, owner, thread_id=None):
        return "", None

    async def facts(db, owner, limit=24):
        return []

    async def audit(db, owner, turn_id, kind, name, detail=None, status="ok", duration_ms=None):
        audits.append((kind, name, status))

    async def decode(ctx, args):
        return {"vin": args["vin"], "specs": {"Make": "INFINITI"}}

    async def propose(ctx, args):
        action = await ctx.propose("product", {"title": "Publish FX35", "summary": "", "details": [], "payload": {}})
        return {"proposed": True, "action_id": action["id"]}

    monkeypatch.setitem(agent_tools.TOOLS, "decode_vin", {**agent_tools.TOOLS["decode_vin"], "handler": decode})
    monkeypatch.setitem(agent_tools.TOOLS, "propose_listing", {**agent_tools.TOOLS["propose_listing"], "handler": propose})
    monkeypatch.setattr(agent_memory, "thread_summary", summary)
    monkeypatch.setattr(agent_memory, "recent_facts", facts)
    monkeypatch.setattr(agent_memory, "audit", audit)

    async def complete(instructions, input_items, tools=None, model=None, max_output_tokens=4000, search=False):
        calls.append(input_items[-1])
        step = len(calls)
        if step == 1:
            return {"output": [{"type": "function_call", "call_id": "c1", "name": "decode_vin", "arguments": json.dumps({"vin": "1" * 17})}],
                    "calls": [{"type": "function_call", "call_id": "c1", "name": "decode_vin", "arguments": json.dumps({"vin": "1" * 17})}],
                    "text": "", "sources": [], "usage": {}}
        if step == 2:
            output = json.loads(input_items[-1]["output"])
            assert output["specs"]["Make"] == "INFINITI"
            call = {"type": "function_call", "call_id": "c2", "name": "propose_listing", "arguments": json.dumps({"publish": True})}
            return {"output": [call], "calls": [call], "text": "", "sources": [], "usage": {}}
        return {"output": [], "calls": [], "text": "Listing prepared and publishing now.", "sources": [], "usage": {}}

    monkeypatch.setattr(agent_loop.agent_provider, "complete", complete)
    stages = []

    async def progress(stage, message=None):
        stages.append(stage)

    turn = {"id": "t1", "auto_approve": True, "timezone": "UTC"}
    reply, sources, actions = await agent_loop.run(None, {"_id": "owner"}, turn, {"request": "list it"}, [], [], [], progress)
    assert reply == "Listing prepared and publishing now."
    assert [action["kind"] for action in actions] == ["product"]
    assert actions[0]["status"] == "queued" and actions[0]["approval"] == "auto"
    assert stages == ["Decoding the VIN", "Preparing the listing"]
    assert ("tool", "decode_vin", "ok") in audits and ("proposal", "product", "ok") in audits


@pytest.mark.asyncio
async def test_tool_errors_are_returned_to_the_model(monkeypatch):
    async def audit(*args, **kwargs):
        return None

    monkeypatch.setattr(agent_memory, "audit", audit)
    ctx = agent_tools.ToolContext(None, {"_id": "owner"}, {"id": "t"}, [], None)
    result = await agent_tools.call(ctx, "decode_vin", json.dumps({"vin": "short"}))
    assert "17-character" in result["error"]
    assert "Unknown tool" in (await agent_tools.call(ctx, "nope", "{}"))["error"]


def test_schedules_parse_and_describe():
    weekly = agent_jobs.parse_schedule("every Monday and Thursday at 7am")
    assert weekly == {"kind": "weekly", "days": [0, 3], "hour": 7, "minute": 0}
    assert agent_jobs.describe_schedule(agent_jobs.parse_schedule("every weekday at 8:00")) == "Weekdays at 08:00"
    assert agent_jobs.next_run(weekly, "America/New_York").weekday() in {0, 3}
