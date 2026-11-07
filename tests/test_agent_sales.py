import asyncio
import json
import os
from datetime import timedelta
from uuid import uuid4

import pytest
import pytest_asyncio
from bson import ObjectId
from fastapi import FastAPI, HTTPException
from httpx import AsyncClient
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorClientSession, AsyncIOMotorGridFSBucket
from pymongo.errors import OperationFailure

from app.routes import agent, documents
from app.routes.auth import get_current_user
from app.services import agent_runtime, agent_sales
from app.services.agent_sales import execute_inventory_vehicle, execute_sale, prepare_inventory_vehicle, prepare_sale

SALE = {
    "vin": "1HGCM82633A004352",
    "year": 2003,
    "make": "Honda",
    "model": "Accord",
    "price": 5500,
    "mileage": 135000,
    "color": "White",
    "sale_date": "2024-11-01",
    "buyer_name": "Example Customer",
    "buyer_email": "customer@example.com",
    "stock_number": "STK 42",
}
SAMPLE_PDF = b"%PDF-1.7 synthetic test file"


@pytest_asyncio.fixture
async def db():
    uri = os.getenv("EASYCARS_TEST_REPLICA_URI")
    if not uri:
        pytest.skip("Set EASYCARS_TEST_REPLICA_URI to run against an isolated test replica set.")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=5000)
    database = client[f"test_agent_sales_{uuid4().hex}"]
    try:
        yield database
    finally:
        await client.drop_database(database.name)
        client.close()


async def stage_pdf(db, owner):
    files = AsyncIOMotorGridFSBucket(db, bucket_name="agent_uploads")
    return str(await files.upload_from_stream(
        "Example sale.pdf", SAMPLE_PDF, metadata={"owner": str(owner["_id"]), "type": "application/pdf"},
    ))


@pytest.mark.asyncio
async def test_sale_clarifications_keep_paperwork_and_prior_answers_until_saved(db, monkeypatch):
    user = {"_id": ObjectId()}
    owner = str(user["_id"])
    await db.users.insert_one(user)
    file_id = await stage_pdf(db, user)
    file = {"id": file_id, "type": "application/pdf", "name": "Example sale.pdf", "size": len(SAMPLE_PDF)}
    source = {"attachment_id": file_id, "page": 1, "document_type": "bill_of_sale"}
    timestamp = agent_runtime.now()
    messages = ["Record this sale from the paperwork.", "November 1, 2024.", "customer@example.com"]

    async def provider(instructions, content, schema=None, **kwargs):
        request = json.loads(content[0]["text"])
        index = messages.index(request["request"])
        if schema is agent_runtime.Intent:
            if index:
                assert request["pending_kind"] == "sales"
                assert request["attached_files"] == 0
            return schema(kind="chat" if index else "sales", query=None, continues_previous=bool(index))
        assert schema is agent_runtime.SalesPlan
        assert content[-1]["type"] == "input_file"
        if index:
            prior, = request["pending_import"]
            assert prior["fields"]["source"] == [source]
            assert prior["fields"]["sale_date"] == (SALE["sale_date"] if index == 2 else None)
            fields = prior["fields"].copy()
        else:
            fields = {**dict.fromkeys(agent_runtime.Sale.model_fields), **SALE, "sale_date": None,
                      "buyer_email": None, "source": [source]}
        if index == 1:
            fields["sale_date"] = SALE["sale_date"]
        if index == 2:
            fields["buyer_email"] = request["request"]
        return schema(sales=[agent_runtime.Sale.model_validate(fields)], question=None)

    monkeypatch.setattr(agent_runtime, "authorized", lambda actor: True)
    monkeypatch.setattr(agent_runtime.settings, "AGENT_ALLOW_PRIVATE_FILE_INPUTS", True)
    monkeypatch.setattr(agent_runtime.agent_provider, "responses", provider)

    for index, message in enumerate(messages):
        turn_id = str(uuid4())
        await db.agent_turns.insert_one({
            "_id": turn_id, "id": turn_id, "owner": owner, "status": "queued",
            "created_at": timestamp + timedelta(seconds=index), "timezone": "America/New_York",
            "text": message, "attachments": [file] if not index else [], "auto_approve": True, "actions": [],
        })
        assert await agent_runtime.work_once(db)
        turn = await db.agent_turns.find_one({"_id": turn_id})
        assert turn["context_attachments"] == ([file] if index else [])
        assert "pending_import" not in agent._present(turn)
        if index < 2:
            assert turn["status"] == "needs_input"
            assert "email or phone number" in turn["reply"]
            assert ("original sale date" in turn["reply"]) == (index == 0)
            assert not turn["actions"]
            assert await db.vehicles.count_documents({}) == 0
        else:
            assert turn["status"] == "completed"
            action, = turn["actions"]
            assert action["status"] == "completed"
            assert action["result"]["document_ids"]
            assert turn["pending_import"] == []

    vehicle = await db.vehicles.find_one({"vin": SALE["vin"]})
    assert vehicle["status"] == "sold" and vehicle["dateSold"] == SALE["sale_date"]
    assert await db.customers.count_documents({"vehicles": str(vehicle["_id"])}) == 1
    assert await db.documents.count_documents({"vehicleId": str(vehicle["_id"])}) == 1


