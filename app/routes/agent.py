import hashlib
import re
from datetime import timedelta
from pathlib import PurePath
from typing import Literal
from uuid import UUID
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import jwt
from bson import ObjectId
from fastapi import APIRouter, Depends, File, Header, HTTPException, Query, Request, UploadFile
from gridfs.errors import NoFile
from pydantic import BaseModel, ConfigDict, Field, StrictBool
from pymongo import ReturnDocument
from starlette.responses import StreamingResponse
from urllib.parse import quote

from app.config.env import settings
from app.models.user import UserModel
from app.routes.auth import get_current_user
from app.routes.products import has_private_signing_key
from app.services import agent_provider, agent_runtime
from app.utils.mongodb import get_async_database

router = APIRouter()
UPLOAD_AUDIENCE = "easycars-agent-upload"
MAX_FILE = 18 * 1024 * 1024
MAX_BATCH = 150 * 1024 * 1024
MAX_AUDIO = 4 * 1024 * 1024
PRIVATE = {"Cache-Control": "private, no-store", "X-Content-Type-Options": "nosniff"}
MIMES = {
    ".pdf": "application/pdf",
    ".jpg": "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
    ".txt": "text/plain",
    ".csv": "text/csv",
    ".docx": agent_provider.DOCX,
}
AUDIO = {
    "audio/webm": "dictation.webm",
    "audio/mp4": "dictation.mp4",
    "audio/m4a": "dictation.m4a",
    "audio/ogg": "dictation.ogg",
    "audio/wav": "dictation.wav",
    "audio/mpeg": "dictation.mp3",
}


class AgentInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class TurnInput(AgentInput):
    id: UUID
    text: str = Field(max_length=8000)
    attachment_ids: list[str] = Field(max_length=50)
    auto_approve: bool
    timezone: str = Field(max_length=64)


class Decision(AgentInput):
    decision: Literal["approve", "reject"]


class MemoryInput(AgentInput):
    preferences: str = Field(max_length=2000)


class AgentSettingsInput(AgentInput):
    auto_approve: StrictBool


class ReactionInput(AgentInput):
    reaction: Literal["👍", "🎉", "👀", "✅", "🚀", "🔥"] | None


def _ready():
    if not agent_runtime.configured():
        raise HTTPException(503, "Slizzy's Agent is waiting for its server connection.")


def _permitted(user):
    if settings.ENVIRONMENT == "production" and not has_private_signing_key():
        raise HTTPException(503, "Configure a private EasyCars signing key to enable agent access.")
    if not agent_runtime.authorized(user):
        raise HTTPException(403, "Your account does not have access to Slizzy's Agent.")
    return user


async def _agent_user(user=Depends(get_current_user)):
    return _permitted(user)


async def _write_header(x_easycars_agent: str = Header(default="")):
    if x_easycars_agent != "1":
        raise HTTPException(403, "Invalid agent request.")


async def _upload_user(request: Request):
    _ready()
    authorization = request.headers.get("Authorization", "")
    scheme, _, token = authorization.partition(" ")
    if scheme.lower() != "bearer" or not token:
        raise HTTPException(401, "Request a new attachment upload token.")
    try:
        claims = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM], audience=UPLOAD_AUDIENCE,
                            options={"require": ["exp", "sub", "aud"]})
        owner = claims["sub"]
        if not ObjectId.is_valid(owner):
            raise ValueError
    except (jwt.InvalidTokenError, ValueError):
        raise HTTPException(401, "The attachment upload expired. Try sending again.") from None
    user = await UserModel.find_by_id(owner)
    if not user:
        raise HTTPException(401, "Sign in to upload an attachment.")
    return _permitted(user)


def _key(user, turn_id):
    return f"{user['_id']}:{turn_id}"


def _present(turn, previous=None):
    fields = (
        "id", "text", "auto_approve", "status", "stage", "attachments", "reply", "sources", "error",
        "created_at", "work_started_at", "work_finished_at", "search_query", "reaction", "suggested_reply",
    )
    result = {field: turn.get(field) for field in fields}
    result["attachments"] = agent_runtime.submitted_attachments(turn, previous)
    result["reply"] = agent_runtime.clean_reply(result["reply"])
    result["reply_messages"] = turn.get("reply_messages", [result["reply"]] if result["reply"] else [])
    result["progress_messages"] = turn.get("progress_messages", [])
    for field in ("created_at", "work_started_at", "work_finished_at"):
        value = result[field]
        if value is not None and value.tzinfo is None:
            result[field] = value.isoformat() + "Z"
    result["actions"] = [
        {field: action.get(field) for field in ("id", "title", "summary", "details", "status", "result", "error", "started")}
        for action in turn.get("actions", [])
    ]
    return result


