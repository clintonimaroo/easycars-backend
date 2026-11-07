import asyncio
from datetime import datetime, timedelta, timezone
from os import environ
from uuid import uuid4

from bson import ObjectId
from fastapi import FastAPI
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient
import pytest
import pytest_asyncio

from app.routes import agent
from app.routes.auth import get_current_user
from app.services import agent_runtime
from app.utils.mongodb import get_async_database


@pytest_asyncio.fixture
async def database():
    uri = environ.get("EASYCARS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set EASYCARS_TEST_MONGO_URI to run isolated MongoDB integration tests.")
    mongo = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    db = mongo[f"test_agent_conversation_{uuid4().hex}"]
    try:
        yield db
    finally:
        await mongo.drop_database(db.name)
        mongo.close()


@pytest.mark.asyncio
@pytest.mark.parametrize(("message", "calls"), [
    ("How many cars did I log in this month so far?", 0),
    ("Give me a total of the vehicles I put into our system in the current month.", 1),
])
async def test_inventory_count_uses_database_and_only_classifies_if_needed(database, monkeypatch, message, calls):
    owner = ObjectId()
    turn = {
        "_id": str(uuid4()), "owner": str(owner), "lease_id": str(uuid4()), "attachments": [], "status": "running",
        "text": message, "timezone": "America/New_York", "created_at": datetime(2026, 11, 6, 16),
    }
    await database.agent_turns.insert_one(turn)
    await database.vehicles.insert_many([
        {"created_by": str(owner), "created_at": "2026-11-01T04:00:00Z"},
        {"created_by": str(ObjectId()), "created_at": "2026-11-01T04:00:00Z"},
    ])
    classifications = []

    async def classify(*args, schema):
        assert len(classifications) < calls
        assert schema is agent_runtime.Intent
        classifications.append(schema)
        return schema(kind="inventory_count", query="this_month", continues_previous=False)

    monkeypatch.setattr(agent_runtime.agent_provider, "responses", classify)
    result = await agent_runtime._plan(database, {"_id": owner}, turn)
    assert result == ("You logged 1 vehicle for November 2026.", [], [], False)
    assert turn["stage"] == "Counting your inventory records"
    assert len(classifications) == calls


@pytest.mark.asyncio
async def test_copart_monitoring_routes_directly_to_watchlist(database, monkeypatch):
    owner = ObjectId()
    turn = {
        "_id": str(uuid4()), "owner": str(owner), "lease_id": str(uuid4()), "attachments": [], "status": "running",
        "text": "i want u to monitor this car on copart and tell me when the bid opens 55684536",
    }
    await database.agent_turns.insert_one(turn)

    async def unexpected_provider(*args, **kwargs):
        pytest.fail("A Copart Watchlist handoff should bypass OpenAI.")

    monkeypatch.setattr(agent_runtime.agent_provider, "responses", unexpected_provider)
    reply, sources, actions, needs_input = await agent_runtime._plan(database, {"_id": owner}, turn)
    assert "https://www.copart.com/lot/55684536" in reply
    assert needs_input and not actions and not sources
    assert turn["intent"] == "reminder"
    assert agent_runtime._copart_watch_lot("Remind me at 3 pm to watch Copart lot 55684536") is None


