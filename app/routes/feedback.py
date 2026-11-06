from html.parser import HTMLParser
import logging
import re
from typing import Annotated
from urllib.parse import urljoin, urlsplit, urlunsplit

import httpx
from fastapi import APIRouter, Depends, HTTPException, Query, Response

from app.config.env import settings
from app.routes.auth import get_current_user
from app.routes.products import StoreClient, require_store_access, store_url


JETPACK_RESPONSES = "/wp-json/wpcom/v2/forms/responses"
SETUP_DETAIL = "Configure a WordPress application password for an account that can view Jetpack form responses."


class FeedbackLogFilter(logging.Filter):
    def filter(self, record):
        args = record.args
        if not isinstance(args, tuple) or len(args) != 5:
            return True
        if record.name == "uvicorn.access" and isinstance(args[2], str):
            path = args[2].partition("?")[0]
            if path.rstrip("/") == "/feedback":
                record.args = (*args[:2], path, *args[3:])
        if record.name == "httpx" and isinstance(args[1], httpx.URL):
            if args[1].path.endswith(JETPACK_RESPONSES):
                record.args = (args[0], args[1].copy_with(query=None), *args[2:])
        return True


class FeedbackTextParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.text = []
        self.hidden_tag = None

    def handle_starttag(self, tag, attrs):
        if tag in {"script", "style"}:
            self.hidden_tag = tag
        elif tag == "br" and not self.hidden_tag:
            self.text.append("\n")

    def handle_endtag(self, tag):
        if self.hidden_tag == tag:
            self.hidden_tag = None
        elif tag in {"p", "div", "li"} and not self.hidden_tag:
            self.text.append("\n")

    def handle_data(self, data):
        if not self.hidden_tag:
            self.text.append(data)


def plain_text(value):
    if not isinstance(value, (str, int)) or isinstance(value, bool):
        return ""
    parser = FeedbackTextParser()
    parser.feed(str(value))
    parser.close()
    return "".join(parser.text).strip()


def feedback_source(value, origin):
    if not isinstance(value, str) or any(char.isspace() or char == "\\" for char in value):
        return ""
    try:
        store = urlsplit(origin)
        source = urlsplit(urljoin(f"{origin}/", value))
        if (value and source.scheme in {"http", "https"} and source.netloc == store.netloc):
            return urlunsplit((store.scheme, store.netloc, source.path, "", ""))
    except ValueError:
        pass
    return ""


def feedback_fields(item, origin):
    if not isinstance(item, dict) or type(item.get("id")) is not int or item["id"] <= 0:
        raise HTTPException(502, "Jetpack returned an invalid form response.")
    raw_fields = item.get("fields")
    fields = {}
    if isinstance(raw_fields, dict):
        for label, value in raw_fields.items():
            name = re.sub(r"^\d+_", "", label.strip()).strip().casefold().rstrip(":")
            fields[name] = value
    return {
        "id": item["id"],
        "date": plain_text(item.get("date")),
        "name": plain_text(fields.get("full name") or fields.get("name") or item.get("author_name")),
        "email": plain_text(fields.get("email") or fields.get("email address") or item.get("author_email")),
        "phone": plain_text(fields.get("phone") or fields.get("phone number") or fields.get("telephone")),
        "message": plain_text(fields.get("message") or fields.get("your message") or fields.get("comments")),
        "source": feedback_source(item.get("entry_permalink"), origin),
    }


async def require_feedback_access(response: Response, current_user: Annotated[dict, Depends(get_current_user)]):
    require_store_access(response, current_user, "Feedback")
    if not settings.WORDPRESS_USERNAME.strip() or not settings.WORDPRESS_APPLICATION_PASSWORD.strip():
        raise HTTPException(503, SETUP_DETAIL)


router = APIRouter(dependencies=[Depends(require_feedback_access)])


@router.get("")
async def list_feedback(
    client: StoreClient,
    page: Annotated[int, Query(ge=1, le=10000)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=200)] = None,
):
    origin = store_url()
    params = {"limit": per_page, "offset": (page - 1) * per_page}
    if search and search.strip():
        params["search"] = search.strip()
    credentials = (settings.WORDPRESS_USERNAME.strip(), settings.WORDPRESS_APPLICATION_PASSWORD.strip())
    try:
        response = await client.get(
            f"{origin}{JETPACK_RESPONSES}", auth=credentials, params=params, headers={"Accept": "application/json"},
        )
    except httpx.InvalidURL:
        raise HTTPException(503, "Set the WordPress store URL to its canonical HTTPS address.") from None
    except httpx.TimeoutException:
        raise HTTPException(504, "WordPress took too long to respond.") from None
    except httpx.RequestError:
        raise HTTPException(502, "EasyCars could not reach WordPress.") from None

    if response.status_code in {401, 403}:
        raise HTTPException(503, SETUP_DETAIL)
    if response.status_code == 404:
        raise HTTPException(503, "Enable the Jetpack Forms responses API on the connected WordPress site.")
    if response.status_code == 429:
        raise HTTPException(503, "WordPress is temporarily limiting requests. Try again in a moment.")
    if not response.is_success:
        raise HTTPException(502, "WordPress could not load form responses.")

    try:
        payload = response.json()
    except ValueError:
        raise HTTPException(502, "WordPress returned an unreadable response.") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("responses"), list):
        raise HTTPException(502, "Jetpack returned an invalid list of form responses.")
    totals = payload.get("totals")
    total = totals.get("inbox") if isinstance(totals, dict) else None
    if type(total) is not int or total < 0:
        raise HTTPException(502, "Jetpack returned an invalid form response count.")

    return {
        "items": [feedback_fields(item, origin) for item in payload["responses"]],
        "total": total,
        "page": page,
        "total_pages": (total + per_page - 1) // per_page,
    }