@pytest.mark.asyncio
async def test_concurrent_sale_retries_attach_one_private_copy(db):
    user = {"_id": ObjectId()}
    first_file, second_file = await asyncio.gather(stage_pdf(db, user), stage_pdf(db, user))
    async def prepare(file_id):
        return await prepare_sale(db, user, {
            **SALE, "source": [
                {"attachment_id": file_id, "page": 1, "document_type": "bill_of_sale"},
                {"attachment_id": file_id, "page": 2, "document_type": "odometer_certification"},
            ],
        }, [{"id": file_id}])

    first, second = await asyncio.gather(prepare(first_file), prepare(second_file))
    results = await asyncio.gather(
        execute_sale(db, user, first["payload"], "first"),
        execute_sale(db, user, first["payload"], "first"),
        execute_sale(db, user, second["payload"], "second"),
    )
    assert all(result == results[0] for result in results)
    vehicle = await db.vehicles.find_one({})
    assert vehicle["status"] == "sold"
    assert vehicle["dateSold"] == "2024-11-01"
    assert vehicle["hasBillOfSale"] is True
    assert vehicle["hasOdometerCertification"] is True
    assert await db.vehicles.count_documents({}) == 1
    assert await db.customers.count_documents({"vehicles": str(vehicle["_id"])}) == 1
    assert await db.documents.count_documents({"vehicleId": str(vehicle["_id"])}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transient", "unknown", "permanent"])
async def test_sale_transaction_retries_commit_with_correct_scope(db, monkeypatch, failure):
    user = {"_id": ObjectId()}
    proposal = await prepare_sale(db, user, SALE, [])
    commit = AsyncIOMotorClientSession.commit_transaction
    save = agent_sales._commit_sale
    commits = 0
    saves = 0

    async def commit_with_failure(session):
        nonlocal commits
        commits += 1
        if commits == 1:
            if failure == "unknown":
                await commit(session)
                raise OperationFailure("Commit reply lost", 91, {"errorLabels": ["UnknownTransactionCommitResult"]})
            if failure == "transient":
                raise OperationFailure("Write conflict", 112, {"errorLabels": ["TransientTransactionError"]})
            raise OperationFailure("Permission denied", 13)
        return await commit(session)

    async def count_save(*args):
        nonlocal saves
        saves += 1
        return await save(*args)

    monkeypatch.setattr(AsyncIOMotorClientSession, "commit_transaction", commit_with_failure)
    monkeypatch.setattr(agent_sales, "_commit_sale", count_save)
    if failure == "permanent":
        with pytest.raises(OperationFailure) as error:
            await execute_sale(db, user, proposal["payload"], "commit-failure")
        assert error.value.code == 13
        assert saves == commits == 1
        for collection in (db.vehicles, db.customers, db.agent_sale_receipts):
            assert await collection.count_documents({}) == 0
    else:
        result = await execute_sale(db, user, proposal["payload"], "commit-failure")
        assert result["vehicle_id"]
        assert saves == (2 if failure == "transient" else 1)
        assert commits == 2
        for collection in (db.vehicles, db.customers, db.agent_sale_receipts):
            assert await collection.count_documents({}) == 1
        assert await execute_sale(db, user, proposal["payload"], "commit-failure") == result
        assert commits == 2


@pytest.mark.asyncio
async def test_conflicting_concurrent_stock_creates_only_one_sale(db):
    user = {"_id": ObjectId()}
    first = await prepare_sale(db, user, SALE, [])
    second = await prepare_sale(db, user, {**SALE, "vin": "JH4DA9350MS000095"}, [])
    results = await asyncio.gather(
        execute_sale(db, user, first["payload"], "first"),
        execute_sale(db, user, second["payload"], "second"),
        return_exceptions=True,
    )
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    assert await db.vehicles.count_documents({}) == 1
    assert await db.agent_sale_receipts.count_documents({}) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("first_kind", ["sale", "inventory"])
async def test_sale_and_inventory_share_stock_lock(db, monkeypatch, first_kind):
    user = {"_id": ObjectId()}
    sale = await prepare_sale(db, user, SALE, [])
    inventory = await prepare_inventory_vehicle(db, user, {
        "vin": "JH4DA9350MS000095", "year": 1991, "make": "Acura", "model": "Integra",
        "price": 4500, "mileage": 140000, "color": "Black", "status": "available",
        "stockNo": SALE["stock_number"].lower(),
    })
    started = asyncio.Event()
    release = asyncio.Event()
    original = agent_sales._find_vehicle
    paused = False

    async def overlap(*args, **kwargs):
        nonlocal paused
        result = await original(*args, **kwargs)
        if not paused:
            paused = True
            started.set()
            await release.wait()
        return result

    monkeypatch.setattr(agent_sales, "_find_vehicle", overlap)
    operations = {
        "sale": lambda: execute_sale(db, user, sale["payload"], "sale-stock-race"),
        "inventory": lambda: execute_inventory_vehicle(db, user, inventory["payload"], "inventory-stock-race"),
    }
    first = asyncio.create_task(operations[first_kind]())
    await asyncio.wait_for(started.wait(), 5)
    second = asyncio.create_task(operations["inventory" if first_kind == "sale" else "sale"]())
    try:
        await asyncio.wait_for(asyncio.shield(second), 0.2)
    except (asyncio.TimeoutError, HTTPException):
        pass
    finally:
        release.set()
    results = await asyncio.gather(first, second, return_exceptions=True)
    assert sum(isinstance(result, dict) for result in results) == 1
    assert sum(isinstance(result, HTTPException) and result.status_code == 409 for result in results) == 1
    assert await db.vehicles.count_documents({}) == 1
    first_won = isinstance(results[0], dict)
    winner = first_kind if first_won else ("inventory" if first_kind == "sale" else "sale")
    assert await db.customers.count_documents({}) == (1 if winner == "sale" else 0)
    receipt = await operations[winner]()
    assert receipt == results[0 if first_won else 1]
    assert await db.vehicles.count_documents({}) == 1


@pytest.mark.asyncio
async def test_inventory_stock_lock_preserves_unrelated_legacy_stock_duplicates(db):
    await db.vehicles.insert_many([
        {"vin": "1HGCM82633A004352", "stockNo": "OLD 1"},
        {"vin": "JH4DA9350MS000095", "stockNo": "OLD 1"},
    ])
    user = {"_id": ObjectId()}
    proposal = await prepare_inventory_vehicle(db, user, {
        "vin": "1M8GDM9AXKP042788", "year": 1989, "make": "Example", "model": "Vehicle",
        "price": 4500, "mileage": 140000, "color": "Black", "status": "available", "stockNo": "NEW 1",
    })
    result = await execute_inventory_vehicle(db, user, proposal["payload"], "inventory-legacy")
    assert result["vehicle_id"]
    assert await db.vehicles.count_documents({"stockNo": "OLD 1"}) == 2
    assert await db.vehicles.count_documents({"stockNo": "NEW 1"}) == 1


@pytest.mark.asyncio
async def test_reusing_another_vehicles_document_leaves_no_partial_sale(db):
    user = {"_id": ObjectId()}
    file_id = await stage_pdf(db, user)
    source = [{"attachment_id": file_id, "page": 1, "document_type": "bill_of_sale"}]
    attachments = [{"id": file_id}]
    first = await prepare_sale(db, user, {**SALE, "source": source}, attachments)
    await execute_sale(db, user, first["payload"], "first")

    other_sale = {
        **SALE, "vin": "JH4DA9350MS000095", "stock_number": "STK 99",
        "buyer_name": "Another Customer", "buyer_email": "another@example.com", "source": source,
    }
    second = await prepare_sale(db, user, other_sale, attachments)
    with pytest.raises(HTTPException) as error:
        await execute_sale(db, user, second["payload"], "second")
    assert error.value.status_code == 409
    assert await db.vehicles.count_documents({}) == 1
    assert await db.customers.count_documents({}) == 1
    assert await db.documents.count_documents({}) == 1


@pytest.mark.asyncio
async def test_document_requires_auth_and_owner_and_supports_pdf_ranges(db, monkeypatch):
    user = {"_id": ObjectId()}
    other = {"_id": ObjectId(), "role": "admin"}
    file_id = await stage_pdf(db, user)
    proposal = await prepare_sale(db, user, {
        **SALE, "source": [{"attachment_id": file_id, "page": 1, "document_type": "bill_of_sale"}],
    }, [{"_id": file_id}])
    await execute_sale(db, user, proposal["payload"], "sale")
    monkeypatch.setattr(documents.settings, "DOCUMENTS_ADMIN_USER_IDS", "")

    async def test_database():
        return db

    monkeypatch.setattr(documents, "get_async_database", test_database)
    app = FastAPI()
    app.include_router(documents.router, prefix="/documents")
    app.include_router(documents.router, prefix="/inventory/documents")
    async with AsyncClient(app=app, base_url="http://test") as client:
        for prefix in ("/documents", "/inventory/documents"):
            path = f"{prefix}/preview/{file_id}"
            app.dependency_overrides.clear()
            assert (await client.get(path)).status_code == 401
            app.dependency_overrides[get_current_user] = lambda: other
            assert (await client.get(path)).status_code == 404
            app.dependency_overrides[get_current_user] = lambda: user
            response = await client.get(path, headers={"Range": "bytes=0-7"})
            assert response.status_code == 206
            assert response.headers["content-type"] == "application/pdf"
            assert response.headers["content-range"] == f"bytes 0-7/{len(SAMPLE_PDF)}"
            assert response.headers["cache-control"] == "private, no-store"
            assert response.content == SAMPLE_PDF[:8]
            download = await client.get(f"{prefix}/download/{file_id}")
            assert download.status_code == 200
            assert download.content == SAMPLE_PDF