@pytest.mark.asyncio
async def test_web_search_exposes_sanitized_query_and_returned_sources(database, monkeypatch):
    owner = ObjectId()
    job_id = str(uuid4())
    source = {"title": "Manufacturer specs", "url": "https://example.invalid/specs"}
    await database.users.insert_one({"_id": owner})
    await database.agent_turns.insert_one({
        "_id": job_id, "owner": str(owner), "status": "queued", "created_at": agent_runtime.now(),
        "text": "Look up the Civic Sport engine.", "timezone": "America/New_York", "attachments": [],
    })

    async def provider(instructions, content, schema=None, search=False):
        if schema is agent_runtime.Intent:
            return schema(
                kind="research", query="Civic Sport owner@example.invalid", continues_previous=False,
            )
        assert search
        running = agent._present(await database.agent_turns.find_one({"_id": job_id}))
        assert running["stage"] == "Searching the web"
        assert running["search_query"] == "Civic Sport [private identifier removed]"
        assert "owner@example.invalid" not in content[0]["text"]
        assert running["progress_messages"][0]["text"] == "I’m checking current sources."
        assert running["progress_messages"][0]["created_at"]
        return (
            f"I found the engine specs.\n{agent_runtime.agent_provider.REPLY_MESSAGE_BREAK}\n"
            f"See [manufacturer specs]({source['url']}).\n{agent_runtime.agent_provider.SUGGESTED_REPLY_MARKER}\n"
            "What are the Sport trim options?",
            [source],
        )

    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent_runtime.agent_provider, "responses", provider)
    assert await agent_runtime.work_once(database)
    completed = agent._present(await database.agent_turns.find_one({"_id": job_id}))
    assert completed["status"] == "completed"
    assert completed["sources"] == [source]
    assert completed["reply_messages"] == ["I found the engine specs.", f"See [manufacturer specs]({source['url']})."]
    assert completed["reply"] == "\n\n".join(completed["reply_messages"])
    assert completed["suggested_reply"] == "What are the Sport trim options?"


@pytest.mark.asyncio
async def test_photo_context_survives_follow_ups_without_reattaching_files(database, monkeypatch):
    owner = ObjectId()
    first_id, legacy_id, new_id, reattach_id = [uuid4() for _ in range(4)]
    timestamp = datetime(2026, 11, 6, tzinfo=timezone.utc)
    photo = {"id": str(ObjectId()), "name": "synthetic.jpg", "type": "image/jpeg", "size": 5}
    await database.users.insert_one({"_id": owner})
    await database["agent_uploads.files"].insert_one({
        "_id": ObjectId(photo["id"]), "filename": photo["name"], "length": photo["size"],
        "metadata": {"owner": str(owner), "type": photo["type"]},
    })
    await database.agent_turns.insert_many([
        {
            "_id": f"{owner}:{first_id}", "id": str(first_id), "owner": str(owner),
            "text": "Find parts using this photo.", "intent": "research", "attachments": [photo],
            "status": "needs_input", "created_at": timestamp, "reply": "OEM or aftermarket?",
        },
        {
            "_id": f"{owner}:{legacy_id}", "id": str(legacy_id), "owner": str(owner),
            "text": "OEM", "intent": "research", "attachments": [photo], "timezone": "America/New_York",
            "status": "failed", "created_at": timestamp + timedelta(seconds=1),
            "search_query": "Old search", "sources": [], "actions": [],
        },
    ])
    app = FastAPI()
    app.include_router(agent.router, prefix="/agent")
    app.dependency_overrides[get_current_user] = lambda: {"_id": owner}
    app.dependency_overrides[get_async_database] = lambda: database
    timestamp += timedelta(seconds=2)
    batches = []

    async def files(db, user, attachments):
        batches.append(attachments)
        return [{"type": "input_image", "image_url": "data:image/jpeg;base64,cGhvdG8="}]

    async def provider(instructions, content, schema=None, **kwargs):
        if schema is agent_runtime.Intent:
            return schema(kind="research", query="Honda Accord parts", continues_previous=True)
        assert schema is agent_runtime.PhotoResearchPlan
        return schema(query=None, visible_parts=[], question="What trim is it?")

    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent_runtime, "configured", lambda: True)
    monkeypatch.setattr(agent_runtime, "now", lambda: timestamp)
    monkeypatch.setattr(agent_runtime, "_file_content", files)
    monkeypatch.setattr(agent_runtime.agent_provider, "responses", provider)
    monkeypatch.setattr(agent_runtime.settings, "AGENT_ALLOW_VEHICLE_PHOTO_ANALYSIS", True)
    monkeypatch.setattr(agent.settings, "ENVIRONMENT", "development")
    headers = {"X-EasyCars-Agent": "1"}

    async with AsyncClient(app=app, base_url="http://test", headers=headers) as client:
        turns = (await client.get("/agent")).json()["turns"]
        assert [turn["attachments"] for turn in turns] == [[photo], []]
        retry = await client.post(f"/agent/turns/{legacy_id}/retry")
        assert retry.status_code == 200
        assert retry.json()["turn"]["search_query"] is None
        assert retry.json()["turn"]["attachments"] == []
        assert await agent_runtime.work_once(database)

        for turn_id, explicit in [(new_id, []), (reattach_id, [photo])]:
            timestamp += timedelta(seconds=1)
            posted = await client.post("/agent/turns", json={
                "id": str(turn_id), "text": "Look near Dallas.", "timezone": "America/New_York",
                "attachment_ids": [file["id"] for file in explicit], "auto_approve": False,
            })
            assert posted.status_code == 202
            assert posted.json()["turn"]["attachments"] == explicit
            assert await agent_runtime.work_once(database)
            turn = (await client.get("/agent")).json()["turns"][-1]
            assert turn["status"] == "needs_input"
            assert turn["attachments"] == explicit
            assert "context_attachments" not in turn

    assert batches == [[photo], [photo], [photo]]