async def _present_after_write(db, turn):
    previous = None
    if "context_attachments" not in turn and turn.get("attachments") and turn.get("intent"):
        previous = await db.agent_turns.find_one(
            {"owner": turn["owner"], "created_at": {"$lt": turn["created_at"]}},
            {"attachments": 1, "context_attachments": 1, "intent": 1, "status": 1},
            sort=[("created_at", -1), ("_id", -1)],
        )
    return _present(turn, previous)


@router.get("")
async def read_agent(before: UUID | None = Query(None), user=Depends(_agent_user), db=Depends(get_async_database)):
    owner = str(user["_id"])
    query = {"owner": owner}
    if before is not None:
        cursor = await db.agent_turns.find_one({"_id": _key(user, before)}, {"created_at": 1})
        if not cursor:
            raise HTTPException(404, "That part of the conversation could not be found.")
        query["$or"] = [
            {"created_at": {"$lt": cursor["created_at"]}},
            {"created_at": cursor["created_at"], "_id": {"$lt": cursor["_id"]}},
        ]
    turns = await db.agent_turns.find(query).sort([("created_at", -1), ("_id", -1)]).to_list(26)
    memory = await db.agent_memory.find_one({"_id": owner}) or {}
    return {
        "turns": list(reversed([
            _present(turn, turns[index + 1] if index + 1 < len(turns) else None)
            for index, turn in enumerate(turns[:25])
        ])),
        "has_more": len(turns) > 25,
        "configured": agent_runtime.configured(),
        "memory": {"preferences": memory.get("preferences", "")},
        "settings": {"auto_approve": memory.get("auto_approve") is True},
    }


@router.get("/turns/{turn_id}")
async def read_turn_status(turn_id: UUID, user=Depends(_agent_user), db=Depends(get_async_database)):
    turn = await db.agent_turns.find_one(
        {"_id": _key(user, turn_id), "owner": str(user["_id"])},
        {"_id": 0, "id": 1, "status": 1, "actions.id": 1, "actions.status": 1,
         "actions.result.vehicle_id": 1, "actions.result.customer_id": 1},
    )
    if turn is None:
        raise HTTPException(404, "This task could not be found.")
    return {"turn": turn}


@router.patch("/memory", dependencies=[Depends(_write_header)])
async def update_memory(body: MemoryInput, user=Depends(_agent_user), db=Depends(get_async_database)):
    preferences = body.preferences.strip()
    await db.agent_memory.update_one(
        {"_id": str(user["_id"])}, {"$set": {"preferences": preferences}}, upsert=True,
    )
    return {"preferences": preferences}


@router.patch("/settings", dependencies=[Depends(_write_header)])
async def update_agent_settings(body: AgentSettingsInput, user=Depends(_agent_user),
                                db=Depends(get_async_database)):
    preference = body.model_dump()
    await db.agent_memory.update_one(
        {"_id": str(user["_id"])}, {"$set": preference}, upsert=True,
    )
    return preference


@router.post("/upload-token", dependencies=[Depends(_write_header)])
async def upload_token(user=Depends(_agent_user)):
    _ready()
    token = jwt.encode(
        {"aud": UPLOAD_AUDIENCE, "sub": str(user["_id"]), "exp": agent_runtime.now() + timedelta(minutes=10)},
        settings.SECRET_KEY, algorithm=settings.ALGORITHM,
    )
    return {"token": token, "url": "/agent/attachments"}


def _identify(name, data):
    mime = MIMES.get(PurePath(name).suffix.lower())
    valid = {
        "application/pdf": data.startswith(b"%PDF-"),
        "image/jpeg": data.startswith(b"\xff\xd8\xff"),
        "image/png": data.startswith(b"\x89PNG\r\n\x1a\n"),
        "image/webp": data[:4] == b"RIFF" and data[8:12] == b"WEBP",
        agent_provider.DOCX: data.startswith(b"PK\x03\x04"),
    }
    if mime in {"text/plain", "text/csv"}:
        try:
            data.decode("utf-8-sig")
            return mime
        except UnicodeError:
            raise HTTPException(415, "Choose a UTF-8 text document.") from None
    if mime and valid.get(mime):
        return mime
    raise HTTPException(415, "Choose a valid PDF, photo, DOCX, TXT, or CSV file.")


