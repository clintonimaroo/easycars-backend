import asyncio
import base64
from datetime import datetime, timedelta, timezone
import json
from os import environ
import re
from uuid import uuid4

from bson import ObjectId
from fastapi import HTTPException
import httpx
from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorGridFSBucket
import pytest
import pytest_asyncio

from app.services import agent_actions as actions


OWNER = {"_id": ObjectId("507f1f77bcf86cd799439011")}
STORE = "https://shop.example.test"
PRODUCTS = "/wp-json/wc/v3/products"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR2QAAAAASUVORK5CYII="
)


@pytest_asyncio.fixture
async def database():
    uri = environ.get("EASYCARS_TEST_MONGO_URI")
    if not uri:
        pytest.skip("Set EASYCARS_TEST_MONGO_URI to run isolated MongoDB integration tests.")
    client = AsyncIOMotorClient(uri, serverSelectionTimeoutMS=1500)
    name = f"easycars_agent_actions_test_{uuid4().hex}"
    try:
        await client.admin.command("ping")
        yield client[name]
    finally:
        await client.drop_database(name)
        client.close()


@pytest.fixture
def wordpress(monkeypatch):
    config = {
        "SECRET_KEY": "test-only-signing-key-independent-from-default",
        "WOOCOMMERCE_STORE_URL": STORE,
        "WOOCOMMERCE_CONSUMER_KEY": "test-woo-key",
        "WOOCOMMERCE_CONSUMER_SECRET": "test-woo-secret",
        "WOOCOMMERCE_ALLOWED_USER_IDS": str(OWNER["_id"]),
        "WORDPRESS_USERNAME": "test-upload-user",
        "WORDPRESS_APPLICATION_PASSWORD": "test-upload-password",
    }
    for field, value in config.items():
        monkeypatch.setattr(actions.settings, field, value)
    product = {}
    media = {}
    writes = []

    def mock_store(request):
        assert request.url.host == "shop.example.test"
        method, path = request.method, request.url.path
        if method == "GET" and path == PRODUCTS:
            return httpx.Response(200, json=[product] if product else [])
        if method == "GET" and path == f"{PRODUCTS}/12":
            return httpx.Response(200, json=product)
        if method == "GET" and path == actions.MEDIA_ROUTE:
            return httpx.Response(200, json=[media] if media else [])
        writes.append((method, path))
        if method == "POST" and path == PRODUCTS:
            product.update(json.loads(request.content), id=12, images=[], permalink=f"{STORE}/product/civic/")
        elif method == "POST" and path == actions.MEDIA_ROUTE:
            slug = re.search(rb"(easycars-photo-[a-f0-9]{24})\.png", request.content)[1].decode()
            media.update(id=24, slug=slug, post=12)
        elif method == "PUT" and path == f"{PRODUCTS}/12":
            product.update(json.loads(request.content))
        else:
            pytest.fail(f"Unexpected WordPress request {method} {path}")
        raise httpx.ReadTimeout("Lost response after the write", request=request)

    client = httpx.AsyncClient
    monkeypatch.setattr(actions.httpx, "AsyncClient", lambda **kwargs: client(
        transport=httpx.MockTransport(mock_store), **kwargs,
    ))
    return product, writes


@pytest.mark.asyncio
async def test_counts_logged_vehicles_by_creator_and_local_period(database):
    owner = OWNER["_id"]
    instant = datetime(2026, 11, 6, 16, tzinfo=timezone.utc)
    await database.vehicles.insert_many([
        {"created_by": str(owner), "created_at": datetime(2026, 11, 1, 3, 59, 59, tzinfo=timezone.utc)},
        {"created_by": str(owner), "created_at": datetime(2026, 11, 1, 4, tzinfo=timezone.utc)},
        {"created_by": owner, "created_at": "2026-11-06T10:00:00-05:00"},
        {"created_by": str(owner), "createdAt": "2026-11-06T14:00:00Z"},
        {"created_by": str(owner), "created_at": instant},
        {"created_by": str(owner), "created_at": "2026-11-06T16:00:01Z"},
        {"created_by": str(owner), "created_at": "invalid"},
        {"created_by": str(owner), "created_at": 1793980800000},
        {"created_by": str(ObjectId()), "created_at": "2026-11-06T14:00:00Z"},
        {"created_at": "2026-11-06T14:00:00Z"},
    ])

    result = await actions.count_logged_vehicles(database, OWNER, "this month", "America/New_York", instant)
    assert result == {
        "count": 3, "period": "November 2026",
        "start_at": "2026-11-01T00:00:00-04:00", "end_at": "2026-11-06T11:00:00-05:00",
        "timezone": "America/New_York",
    }
    for period, count in [("today", 2), ("this_week", 2), ("last_month", 1)]:
        result = await actions.count_logged_vehicles(database, OWNER, period, "America/New_York", instant)
        assert result["count"] == count