@pytest.mark.asyncio
@pytest.mark.parametrize("pdf_source", ["attached", "extraction", "previous_lookup"])
async def test_add_inventory_uses_pdf_and_approval_flow(database, monkeypatch, pdf_source):
    owner = ObjectId()
    job_id = str(uuid4())
    timestamp = agent_runtime.now()
    file = {"id": str(ObjectId()), "name": "synthetic.pdf", "type": "application/pdf", "size": 5}
    fields = {
        "year": 2015, "make": "Example", "model": "Vehicle", "vin": "1TEST23456TEST789",
        "price": 9000, "mileage": 50000, "color": "Blue", "status": "available", "stockNo": "T-100",
    }
    await database.users.insert_one({"_id": owner})
    fresh_pdf = pdf_source == "attached"
    if not fresh_pdf:
        await database.agent_turns.insert_one({
            "_id": str(uuid4()), "owner": str(owner), "status": "completed", "intent": "chat",
            "created_at": timestamp - timedelta(seconds=2), "attachments": [file], "text": "",
            "reply": "I read the vehicle paperwork.",
        })
    if pdf_source == "previous_lookup":
        await database.agent_turns.insert_one({
            "_id": str(uuid4()), "owner": str(owner), "status": "needs_input", "intent": "inventory",
            "created_at": timestamp - timedelta(seconds=1), "attachments": [], "text": "Add vehicle to inventory",
            "reply": "Which vehicle should I look up in inventory?",
        })
    await database.agent_turns.insert_one({
        "_id": job_id, "id": job_id, "owner": str(owner), "status": "queued", "created_at": timestamp,
        "text": "Add vehicle to inventory", "timezone": "America/New_York", "attachments": [file] if fresh_pdf else [],
        "auto_approve": fresh_pdf, "actions": [],
    })

    async def files(db, user, attachments):
        assert attachments == [file]
        return [{"type": "input_file", "filename": "synthetic.pdf"}]

    async def provider(instructions, content, schema=None, **kwargs):
        assert schema is agent_runtime.AddInventoryPlan
        assert content[-1]["type"] == "input_file"
        return schema(vehicle=agent_runtime.InventoryVehicle(**fields), sale=None, question=None)

    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent_runtime.settings, "AGENT_ALLOW_PRIVATE_FILE_INPUTS", True)
    monkeypatch.setattr(agent_runtime, "_file_content", files)
    monkeypatch.setattr(agent_runtime.agent_provider, "responses", provider)
    assert await agent_runtime.work_once(database)
    turn = await database.agent_turns.find_one({"_id": job_id})
    assert turn["intent"] == "add_inventory"
    assert turn["attachments"] == ([file] if fresh_pdf else [])
    assert turn["context_attachments"] == ([] if fresh_pdf else [file])
    action, = turn["actions"]
    assert action["kind"] == "add_inventory"
    if fresh_pdf:
        assert turn["status"] == "completed"
        assert action["status"] == "completed"
    else:
        assert turn["status"] == "awaiting_approval"
        assert action["status"] == "pending"
        assert await database.vehicles.count_documents({}) == 0
        await database.agent_turns.update_one({"_id": job_id}, {"$set": {
            "status": "queued", "actions.0.status": "queued", "actions.0.approved_by": str(owner),
        }})
        assert await agent_runtime.work_once(database)
        action = (await database.agent_turns.find_one({"_id": job_id}))["actions"][0]
        assert action["status"] == "completed"
    vehicle = await database.vehicles.find_one({"vin": fields["vin"]})
    assert vehicle["status"] == "available"
    assert action["result"]["url"] == f"/inventory/{vehicle['_id']}"
    retry = await agent_runtime.agent_sales.execute_inventory_vehicle(
        database, {"_id": owner}, action["payload"], f"{job_id}:{action['id']}",
    )
    assert retry == action["result"]
    assert await database.vehicles.count_documents({}) == 1