@router.post("/attachments", name="agent_upload_attachment", status_code=201)
async def upload_attachment(file: UploadFile = File(...), user=Depends(_upload_user), db=Depends(get_async_database)):
    data = await file.read(MAX_FILE + 1)
    if not data:
        raise HTTPException(422, "This attachment is empty.")
    if len(data) > MAX_FILE:
        raise HTTPException(413, "Choose a file under 18 MB.")
    name = re.sub(r"[\x00-\x1f\x7f]", "", PurePath((file.filename or "attachment").replace("\\", "/")).name)
    if len(name) > 180:
        suffix = PurePath(name).suffix[:12]
        name = name[:180 - len(suffix)] + suffix
    mime = _identify(name, data)
    owner = str(user["_id"])
    file_id = await agent_runtime.bucket(db).upload_from_stream(name, data, metadata={"owner": owner, "type": mime, "sha256": hashlib.sha256(data).hexdigest()})
    return {"id": str(file_id), "name": name, "type": mime, "size": len(data)}


@router.get("/attachments/{file_id}")
async def read_attachment(file_id: str, user=Depends(_agent_user), db=Depends(get_async_database)):
    try:
        if not ObjectId.is_valid(file_id):
            raise NoFile
        stream = await agent_runtime.bucket(db).open_download_stream(ObjectId(file_id))
        if stream.metadata.get("owner") != str(user["_id"]):
            raise NoFile
    except NoFile:
        raise HTTPException(404, "Attachment not found.") from None

    async def chunks():
        while chunk := await stream.readchunk():
            yield chunk

    return StreamingResponse(chunks(), media_type=stream.metadata["type"], headers={
        **PRIVATE, "Content-Length": str(stream.length),
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(stream.filename, safe='')}",
    })


@router.post("/turns", dependencies=[Depends(_write_header)], status_code=202)
async def create_turn(body: TurnInput, user=Depends(_agent_user), db=Depends(get_async_database)):
    _ready()
    text = body.text.strip()
    if not text and not body.attachment_ids:
        raise HTTPException(422, "Write a message or add an attachment.")
    try:
        ZoneInfo(body.timezone)
    except (ValueError, ZoneInfoNotFoundError):
        raise HTTPException(422, "Choose a valid time zone.") from None
    if len(set(body.attachment_ids)) != len(body.attachment_ids) or not all(
        ObjectId.is_valid(item) for item in body.attachment_ids
    ):
        raise HTTPException(422, "One of the selected attachments is invalid.")
    owner = str(user["_id"])
    records = await db["agent_uploads.files"].find({
        "_id": {"$in": [ObjectId(item) for item in body.attachment_ids]}, "metadata.owner": owner,
    }).to_list(len(body.attachment_ids))
    by_id = {str(record["_id"]): agent_runtime.attachment_view(record) for record in records}
    if len(by_id) != len(body.attachment_ids):
        raise HTTPException(404, "One of the selected attachments could not be found.")
    attachments = [by_id[item] for item in body.attachment_ids]
    if sum(item["size"] for item in attachments) > MAX_BATCH:
        raise HTTPException(413, "Attachments must total less than 150 MB per message.")
    key = _key(user, body.id)
    created_at = agent_runtime.now()
    turn = await db.agent_turns.find_one_and_update(
        {"_id": key},
        {"$setOnInsert": {
            "owner": owner, "id": str(body.id), "text": text, "timezone": body.timezone,
            "attachments": attachments, "context_attachments": [], "auto_approve": body.auto_approve, "created_at": created_at,
            "work_started_at": created_at, "work_finished_at": None,
            "status": "queued", "stage": "Queued", "attempts": 0, "actions": [], "reply": "", "sources": [],
        }},
        upsert=True, return_document=ReturnDocument.AFTER,
    )
    return {"turn": await _present_after_write(db, turn)}


@router.patch("/turns/{turn_id}/reaction", dependencies=[Depends(_write_header)])
async def update_reaction(turn_id: UUID, body: ReactionInput, user=Depends(_agent_user),
                          db=Depends(get_async_database)):
    result = await db.agent_turns.update_one(
        {"_id": _key(user, turn_id), "owner": str(user["_id"]),
         "status": {"$in": ["completed", "needs_input", "awaiting_approval", "failed"]},
         "reply": {"$regex": r"\S"}},
        {"$set": body.model_dump()},
    )
    if not result.matched_count:
        raise HTTPException(404, "This agent reply could not be found.")
    return body.model_dump()


