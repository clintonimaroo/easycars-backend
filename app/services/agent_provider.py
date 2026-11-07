import asyncio
import base64
import io
import json
import logging
import re
from urllib.parse import urlsplit
from xml.etree import ElementTree
from zipfile import BadZipFile, ZipFile

import httpx
from fastapi import HTTPException
from pydantic import ValidationError

from app.config.env import settings

API_ROOT = "https://api.openai.com/v1"
DOCX = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
SEARCH_OUTPUT_TOKENS = 6_500
SEARCH_RETRY_OUTPUT_TOKENS = 12_000
REPLY_MESSAGE_BREAK = "[[EASYCARS_MESSAGE_BREAK]]"
SUGGESTED_REPLY_MARKER = "[[EASYCARS_SUGGESTED_REPLY]]"
ANSWER_STYLE = (
    " Write like a capable colleague talking to the dealer. Lead with the answer, the result, or the one thing "
    "you need from them. Use the details they already provided and skip generic introductions and repeated recaps. "
    "Make reasonable, reversible assumptions when they let you help immediately. State assumptions that affect the "
    "result. Ask a focused follow-up only when the missing detail materially changes what you can do. Only offer "
    "actions the app has tools for in this request. Web search alone cannot call or email anyone. Use one chat "
    "message for a simple reply. For distinct ideas that read better separately, use up to three messages and put "
    f"{REPLY_MESSAGE_BREAK} alone on the line "
    "between them. Keep related findings, lists, and citations together within a message. If there is one useful "
    f"read-only follow-up, place {SUGGESTED_REPLY_MARKER} on its own line after the reply, then write a single "
    "draft message of at most 180 characters in the dealer's voice. Never suggest permission, sending, a purchase, "
    "or any other action approval. Omit it if it would invent details or repeat private names, addresses, or identifiers."
)
SEARCH_ANSWER_STYLE = (
    " Give the best findings first and put citations beside the findings they support. Aim for 300 words or fewer "
    "unless the dealer asks for a detailed report. Skip repeated advice, source recaps, and unsolicited call scripts. "
    "For local parts sourcing, treat a junkyard request as a preference for used OEM parts unless the dealer says "
    "otherwise. Search for "
    "specific parts and matching donor vehicles near the requested area. Start with a short, prioritized list of "
    "possible replacements grounded in the supplied photo findings, if any. Separate visible damage from items "
    "that need an in-person inspection. Then give the strongest actionable leads. For each, name the part or "
    "donor vehicle, seller or yard, location, and listed price if shown. Cite the direct item or donor page when "
    "available. Clearly distinguish confirmed part stock, donor vehicles, and general yard leads. Distinguish "
    "shipping from local pickup. Before giving a listing price, saying a part is in stock, or recommending a trip, "
    "open the exact item page and check price, availability, and the item pickup location. Seller headquarters, "
    "search snippets, product overview pages, and substituted listings do not verify those facts. Label listings "
    "you could not verify as unconfirmed leads. Describe self-service yards as self-service when the yard says so. "
    "If new evidence changes an earlier finding, correct it directly and continue with useful leads. Give a subtotal "
    "or range for priced items when the figures support it, and say which parts or fees are still unpriced. Use prices, "
    "availability, and URLs supported by the sources. If useful leads are available now, give them before asking "
    "for a VIN or other details to verify fit."
)
logger = logging.getLogger(__name__)

_PERSONAL_DATA = re.compile(
    r"\b[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}\b"
    r"|\b\d{3}-\d{2}-\d{4}\b"
    r"|(?<!\d)(?:\+1[-. ]?)?(?:\(\d{3}\)|\d{3})[-. ]?\d{3}[-. ]?\d{4}(?!\d)"
    r"|\b[A-HJ-NPR-Z0-9]{17}\b",
    re.IGNORECASE,
)


class AgentProviderError(Exception):
    def __init__(self, message, too_large=False):
        super().__init__(message)
        self.too_large = too_large

    pass


def parse_reply(message):
    suggestion_separator = rf"(?m)^[ \t]*{re.escape(SUGGESTED_REPLY_MARKER)}[ \t]*$"
    reply, *suggestions = re.split(suggestion_separator, message or "", maxsplit=1)
    suggestion = suggestions[0].strip() if suggestions else ""
    if (
        not 1 <= len(suggestion) <= 180 or len(suggestion.splitlines()) != 1
        or _PERSONAL_DATA.search(suggestion) or re.search(r"(?:https?://|www\.)", suggestion, re.I)
        or re.search(r"\b(?:approv\w*|authoriz\w*|send|submit|publish|buy|purchase|place a bid|delete|go ahead)\b", suggestion, re.I)
    ):
        suggestion = None
    separator = rf"(?m)^[ \t]*{re.escape(REPLY_MESSAGE_BREAK)}[ \t]*$"
    messages = [part.strip() for part in re.split(separator, reply) if part.strip()]
    if len(messages) > 3:
        messages = [*messages[:2], "\n\n".join(messages[2:])]
    return messages, suggestion