@pytest.mark.asyncio
async def test_running_job_renews_lease_and_recovers_after_cancellation(database, monkeypatch):
    owner = ObjectId()
    timestamp = datetime(2026, 11, 6, tzinfo=timezone.utc)
    started = asyncio.Event()
    job_id = str(uuid4())

    async def interrupted_plan(*args):
        started.set()
        await asyncio.Event().wait()

    async def recovered_plan(*args):
        return "Recovered reply.", [], [], False

    monkeypatch.setattr(agent_runtime, "now", lambda: timestamp)
    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent_runtime, "LEASE_RENEWAL_SECONDS", 0.01)
    monkeypatch.setattr(agent_runtime, "_plan", interrupted_plan)
    await database.users.insert_one({"_id": owner})
    await database.agent_turns.insert_one({
        "_id": job_id, "owner": str(owner), "status": "queued", "created_at": timestamp,
        "work_started_at": timestamp, "actions": [],
    })
    worker = asyncio.create_task(agent_runtime.work_once(database))
    try:
        await asyncio.wait_for(started.wait(), timeout=2)
        timestamp += timedelta(seconds=25)
        expected_lease = (timestamp + agent_runtime.LEASE).replace(tzinfo=None)
        async with asyncio.timeout(2):
            while (await database.agent_turns.find_one({"_id": job_id}))["lease_until"] < expected_lease:
                await asyncio.sleep(0.01)
        assert not await agent_runtime.work_once(database)
    finally:
        worker.cancel()
        with pytest.raises(asyncio.CancelledError):
            await worker

    queued = await database.agent_turns.find_one({"_id": job_id})
    assert queued["status"] == "queued" and queued["lease_id"] is None
    assert queued["work_started_at"] is None

    monkeypatch.setattr(agent_runtime, "_plan", recovered_plan)
    await database.agent_turns.update_one({"_id": job_id}, {"$set": {
        "status": "running", "lease_id": "lost-lease", "lease_until": timestamp - timedelta(seconds=1),
        "work_started_at": timestamp - timedelta(minutes=12),
    }})
    assert await agent_runtime.work_once(database)
    result = await database.agent_turns.find_one({"_id": job_id})
    assert result["status"] == "completed"
    assert result["attempts"] == 2
    assert result["work_started_at"] == timestamp.replace(tzinfo=None)
    assert result["work_finished_at"] == result["work_started_at"]


