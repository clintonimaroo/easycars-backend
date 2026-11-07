import logging
import re
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException
from pymongo import ReturnDocument

from app.services import agent_threads

logger = logging.getLogger(__name__)
JOBS = "agent_jobs"
DAYS = ["monday", "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday"]
DAY_ALIASES = {name[:3]: index for index, name in enumerate(DAYS)}
DAY_ALIASES.update({name: index for index, name in enumerate(DAYS)})
MAX_JOBS = 40


def now():
    return datetime.now(timezone.utc)


def _zone(name):
    try:
        return ZoneInfo(name or "UTC")
    except (ValueError, ZoneInfoNotFoundError):
        raise HTTPException(422, "Choose a valid time zone.") from None


def _time_of(match):
    hour = int(match.group("hour"))
    minute = int(match.group("minute") or 0)
    meridiem = (match.group("meridiem") or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    if meridiem == "am" and hour == 12:
        hour = 0
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise HTTPException(422, "Use a time between 00:00 and 23:59.")
    return hour, minute


TIME = r"(?P<hour>\d{1,2})(?::(?P<minute>\d{2}))?\s*(?P<meridiem>am|pm)?"


def parse_schedule(text):
    value = " ".join(str(text or "").lower().replace(".", "").split())
    if not value:
        raise HTTPException(422, "Describe when the task should run.")
    iso = re.fullmatch(r"\d{4}-\d{2}-\d{2}t\d{2}:\d{2}(?::\d{2})?(?:z|[+-]\d{2}:\d{2})?", value)
    if iso:
        try:
            at = datetime.fromisoformat(value.upper().replace("Z", "+00:00"))
        except ValueError:
            raise HTTPException(422, "Use an ISO 8601 date and time.") from None
        return {"kind": "once", "at": at.isoformat()}
    match = re.fullmatch(r"every (?P<n>\d+) hours?", value)
    if match:
        hours = int(match.group("n"))
        if not 1 <= hours <= 168:
            raise HTTPException(422, "Repeat between every 1 and every 168 hours.")
        return {"kind": "hourly", "interval_hours": hours}
    if value in {"every hour", "hourly"}:
        return {"kind": "hourly", "interval_hours": 1}
    match = re.fullmatch(rf"(?:every ?day|daily|each day)(?: at)? {TIME}", value)
    if match:
        hour, minute = _time_of(match)
        return {"kind": "daily", "hour": hour, "minute": minute}
    match = re.fullmatch(rf"(?:every )?weekdays?(?: at)? {TIME}", value)
    if match:
        hour, minute = _time_of(match)
        return {"kind": "weekly", "days": [0, 1, 2, 3, 4], "hour": hour, "minute": minute}
    match = re.fullmatch(rf"(?:every |weekly on |on )?(?P<days>(?:(?:mon|tue|wed|thu|fri|sat|sun)[a-z]*s?(?:,| and |, and | )?)+)(?: at)? {TIME}", value)
    if match:
        hour, minute = _time_of(match)
        days = sorted({DAY_ALIASES[token[:3]] for token in re.findall(r"mon|tue|wed|thu|fri|sat|sun", match.group("days"))})
        if days:
            return {"kind": "weekly", "days": days, "hour": hour, "minute": minute}
    raise HTTPException(422, "Use a schedule like 'every weekday at 8:00', 'daily at 18:00', 'every Monday at 9:30', 'every 4 hours', or an ISO date and time.")


def describe_schedule(schedule):
    kind = schedule.get("kind")
    if kind == "once":
        return f"Once at {schedule['at']}"
    if kind == "hourly":
        hours = schedule.get("interval_hours", 1)
        return "Every hour" if hours == 1 else f"Every {hours} hours"
    clock = f"{schedule['hour']:02d}:{schedule['minute']:02d}"
    if kind == "daily":
        return f"Every day at {clock}"
    days = schedule.get("days", [])
    if days == [0, 1, 2, 3, 4]:
        return f"Weekdays at {clock}"
    return "Every " + ", ".join(DAYS[day].capitalize() for day in days) + f" at {clock}"


def next_run(schedule, zone_name, after=None):
    after = after or now()
    zone = _zone(zone_name)
    kind = schedule.get("kind")
    if kind == "once":
        at = datetime.fromisoformat(schedule["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=zone)
        return at.astimezone(timezone.utc) if at > after else None
    if kind == "hourly":
        return after + timedelta(hours=schedule.get("interval_hours", 1))
    local = after.astimezone(zone)
    candidate = local.replace(hour=schedule["hour"], minute=schedule["minute"], second=0, microsecond=0)
    days = schedule.get("days") if kind == "weekly" else list(range(7))
    for offset in range(0, 8):
        attempt = candidate + timedelta(days=offset)
        if attempt <= local or attempt.weekday() not in days:
            continue
        return attempt.astimezone(timezone.utc)
    return None


def _view(job):
    return {
        "id": job["id"], "name": job["name"], "prompt": job["prompt"], "schedule": job["schedule"],
        "schedule_text": describe_schedule(job["schedule"]), "timezone": job["timezone"], "enabled": job.get("enabled", True),
        "auto_approve": job.get("auto_approve", False),
        "next_run_at": job["next_run_at"].isoformat() if job.get("next_run_at") else None,
        "last_run_at": job["last_run_at"].isoformat() if job.get("last_run_at") else None,
        "last_turn_id": job.get("last_turn_id"), "runs": job.get("runs", 0),
    }


async def ensure_job_indexes(db):
    await db[JOBS].create_index([("owner", 1), ("created_at", -1)])
    await db[JOBS].create_index([("enabled", 1), ("next_run_at", 1)])


async def list_jobs(db, owner):
    cursor = db[JOBS].find({"owner": owner}).sort("created_at", -1).limit(MAX_JOBS)
    return [_view(job) async for job in cursor]


async def create_job(db, owner, body):
    name = " ".join(str(body.get("name") or "").split())[:80]
    prompt = str(body.get("prompt") or "").strip()[:4000]
    if not name or not prompt:
        raise HTTPException(422, "Give the task a name and an instruction.")
    schedule = body.get("schedule")
    if isinstance(schedule, str):
        schedule = parse_schedule(schedule)
    elif not isinstance(schedule, dict) or "kind" not in schedule:
        raise HTTPException(422, "Describe when the task should run.")
    zone_name = body.get("timezone") or "UTC"
    _zone(zone_name)
    if await db[JOBS].count_documents({"owner": owner}) >= MAX_JOBS:
        raise HTTPException(409, f"You can keep up to {MAX_JOBS} scheduled tasks. Remove one first.")
    upcoming = next_run(schedule, zone_name)
    if upcoming is None:
        raise HTTPException(422, "That schedule has no future run.")
    job = {
        "_id": f"{owner}:{uuid4()}", "id": str(uuid4()), "owner": owner, "name": name, "prompt": prompt,
        "schedule": schedule, "timezone": zone_name, "enabled": True, "auto_approve": bool(body.get("auto_approve")),
        "created_at": now(), "next_run_at": upcoming, "last_run_at": None, "runs": 0,
    }
    await db[JOBS].insert_one(job)
    return _view(job)


async def update_job(db, owner, job_id, body):
    job = await db[JOBS].find_one({"owner": owner, "id": job_id})
    if not job:
        raise HTTPException(404, "That scheduled task was not found.")
    changes = {}
    if "name" in body:
        changes["name"] = " ".join(str(body["name"] or "").split())[:80] or job["name"]
    if "prompt" in body:
        changes["prompt"] = str(body["prompt"] or "").strip()[:4000] or job["prompt"]
    if "enabled" in body:
        changes["enabled"] = bool(body["enabled"])
    if "auto_approve" in body:
        changes["auto_approve"] = bool(body["auto_approve"])
    if "timezone" in body:
        _zone(body["timezone"])
        changes["timezone"] = body["timezone"]
    if "schedule" in body:
        schedule = body["schedule"]
        changes["schedule"] = parse_schedule(schedule) if isinstance(schedule, str) else schedule
    if "schedule" in changes or "timezone" in changes or changes.get("enabled"):
        changes["next_run_at"] = next_run(changes.get("schedule", job["schedule"]), changes.get("timezone", job["timezone"]))
    updated = await db[JOBS].find_one_and_update(
        {"_id": job["_id"]}, {"$set": changes}, return_document=ReturnDocument.AFTER,
    )
    return _view(updated)


async def delete_job(db, owner, job_id):
    result = await db[JOBS].delete_one({"owner": owner, "id": job_id})
    if not result.deleted_count:
        raise HTTPException(404, "That scheduled task was not found.")


async def run_due_jobs(db):
    current = now()
    while True:
        job = await db[JOBS].find_one_and_update(
            {"enabled": True, "next_run_at": {"$lte": current}},
            {"$set": {"claimed_at": current}, "$inc": {"runs": 1}},
            sort=[("next_run_at", 1)], return_document=ReturnDocument.AFTER,
        )
        if job is None:
            return
        upcoming = next_run(job["schedule"], job["timezone"], after=current)
        turn_id = str(uuid4())
        thread = await agent_threads.latest(db, job["owner"])
        turn = {
            "_id": f"{job['owner']}:{turn_id}", "owner": job["owner"], "id": turn_id, "thread_id": thread["_id"],
            "text": job["prompt"], "timezone": job["timezone"], "reply_to": None, "job_id": job["id"], "job_name": job["name"],
            "attachments": [], "context_attachments": [], "auto_approve": job.get("auto_approve", False),
            "created_at": current, "work_started_at": current, "work_finished_at": None,
            "status": "queued", "stage": "Queued", "attempts": 0, "actions": [], "reply": "", "sources": [],
        }
        try:
            await db.agent_turns.insert_one(turn)
            await agent_threads.touch(db, job["owner"], thread["_id"], job["prompt"])
        except Exception as exc:
            logger.warning("Scheduled task turn could not be queued (%s)", type(exc).__name__)
        await db[JOBS].update_one(
            {"_id": job["_id"]},
            {"$set": {"last_run_at": current, "last_turn_id": turn_id, "next_run_at": upcoming, "enabled": upcoming is not None}},
        )