def _headers():
    key = settings.OPENAI_API_KEY.get_secret_value().strip()
    if not key:
        raise AgentProviderError("The agent needs an OpenAI connection.")
    return {"Authorization": f"Bearer {key}"}


RETRY_DELAYS = (4, 10, 20)


def _error_detail(response):
    try:
        error = response.json().get("error") or {}
    except ValueError:
        return "", ""
    return str(error.get("code") or error.get("type") or ""), str(error.get("message") or "")[:200]


async def _post(path, **kwargs):
    attempt = 0
    while True:
        try:
            async with httpx.AsyncClient(timeout=httpx.Timeout(180, connect=10)) as client:
                response = await client.post(f"{API_ROOT}/{path}", headers=_headers(), **kwargs)
            if response.status_code == 429 or response.status_code >= 500:
                code, detail = _error_detail(response)
                if response.status_code == 429 and code == "insufficient_quota":
                    raise AgentProviderError("The OpenAI account is out of credit. Add billing to the OpenAI project to continue.")
                if response.status_code == 429 and "too large" in detail.lower():
                    raise AgentProviderError(f"OpenAI is rate limiting the agent right now. {detail}", too_large=True)
                if attempt < len(RETRY_DELAYS):
                    retry_after = response.headers.get("retry-after")
                    delay = RETRY_DELAYS[attempt]
                    if retry_after and retry_after.replace(".", "", 1).isdigit():
                        delay = min(max(float(retry_after), 1), 60)
                    attempt += 1
                    logger.info("OpenAI %s, retrying in %ss (%s)", response.status_code, delay, code or "no code")
                    await asyncio.sleep(delay)
                    continue
                if response.status_code == 429:
                    raise AgentProviderError(f"OpenAI is rate limiting the agent right now. {detail or 'Try again in a minute.'}", too_large="too large" in detail.lower())
                raise AgentProviderError("OpenAI could not complete the request. Try again.")
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            code, detail = _error_detail(exc.response)
            if exc.response.status_code in {401, 403}:
                message = "The OpenAI connection rejected this request. Check the server configuration."
            elif exc.response.status_code == 404 and "model" in detail.lower():
                message = f"OpenAI does not offer the configured model to this account. {detail}"
            elif exc.response.status_code == 400:
                message = f"OpenAI rejected the request. {detail or 'Try again.'}"
            else:
                message = "OpenAI could not complete the request. Try again."
            raise AgentProviderError(message) from None
        except (httpx.RequestError, ValueError):
            raise AgentProviderError("The OpenAI connection did not respond. Try again.") from None


async def responses(instructions, content, schema=None, search=False, model=None):
    payload = {
        "model": model or settings.AGENT_MODEL,
        "instructions": instructions + " Use natural punctuation. Avoid em dashes in all user-facing text.",
        "input": [{"role": "user", "content": content}],
        "store": False,
        "max_output_tokens": 6500 if schema else 2400,
    }
    if schema is not None:
        payload["text"] = {"format": {
            "name": schema.__name__,
            "type": "json_schema",
            "strict": True,
            "schema": schema.model_json_schema(),
        }}
    else:
        payload["instructions"] += ANSWER_STYLE
    if search:
        if schema is None:
            payload["instructions"] += SEARCH_ANSWER_STYLE
        payload["tools"] = [{"type": "web_search"}]
        payload["tool_choice"] = "required"
        payload["max_tool_calls"] = 3
        payload["max_output_tokens"] = SEARCH_OUTPUT_TOKENS
    result = await _post("responses", json=payload)
    reason = (result.get("incomplete_details") or {}).get("reason")
    if search and schema is None and result.get("status") == "incomplete" and reason == "max_output_tokens":
        logger.info("Web search reached its output token limit. Retrying with a larger budget.")
        result = await _post("responses", json={**payload, "max_output_tokens": SEARCH_RETRY_OUTPUT_TOKENS})
        reason = (result.get("incomplete_details") or {}).get("reason")
    if result.get("status") != "completed":
        if result.get("status") == "incomplete" and reason == "max_output_tokens":
            if search:
                raise AgentProviderError("The web search reached its response limit. Try fewer parts or a smaller search area.")
            raise AgentProviderError("The response reached its output limit. Try again with a smaller request.")
        raise AgentProviderError("OpenAI could not complete the response. Try again.")
    fragments, sources = [], {}
    for item in result.get("output", []):
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") != "output_text":
                continue
            fragments.append(part.get("text", ""))
            for annotation in part.get("annotations", []):
                url = annotation.get("url", "")
                if annotation.get("type") == "url_citation" and urlsplit(url).scheme in {"https", "http"}:
                    sources[url] = {"url": url, "title": annotation.get("title") or url}
    message = "\n".join(fragments).strip()
    if not message:
        raise AgentProviderError("The agent couldn't produce a response for this request.")
    if schema is not None:
        try:
            return schema.model_validate_json(message)
        except ValidationError:
            raise AgentProviderError("The response could not be validated. Try again.") from None
    return message, list(sources.values())[:16]


