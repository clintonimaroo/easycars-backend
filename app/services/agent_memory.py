import logging
import re
from datetime import datetime, timezone

from pymongo import DESCENDING

from app.config.env import settings
from app.services import agent_provider

logger = logging.getLogger(__name__)
FACTS = "agent_facts"
SUMMARIES = "agent_thread_summaries"
AUDIT = "agent_audit"
SUBJECT_TYPES = {"vehicle", "customer", "deal", "dealership", "staff", "preference", "other"}
MAX_FACTS = 400


def now():
    return datetime.now(timezone.utc)


def _key(value):
    return " ".join(str(value or "").split()).strip().lower()[:120]


async def ensure_memory_indexes(db):
    await db[FACTS].create_index([("owner", 1), ("subject_type", 1), ("subject_key", 1)])
    await db[FACTS].create_index([("owner", 1), ("updated_at", DESCENDING)])
    await db[AUDIT].create_index([("owner", 1), ("created_at", DESCENDING)])
    await db[AUDIT].create_index([("turn_id", 1)])
    try:
        await db[FACTS].create_index([("fact", "text"), ("subject", "text")], name="agent_facts_text")
    except Exception as exc:
        logger.info("Agent facts text index unavailable (%s)", type(exc).__name__)


async def remember(db, owner, subject_type, subject, fact, source_turn=None):
    subject_type = subject_type if subject_type in SUBJECT_TYPES else "other"
    fact = " ".join(str(fact or "").split())[:600]
    subject = " ".join(str(subject or "").split())[:160]
    if not fact:
        raise ValueError("A fact is required.")
    record = {
        "owner": owner, "subject_type": subject_type, "subject": subject, "subject_key": _key(subject),
        "fact": fact, "updated_at": now(), "source_turn": source_turn,
    }
    await db[FACTS].update_one(
        {"owner": owner, "subject_type": subject_type, "subject_key": _key(subject), "fact": fact},
        {"$set": record, "$setOnInsert": {"created_at": now()}}, upsert=True,
    )
    total = await db[FACTS].count_documents({"owner": owner})
    if total > MAX_FACTS:
        stale = db[FACTS].find({"owner": owner}, {"_id": 1}).sort("updated_at", 1).limit(total - MAX_FACTS)
        ids = [item["_id"] async for item in stale]
        if ids:
            await db[FACTS].delete_many({"_id": {"$in": ids}})
    return record


async def forget(db, owner, subject=None, fact_id=None):
    query = {"owner": owner}
    if fact_id is not None:
        query["_id"] = fact_id
    elif subject:
        query["subject_key"] = _key(subject)
    else:
        return 0
    result = await db[FACTS].delete_many(query)
    return result.deleted_count


def _view(record):
    return {
        "id": str(record["_id"]), "subject_type": record.get("subject_type"), "subject": record.get("subject"),
        "fact": record.get("fact"), "updated_at": record["updated_at"].isoformat() if record.get("updated_at") else None,
    }


async def recall(db, owner, query="", subject_type=None, limit=12):
    limit = max(1, min(int(limit or 12), 40))
    base = {"owner": owner}
    if subject_type in SUBJECT_TYPES:
        base["subject_type"] = subject_type
    terms = [term for term in re.findall(r"[\w'-]{2,}", str(query or "")) if len(term) > 1][:8]
    records = []
    if terms:
        pattern = "|".join(re.escape(term) for term in terms)
        regex = {"$regex": pattern, "$options": "i"}
        cursor = db[FACTS].find({**base, "$or": [{"fact": regex}, {"subject": regex}]}).sort("updated_at", DESCENDING).limit(limit)
        records = [item async for item in cursor]
    if not records:
        cursor = db[FACTS].find(base).sort("updated_at", DESCENDING).limit(limit)
        records = [item async for item in cursor]
    return [_view(item) for item in records]


async def recent_facts(db, owner, limit=24):
    cursor = db[FACTS].find({"owner": owner}).sort("updated_at", DESCENDING).limit(limit)
    return [_view(item) async for item in cursor]


async def thread_summary(db, owner):
    record = await db[SUMMARIES].find_one({"_id": owner})
    return (record or {}).get("summary", ""), (record or {}).get("through")


async def update_thread_summary(db, owner, turn, history_tail):
    previous, _ = await thread_summary(db, owner)
    exchange = {
        "previous_summary": previous,
        "latest_request": turn.get("text", ""),
        "latest_reply": turn.get("reply", ""),
        "latest_actions": [
            {"title": action.get("title"), "status": action.get("status"), "result": action.get("result")}
            for action in turn.get("actions", [])
        ],
        "earlier_turns": history_tail,
    }
    summary, _ = await agent_provider.responses(
        "Maintain a running summary of a dealership assistant conversation for the assistant's own memory. "
        "Write at most 180 words of plain prose. Keep vehicles with their year, make, model, price, mileage and "
        "listing status, open tasks, decisions, pending questions, and dealer preferences. Drop chit-chat. "
        "Carry forward anything from the previous summary that is still relevant. No markdown, no em dashes.",
        agent_provider.text_content(exchange), model=settings.AGENT_MODEL,
    )
    await db[SUMMARIES].update_one(
        {"_id": owner}, {"$set": {"summary": summary.strip()[:2000], "through": turn.get("id"), "updated_at": now()}}, upsert=True,
    )
    return summary


async def audit(db, owner, turn_id, kind, name, detail=None, status="ok", duration_ms=None):
    try:
        await db[AUDIT].insert_one({
            "owner": owner, "turn_id": turn_id, "kind": kind, "name": name, "status": status,
            "detail": detail, "duration_ms": duration_ms, "created_at": now(),
        })
    except Exception as exc:
        logger.warning("Agent audit write failed (%s)", type(exc).__name__)