@pytest.mark.asyncio
async def test_account_preference_and_server_elapsed_time_survive_a_new_session(database, monkeypatch):
    first_owner, second_owner = ObjectId(), ObjectId()
    owner = first_owner
    timestamp = datetime(2026, 11, 6, tzinfo=timezone.utc)
    app = FastAPI()
    app.include_router(agent.router, prefix="/agent")
    app.dependency_overrides[get_current_user] = lambda: {"_id": owner}
    app.dependency_overrides[get_async_database] = lambda: database

    async def plan(*args):
        return "Synthetic reply.", [], [], False

    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent_runtime, "configured", lambda: True)
    monkeypatch.setattr(agent_runtime, "now", lambda: timestamp)
    monkeypatch.setattr(agent_runtime, "_plan", plan)
    monkeypatch.setattr(agent.settings, "ENVIRONMENT", "development")
    await database.users.insert_many([{"_id": first_owner}, {"_id": second_owner}])
    headers = {"X-EasyCars-Agent": "1"}
    message = {
        "id": str(uuid4()), "text": "Synthetic chat.", "attachment_ids": [],
        "auto_approve": True, "timezone": "America/New_York",
    }

    async with AsyncClient(app=app, base_url="http://test") as client:
        assert (await client.get("/agent")).json()["settings"] == {"auto_approve": False}
        assert (await client.patch("/agent/settings", headers=headers, json={"auto_approve": "false"})).status_code == 422
        assert (await client.patch("/agent/settings", json={"auto_approve": True})).status_code == 403
        saved = await client.patch("/agent/settings", headers=headers, json={"auto_approve": True})
        assert saved.json() == {"auto_approve": True}
        await client.patch("/agent/memory", headers=headers, json={"preferences": "Example preference"})
        owner = second_owner
        assert (await client.get("/agent")).json()["settings"] == {"auto_approve": False}
        owner = first_owner

        posted = await client.post("/agent/turns", headers=headers, json=message)
        pending = posted.json()["turn"]
        assert posted.status_code == 202
        assert pending["status"] == "queued"
        assert pending["work_started_at"] == pending["created_at"]
        assert pending["work_finished_at"] is None

    async with AsyncClient(app=app, base_url="http://test") as client:
        restored = (await client.get("/agent")).json()
        assert restored["settings"] == {"auto_approve": True}
        assert restored["memory"] == {"preferences": "Example preference"}
        assert restored["turns"] == [pending]

        timestamp += timedelta(seconds=3, milliseconds=200)
        assert await agent_runtime.work_once(database)
        settled = (await client.get("/agent")).json()["turns"][0]
        started = datetime.fromisoformat(settled["work_started_at"])
        finished = datetime.fromisoformat(settled["work_finished_at"])
        assert settled["status"] == "completed"
        assert started.utcoffset() == timedelta(0)
        assert finished - started == timedelta(seconds=3, milliseconds=200)

        timestamp += timedelta(seconds=30)
        repeated = await client.post("/agent/turns", headers=headers, json=message)
        assert repeated.json()["turn"] == settled


@pytest.mark.asyncio
@pytest.mark.parametrize("running", [False, True])
async def test_stop_turn_retains_message_and_prevents_late_reply(database, monkeypatch, running):
    owner = ObjectId()
    account = owner
    turn_id = uuid4()
    key = {"_id": f"{owner}:{turn_id}"}
    timestamp = datetime(2026, 11, 6, tzinfo=timezone.utc)
    attachments = [{"id": str(ObjectId()), "name": "example.pdf"}]
    entered = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.include_router(agent.router, prefix="/agent")
    app.dependency_overrides[get_current_user] = lambda: {"_id": account}
    app.dependency_overrides[get_async_database] = lambda: database

    async def plan(*args):
        entered.set()
        await release.wait()
        return "Late reply.", [], [], False

    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent_runtime, "configured", lambda: False)
    monkeypatch.setattr(agent_runtime, "_plan", plan)
    monkeypatch.setattr(agent.settings, "ENVIRONMENT", "development")
    await database.users.insert_one({"_id": owner})
    await database.agent_turns.insert_one({
        **key, "owner": str(owner), "id": str(turn_id), "status": "queued", "text": "Keep this message.",
        "attachments": attachments, "created_at": timestamp, "work_started_at": timestamp, "actions": [],
    })
    worker = asyncio.create_task(agent_runtime.work_once(database)) if running else None
    path = f"/agent/turns/{turn_id}/stop"
    headers = {"X-EasyCars-Agent": "1"}
    try:
        if worker:
            await asyncio.wait_for(entered.wait(), timeout=2)
        async with AsyncClient(app=app, base_url="http://test") as client:
            assert (await client.post(path)).status_code == 403
            account = ObjectId()
            assert (await client.post(path, headers=headers)).status_code == 404
            account = owner
            response = await client.post(path, headers=headers)
            assert response.status_code == 200
            stopped = response.json()["turn"]
            assert stopped["status"] == "stopped"
            assert stopped["text"] == "Keep this message."
            assert stopped["attachments"] == attachments
            assert stopped["work_finished_at"]
            assert (await client.post(path, headers=headers)).json() == {"turn": stopped}
    finally:
        release.set()
        if worker:
            assert not await asyncio.wait_for(worker, timeout=2)

    assert not await agent_runtime.work_once(database)
    saved = await database.agent_turns.find_one(key)
    assert saved["lease_id"] is None and saved["lease_until"] is None
    assert saved["reply"] == "" and saved["actions"] == []