def function_tool(name, description, parameters):
    return {"type": "function", "name": name, "description": description, "parameters": parameters, "strict": False}


async def complete(instructions, input_items, tools=None, model=None, max_output_tokens=4000, search=False):
    payload = {
        "model": model or settings.AGENT_PLANNER_MODEL,
        "instructions": instructions + " Use natural punctuation. Avoid em dashes in all user-facing text.",
        "input": input_items,
        "store": False,
        "max_output_tokens": max_output_tokens,
    }
    tool_list = list(tools or [])
    if search:
        tool_list.append({"type": "web_search"})
    if tool_list:
        payload["tools"] = tool_list
        payload["parallel_tool_calls"] = True
        payload["include"] = ["reasoning.encrypted_content"]
    result = await _post("responses", json=payload)
    status = result.get("status")
    reason = (result.get("incomplete_details") or {}).get("reason")
    if status not in {"completed", "incomplete"}:
        raise AgentProviderError("OpenAI could not complete the response. Try again.")
    output = result.get("output", [])
    calls = [item for item in output if item.get("type") == "function_call"]
    fragments, sources = [], {}
    for item in output:
        if item.get("type") != "message":
            continue
        for part in item.get("content", []):
            if part.get("type") != "output_text":
                continue
            fragments.append(part.get("text", ""))
            for annotation in part.get("annotations", []):
                url = annotation.get("url", "")
                if annotation.get("type") == "url_citation" and urlsplit(url).scheme in {"https", "http"}:
                    sources[url] = {"url": url, "title": annotation.get("title") or url}
    if status == "incomplete" and not calls and not fragments:
        if reason == "max_output_tokens":
            raise AgentProviderError("The response reached its output limit. Try again with a smaller request.")
        raise AgentProviderError("OpenAI could not complete the response. Try again.")
    return {
        "output": output,
        "calls": calls,
        "text": "\n".join(fragments).strip(),
        "sources": list(sources.values())[:16],
        "usage": result.get("usage") or {},
    }


async def transcribe(data, name, mime):
    result = await _post(
        "audio/transcriptions",
        files={"file": (name, data, mime)},
        data={"model": settings.AGENT_TRANSCRIBE_MODEL},
    )
    value = result.get("text")
    if not isinstance(value, str) or not value.strip():
        raise AgentProviderError("No clear speech was detected. Try recording again.")
    return value.strip()


MODEL_IMAGE_EDGE = 1280


def shrink_image(data, mime):
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return data, mime
    try:
        with Image.open(io.BytesIO(data)) as image:
            image = ImageOps.exif_transpose(image)
            if max(image.size) <= MODEL_IMAGE_EDGE and len(data) <= 600_000:
                return data, mime
            image.thumbnail((MODEL_IMAGE_EDGE, MODEL_IMAGE_EDGE))
            buffer = io.BytesIO()
            if mime == "image/png" and image.mode in {"RGBA", "LA", "P"}:
                image.save(buffer, format="PNG", optimize=True)
                return buffer.getvalue(), "image/png"
            image.convert("RGB").save(buffer, format="JPEG", quality=82, optimize=True)
            return buffer.getvalue(), "image/jpeg"
    except Exception:
        return data, mime


def attachment_content(attachment, data):
    mime = attachment["type"]
    if mime in {"image/jpeg", "image/png", "image/webp", "application/pdf"}:
        if mime != "application/pdf":
            data, mime = shrink_image(data, mime)
        encoded = base64.b64encode(data).decode("ascii")
        url = f"data:{mime};base64,{encoded}"
        if mime == "application/pdf":
            return {"type": "input_file", "filename": attachment["name"], "file_data": url}
        return {"type": "input_image", "image_url": url, "detail": "low"}
    try:
        if mime == DOCX:
            with ZipFile(io.BytesIO(data)) as archive:
                entry = archive.getinfo("word/document.xml")
                if entry.file_size > 1_000_000:
                    raise ValueError
                root = ElementTree.fromstring(archive.read(entry))
                ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
                value = "\n".join("".join(node.itertext()) for node in root.iter(ns + "p"))
        else:
            value = data.decode("utf-8-sig")
    except (UnicodeError, ValueError, KeyError, BadZipFile, ElementTree.ParseError):
        raise HTTPException(422, "This document could not be read. Try a PDF or a UTF-8 text file.") from None
    if len(value) > 120_000:
        raise HTTPException(422, "This text document is too long. Split it into smaller files.")
    return {"type": "input_text", "text": value}


def public_query(value):
    return _PERSONAL_DATA.sub("[private identifier removed]", value).strip()[:500]


def text_content(value):
    return [{"type": "input_text", "text": json.dumps(value, default=str, ensure_ascii=False)}]