@pytest.mark.asyncio
async def test_publish_reconciles_lost_store_responses_and_retries(database, wordpress):
    product, writes = wordpress
    bucket = AsyncIOMotorGridFSBucket(database, bucket_name="agent_uploads")
    photo_id = str(await bucket.upload_from_stream(
        "civic.png", PNG, metadata={"owner": str(OWNER["_id"]), "type": "image/png"},
    ))
    args = {
        "name": "2020 Honda Civic", "description": "Example listing", "regular_price": "18000",
        "sku": None, "stock_status": "instock", "photo_ids": [photo_id], "publish": True,
    }
    proposal = await actions.prepare_product(database, OWNER, args, [{"id": photo_id}])
    for _ in range(2):
        result = await actions.execute_product(database, OWNER, proposal["payload"], "publish-test")
        assert result == {
            "status": "publish", "product_id": 12, "link": f"{STORE}/product/civic/", "name": "2020 Honda Civic",
        }
    assert product["images"] == [{"id": 24}]
    assert writes == [
        ("POST", PRODUCTS), ("POST", actions.MEDIA_ROUTE), ("PUT", f"{PRODUCTS}/12"), ("PUT", f"{PRODUCTS}/12"),
    ]


@pytest.mark.asyncio
async def test_publishing_an_existing_draft_rechecks_approved_content(database, wordpress):
    product, writes = wordpress
    bucket = AsyncIOMotorGridFSBucket(database, bucket_name="agent_uploads")
    photo_id = str(await bucket.upload_from_stream(
        "civic.png", PNG, metadata={"owner": str(OWNER["_id"]), "type": "image/png"},
    ))
    proposal = await actions.prepare_product(database, OWNER, {
        "name": "2020 Honda Civic", "description": "Example listing", "regular_price": "18000",
        "stock_status": "instock", "photo_ids": [photo_id], "publish": False,
    }, [{"id": photo_id}])
    draft = await actions.execute_product(database, OWNER, proposal["payload"], "draft-test")
    assert draft["status"] == "draft"
    approved = await actions.prepare_product(database, OWNER, {
        "product_id": draft["product_id"], "publish": True,
    }, [])
    product["regular_price"] = "19000"
    with pytest.raises(HTTPException) as error:
        await actions.execute_product(database, OWNER, approved["payload"], "publish-drift-test")
    assert error.value.status_code == 409
    assert product["status"] == "draft"
    assert len(writes) == 3


@pytest.mark.asyncio
async def test_publish_rejects_another_users_upload(database, wordpress):
    _, writes = wordpress
    bucket = AsyncIOMotorGridFSBucket(database, bucket_name="agent_uploads")
    photo_id = str(await bucket.upload_from_stream(
        "other.png", PNG, metadata={"owner": str(ObjectId()), "type": "image/png"},
    ))
    with pytest.raises(HTTPException) as error:
        await actions.prepare_product(database, OWNER, {
            "name": "2020 Honda Civic", "photo_ids": [photo_id], "publish": False,
        }, [{"id": photo_id}])
    assert error.value.status_code == 422
    assert not writes


@pytest.mark.asyncio
async def test_reminder_is_durable_and_delivered_once(database, monkeypatch):
    now = datetime(2030, 6, 3, 9, tzinfo=timezone.utc)
    monkeypatch.setattr(actions, "_utcnow", lambda: now)
    proposal = await actions.prepare_reminder(database, OWNER, {
        "lot_url": "https://www.copart.com/lot/12345678/test-vehicle?ref=discard",
        "remind_at": "2030-06-03T06:00:00-04:00",
        "label": "test vehicle",
    })
    first = await actions.execute_reminder(database, OWNER, proposal["payload"], "reminder-one")
    second = await actions.execute_reminder(database, OWNER, proposal["payload"], "reminder-two")
    assert first == second
    assert first["remind_at"] == "2030-06-03T10:00:00+00:00"
    assert await actions.deliver_due_reminders(database) == 0
    now += timedelta(hours=1)
    await asyncio.gather(actions.deliver_due_reminders(database), actions.deliver_due_reminders(database))
    assert await actions.deliver_due_reminders(database) == 0
    notifications = await database.notifications.find({}).to_list(length=5)
    assert len(notifications) == 1
    assert notifications[0]["user_id"] == str(OWNER["_id"])
    assert notifications[0]["link"] == "https://www.copart.com/lot/12345678"


@pytest.mark.asyncio
@pytest.mark.parametrize("url,stamp", [
    ("https://www.copart.com.attacker.example/lot/12345678", "2030-06-03T10:00:00+00:00"),
    ("https://www.copart.com/lot/12345678", "2030-06-03T10:00:00"),
])
async def test_reminders_require_copart_lot_and_timezone(url, stamp):
    with pytest.raises(HTTPException) as error:
        await actions.prepare_reminder(None, OWNER, {"lot_url": url, "remind_at": stamp})
    assert error.value.status_code == 422