@pytest.mark.asyncio
async def test_stop_turn_preserves_an_action_once_it_starts(database, monkeypatch):
    owner = ObjectId()
    turn_id = uuid4()
    key = {"_id": f"{owner}:{turn_id}"}
    timestamp = datetime(2026, 11, 6, tzinfo=timezone.utc)
    entered = asyncio.Event()
    release = asyncio.Event()
    app = FastAPI()
    app.include_router(agent.router, prefix="/agent")
    app.dependency_overrides[get_current_user] = lambda: {"_id": owner}
    app.dependency_overrides[get_async_database] = lambda: database

    async def execute_reminder(*args):
        entered.set()
        await release.wait()
        return {"message": "Reminder created."}

    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent_runtime, "configured", lambda: True)
    monkeypatch.setattr(agent_runtime.agent_actions, "execute_reminder", execute_reminder)
    monkeypatch.setattr(agent.settings, "ENVIRONMENT", "development")
    await database.users.insert_one({"_id": owner})
    await database.agent_turns.insert_one({
        **key, "owner": str(owner), "id": str(turn_id), "status": "queued", "created_at": timestamp,
        "planned": True, "actions": [{
            "id": str(uuid4()), "kind": "reminder", "title": "Create reminder", "payload": {},
            "status": "queued", "approved_by": str(owner),
        }],
    })
    worker = asyncio.create_task(agent_runtime.work_once(database))
    path = f"/agent/turns/{turn_id}"
    headers = {"X-EasyCars-Agent": "1"}
    try:
        await asyncio.wait_for(entered.wait(), timeout=2)
        async with AsyncClient(app=app, base_url="http://test") as client:
            assert (await client.post(f"{path}/stop", headers=headers)).status_code == 409
    finally:
        release.set()
        assert await asyncio.wait_for(worker, timeout=2)

    completed = await database.agent_turns.find_one(key)
    assert completed["status"] == "completed"
    assert completed["actions"][0]["result"] == {"message": "Reminder created."}

    await database.agent_turns.update_one(key, {"$set": {"status": "failed", "actions.0.status": "failed"},
                                                "$unset": {"actions.0.started": ""}})
    async with AsyncClient(app=app, base_url="http://test") as client:
        assert (await client.post(f"{path}/retry", headers=headers)).status_code == 200
        assert (await client.post(f"{path}/stop", headers=headers)).status_code == 409


@pytest.mark.asyncio
async def test_reaction_is_persisted_for_the_reply_owner_only(database, monkeypatch):
    owner, turn_id = ObjectId(), uuid4()
    account = owner
    key = {"_id": f"{owner}:{turn_id}"}
    app = FastAPI()
    app.include_router(agent.router, prefix="/agent")
    app.dependency_overrides[get_current_user] = lambda: {"_id": account}
    app.dependency_overrides[get_async_database] = lambda: database
    monkeypatch.setattr(agent_runtime, "authorized", lambda user: True)
    monkeypatch.setattr(agent.settings, "ENVIRONMENT", "development")
    await database.agent_turns.insert_one({
        **key, "owner": str(owner), "id": str(turn_id), "status": "running", "reply": "A synthetic reply.",
    })
    path = f"/agent/turns/{turn_id}/reaction"
    headers = {"X-EasyCars-Agent": "1"}

    async with AsyncClient(app=app, base_url="http://test") as client:
        assert (await client.patch(path, headers=headers, json={"reaction": "👍"})).status_code == 404
        await database.agent_turns.update_one(key, {"$set": {"status": "completed"}})
        assert (await client.patch(path, json={"reaction": "👍"})).status_code == 403
        assert (await client.patch(path, headers=headers, json={"reaction": "🙃"})).status_code == 422

        account = ObjectId()
        assert (await client.patch(path, headers=headers, json={"reaction": "👍"})).status_code == 404
        assert (await client.get("/agent")).json()["turns"] == []

        account = owner
        for value in ("👍", "👍", "🔥", None):
            saved = await client.patch(path, headers=headers, json={"reaction": value})
            assert saved.status_code == 200
            assert saved.json() == {"reaction": value}
            assert (await client.get("/agent")).json()["turns"][0]["reaction"] == value