@router.post("/turns/{turn_id}/actions/{action_id}", dependencies=[Depends(_write_header)])
async def decide_action(turn_id: UUID, action_id: UUID, body: Decision, user=Depends(_agent_user),
                        db=Depends(get_async_database)):
    _ready()
    key = _key(user, turn_id)
    query = {"_id": key, "status": {"$in": ["queued", "awaiting_approval", "failed"]},
             "actions": {"$elemMatch": {"id": str(action_id), "status": "pending"}}}
    approve = body.decision == "approve"
    decided_at = agent_runtime.now()
    turn = await db.agent_turns.find_one_and_update(
        query,
        {"$set": {
            "actions.$.status": "queued" if approve else "rejected",
            "actions.$.approved_by": str(user["_id"]) if approve else None,
            "actions.$.approval": "manual" if approve else None,
            "actions.$.approved_at": decided_at if approve else None,
            "status": "queued", "stage": "Queued", "error": None,
            "work_started_at": decided_at, "work_finished_at": None,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if not turn:
        raise HTTPException(409, "This action has changed. Refresh the conversation and try again.")
    return {"turn": await _present_after_write(db, turn)}


@router.post("/turns/{turn_id}/retry", dependencies=[Depends(_write_header)])
async def retry_turn(turn_id: UUID, user=Depends(_agent_user), db=Depends(get_async_database)):
    _ready()
    turn = await db.agent_turns.find_one_and_update(
        {"_id": _key(user, turn_id), "status": "failed"},
        {"$set": {"status": "queued", "stage": "Queued", "error": None, "attempts": 0, "search_query": None,
                  "progress_messages": [],
                  "work_started_at": agent_runtime.now(), "work_finished_at": None,
                  "actions.$[failed].status": "queued", "actions.$[failed].error": None,
                  "actions.$[failed].started": True}},
        array_filters=[{"failed.status": "failed"}],
        return_document=ReturnDocument.AFTER,
    )
    if not turn:
        raise HTTPException(409, "This task has changed. Refresh the conversation and try again.")
    return {"turn": await _present_after_write(db, turn)}


@router.post("/turns/{turn_id}/stop", dependencies=[Depends(_write_header)])
async def stop_turn(turn_id: UUID, user=Depends(_agent_user), db=Depends(get_async_database)):
    key = {"_id": _key(user, turn_id), "owner": str(user["_id"])}
    attempted = {"$or": [
        {"started": True},
        {"status": {"$in": ["running", "completed", "failed"]}},
    ]}
    turn = await db.agent_turns.find_one_and_update(
        {**key, "status": {"$in": ["queued", "running"]},
         "actions": {"$not": {"$elemMatch": attempted}}},
        {"$set": {
            "status": "stopped", "stage": "Stopped", "lease_id": None, "lease_until": None,
            "work_finished_at": agent_runtime.now(), "error": None,
            "reply": "", "reply_messages": [], "suggested_reply": None, "actions": [], "sources": [], "search_query": None,
        }},
        return_document=ReturnDocument.AFTER,
    )
    if turn is None:
        turn = await db.agent_turns.find_one(key)
        if turn is None:
            raise HTTPException(404, "This task could not be found.")
        if turn["status"] != "stopped":
            if any(action.get("started") or action["status"] in {"running", "completed", "failed"}
                   for action in turn.get("actions", [])):
                raise HTTPException(409, "An action has already started. Its result will stay in the conversation.")
            raise HTTPException(409, "This task is no longer running.")
    return {"turn": await _present_after_write(db, turn)}


@router.post("/transcribe", dependencies=[Depends(_write_header)])
async def transcribe(file: UploadFile = File(...), user=Depends(_agent_user)):
    _ready()
    mime = (file.content_type or "").split(";", 1)[0]
    if mime not in AUDIO:
        raise HTTPException(415, "This recording format isn't supported.")
    data = await file.read(MAX_AUDIO + 1)
    if not data or len(data) > MAX_AUDIO:
        raise HTTPException(413, "Record a shorter voice note.")
    try:
        return {"text": await agent_provider.transcribe(data, AUDIO[mime], mime)}
    except agent_provider.AgentProviderError as exc:
        raise HTTPException(502, str(exc)) from None
