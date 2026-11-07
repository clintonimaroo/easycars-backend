import re
import secrets
from datetime import datetime, timezone
from uuid import uuid4

from pymongo import DESCENDING

THREADS = "agent_threads"
THREAD_ID = re.compile(r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}~[0-9a-f]{32}$")


def now():
    return datetime.now(timezone.utc)


def new_thread_id():
    return f"{uuid4()}~{secrets.token_hex(16)}"


def valid_id(value):
    return isinstance(value, str) and bool(THREAD_ID.match(value))


def title_for(text):
    words = " ".join((text or "").split())
    if not words:
        return "New chat"
    return words[:64].rstrip(" ,.;:") + ("…" if len(words) > 64 else "")


def view(record):
    return {
        "id": record["_id"], "title": record.get("title") or "New chat",
        "created_at": record["created_at"], "updated_at": record.get("updated_at") or record["created_at"],
        "preview": record.get("preview", ""),
    }


async def ensure_indexes(db):
    await db[THREADS].create_index([("owner", 1), ("updated_at", DESCENDING)])
    await db.agent_turns.create_index([("owner", 1), ("thread_id", 1), ("created_at", DESCENDING)])


async def create(db, owner, title=None):
    moment = now()
    record = {"_id": new_thread_id(), "owner": owner, "title": title or "New chat", "preview": "", "created_at": moment, "updated_at": moment}
    await db[THREADS].insert_one(record)
    return record


async def get(db, owner, thread_id):
    if not valid_id(thread_id):
        return None
    return await db[THREADS].find_one({"_id": thread_id, "owner": owner})


async def ensure_default(db, owner):
    orphan = await db.agent_turns.find_one({"owner": owner, "thread_id": {"$exists": False}}, {"text": 1, "created_at": 1}, sort=[("created_at", 1)])
    if not orphan:
        return None
    record = await create(db, owner, title_for(orphan.get("text")))
    await db.agent_turns.update_many({"owner": owner, "thread_id": {"$exists": False}}, {"$set": {"thread_id": record["_id"]}})
    latest = await db.agent_turns.find_one({"owner": owner, "thread_id": record["_id"]}, {"text": 1, "created_at": 1}, sort=[("created_at", -1)])
    await db[THREADS].update_one({"_id": record["_id"]}, {"$set": {"updated_at": latest["created_at"], "preview": " ".join((latest.get("text") or "").split())[:140]}})
    return record


async def list_threads(db, owner, limit=60):
    await ensure_default(db, owner)
    cursor = db[THREADS].find({"owner": owner}).sort("updated_at", DESCENDING).limit(limit)
    return [view(item) async for item in cursor]


async def latest(db, owner):
    await ensure_default(db, owner)
    record = await db[THREADS].find_one({"owner": owner}, sort=[("updated_at", DESCENDING)])
    return record or await create(db, owner)


async def touch(db, owner, thread_id, text):
    record = await db[THREADS].find_one({"_id": thread_id, "owner": owner}, {"title": 1})
    changes = {"updated_at": now(), "preview": " ".join((text or "").split())[:140]}
    if record and record.get("title") in (None, "", "New chat") and text:
        changes["title"] = title_for(text)
    await db[THREADS].update_one({"_id": thread_id, "owner": owner}, {"$set": changes})


async def rename(db, owner, thread_id, title):
    result = await db[THREADS].update_one({"_id": thread_id, "owner": owner}, {"$set": {"title": title_for(title)}})
    return result.matched_count == 1


async def remove(db, owner, thread_id):
    result = await db[THREADS].delete_one({"_id": thread_id, "owner": owner})
    if result.deleted_count:
        await db.agent_turns.delete_many({"owner": owner, "thread_id": thread_id})
        await db.agent_thread_summaries.delete_one({"_id": f"{owner}:{thread_id}"})
    return result.deleted_count == 1
