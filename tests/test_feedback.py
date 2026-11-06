import base64
import logging

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config.env import settings
from app.routes import feedback, products
from app.routes.auth import get_current_user


@pytest.mark.parametrize("numbered_labels", [False, True])
def test_reads_only_allowlisted_feedback_without_private_metadata(monkeypatch, numbered_labels):
    config = {
        "SECRET_KEY": "a-private-signing-key-used-only-for-this-test",
        "WOOCOMMERCE_STORE_URL": "https://shop.example.test",
        "WOOCOMMERCE_ALLOWED_USER_IDS": "authorized-user",
        "WORDPRESS_USERNAME": "forms-reader",
        "WORDPRESS_APPLICATION_PASSWORD": "test-application-password",
    }
    for field, value in config.items():
        monkeypatch.setattr(settings, field, value)

    fields = {
        "Full Name": "Test &amp; Buyer", "Phone": "555-0123", "Email": "contact@example.test",
        "Message": "<p>Interested.<br />Is it available?</p><script>ignored()</script>",
        "Hidden": "private",
    }
    if numbered_labels:
        fields = {f"{index}_{label}": value for index, (label, value) in enumerate(fields.items(), 1)}
    requests = []
    upstream_response = httpx.Response(200, json={
        "responses": [{
            "id": 12,
            "date": "2026-10-01T08:15:00-05:00",
            "author_name": "Alternative name",
            "author_email": "alternative@example.test",
            "entry_permalink": "https://shop.example.test/product/car/?tracking=internal#form",
            "ip": "192.0.2.1",
            "fields": fields,
        }],
        "totals": {"inbox": 25, "spam": 8, "trash": 9},
    })

    def mock_wordpress(request):
        requests.append(request)
        return upstream_response

    async def get_mock_client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock_wordpress)) as client:
            yield client

    app = FastAPI()
    app.include_router(feedback.router, prefix="/feedback")
    app.dependency_overrides[products.get_store_client] = get_mock_client
    with TestClient(app) as client:
        assert client.get("/feedback").status_code == 401
        app.dependency_overrides[get_current_user] = lambda: {"_id": "other-user", "role": "admin"}
        assert client.get("/feedback").status_code == 403
        app.dependency_overrides[get_current_user] = lambda: {"_id": "authorized-user"}
        with monkeypatch.context() as config_override:
            config_override.setattr(settings, "WORDPRESS_APPLICATION_PASSWORD", "")
            assert client.get("/feedback").json() == {"detail": feedback.SETUP_DETAIL}
        assert not requests

        result = client.get("/feedback?page=2&search=car")
        assert result.status_code == 200
        assert result.headers["Cache-Control"] == "no-store"
        assert result.json() == {
            "items": [{
                "id": 12, "date": "2026-10-01T08:15:00-05:00", "name": "Test & Buyer",
                "email": "contact@example.test", "phone": "555-0123", "message": "Interested.\nIs it available?",
                "source": "https://shop.example.test/product/car/",
            }],
            "total": 25, "page": 2, "total_pages": 2,
        }
        request, = requests
        assert request.method == "GET"
        assert request.url.host == "shop.example.test"
        assert request.url.path == feedback.JETPACK_RESPONSES
        assert request.url.params == httpx.QueryParams(limit=20, offset=20, search="car")
        assert request.headers["Authorization"] == "Basic " + base64.b64encode(
            b"forms-reader:test-application-password",
        ).decode()

        upstream_response = httpx.Response(401, json={"message": "private upstream error"})
        result = client.get("/feedback")
        assert result.status_code == 503
        assert result.json() == {"detail": feedback.SETUP_DETAIL}


def test_redacts_feedback_search_from_access_logs():
    log_filter = feedback.FeedbackLogFilter()
    records = (
        logging.LogRecord("uvicorn.access", logging.INFO, "", 0, "%s %s %s %s %s",
                          ("localhost", "GET", "/feedback?search=private", "1.1", 200), None),
        logging.LogRecord("httpx", logging.INFO, "", 0, "%s %s %s %s %s",
                          ("GET", httpx.URL(f"https://shop.example.test{feedback.JETPACK_RESPONSES}?search=private"),
                           "1.1", 200, "OK"), None),
    )
    for record in records:
        assert log_filter.filter(record)
        assert "private" not in record.getMessage()
