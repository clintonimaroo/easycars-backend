import base64
from hashlib import sha256
import json

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config.env import settings
from app.models.user import UserModel
from app.routes import products
from app.utils import auth


STORE = "https://shop.example.test"
PRODUCTS = "/wp-json/wc/v3/products"
EDITOR_ID = "507f1f77bcf86cd799439011"
OTHER_ID = "507f1f77bcf86cd799439012"
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+jR2QAAAAASUVORK5CYII="
)


@pytest.fixture
def integration(monkeypatch):
    key = "test-only-signing-key-independent-from-default"
    config = {
        "SECRET_KEY": key,
        "WOOCOMMERCE_STORE_URL": STORE,
        "WOOCOMMERCE_CONSUMER_KEY": "test-woo-key",
        "WOOCOMMERCE_CONSUMER_SECRET": "test-woo-secret",
        "WOOCOMMERCE_ALLOWED_USER_IDS": EDITOR_ID,
        "WORDPRESS_USERNAME": "test-upload-user",
        "WORDPRESS_APPLICATION_PASSWORD": "test-upload-password",
    }
    for field, value in config.items():
        monkeypatch.setattr(settings, field, value)
    monkeypatch.setattr(auth, "JWT_SECRET", key)

    async def find_user(user_id):
        return {"_id": user_id, "role": "user" if user_id == EDITOR_ID else "admin"}

    monkeypatch.setattr(UserModel, "find_by_id", find_user)
    responses = {}
    requests = []

    def mock_wordpress(request):
        requests.append(request)
        assert request.url.scheme == "https"
        assert request.url.host == "shop.example.test"
        response = responses[(request.method, request.url.path)]
        return response(request) if callable(response) else response

    async def get_mock_client():
        async with httpx.AsyncClient(transport=httpx.MockTransport(mock_wordpress)) as client:
            yield client

    app = FastAPI()
    app.include_router(products.router, prefix="/products")
    app.dependency_overrides[products.get_store_client] = get_mock_client
    headers = {
        "Authorization": f"Bearer {auth.create_access_token({'id': EDITOR_ID})}",
        "X-EasyCars-Products": "1",
    }
    with TestClient(app) as client:
        yield client, headers, responses, requests


def test_requires_authorized_existing_account_and_write_header(integration, monkeypatch):
    client, headers, _, requests = integration
    assert client.get("/products").status_code == 401
    other = {"Authorization": f"Bearer {auth.create_access_token({'id': OTHER_ID})}"}
    assert client.get("/products", headers=other).status_code == 403
    assert client.post("/products", headers={"Authorization": headers["Authorization"]}, json={"name": "Test car"}).status_code == 403
    assert client.post(
        "/products/media", headers={"Authorization": headers["Authorization"]}, files={"file": ("car.png", PNG)},
    ).status_code == 403
    with monkeypatch.context() as config:
        config.setattr(products, "LEGACY_SIGNING_KEY_FINGERPRINT", sha256(settings.SECRET_KEY.encode()).hexdigest())
        assert client.get("/products", headers=headers).status_code == 503
    with monkeypatch.context() as config:
        config.setattr(settings, "SECRET_KEY", "short-test-key")
        assert client.get("/products", headers=headers).status_code == 503
    with monkeypatch.context() as config:
        config.setattr(settings, "WOOCOMMERCE_ALLOWED_USER_IDS", "")
        assert client.get("/products", headers=headers).status_code == 503
    with monkeypatch.context() as config:
        config.setattr(settings, "WOOCOMMERCE_CONSUMER_KEY", "")
        assert client.get("/products", headers=headers).status_code == 503
    assert not requests


def test_list_and_options_keep_pagination_without_private_metadata(integration):
    client, headers, responses, requests = integration
    responses[("GET", PRODUCTS)] = httpx.Response(
        200, headers={"X-WP-Total": "46", "X-WP-TotalPages": "3"}, json=[{
            "id": 12, "name": "2020 Honda Civic", "status": "draft", "price_html": "<span>$18,000</span>",
            "images": [{"id": 4, "src": f"{STORE}/car.jpg", "private": "test-private-image-field"}],
            "meta_data": [{"key": "test-private-field", "value": "test-private-value"}],
        }],
    )
    response = client.get("/products?page=2&search=Honda&stock_status=instock", headers=headers)
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "items": [{"id": 12, "name": "2020 Honda Civic", "status": "draft", "price_html": "<span>$18,000</span>",
                   "images": [{"id": 4, "src": f"{STORE}/car.jpg"}], "categories": [], "tags": [], "attributes": []}],
        "total": 46, "page": 2, "total_pages": 3,
    }
    assert requests[0].url.params == httpx.QueryParams(page=2, per_page=20, status="any", search="Honda", stock_status="instock")
    assert requests[0].headers["authorization"] == "Basic " + base64.b64encode(b"test-woo-key:test-woo-secret").decode()

    def categories(request):
        page = int(request.url.params["page"])
        return httpx.Response(200, headers={"X-WP-TotalPages": "2"}, json=[{"id": page, "name": f"Category {page}", "count": 12}])

    responses[("GET", f"{PRODUCTS}/categories")] = categories
    responses[("GET", f"{PRODUCTS}/tags")] = httpx.Response(200, json=[{"id": 3, "name": "New arrival"}])
    assert client.get("/products/options", headers=headers).json() == {
        "categories": [{"id": 1, "name": "Category 1"}, {"id": 2, "name": "Category 2"}],
        "tags": [{"id": 3, "name": "New arrival"}],
    }


def test_create_draft_and_publish_only_submitted_listing_fields(integration):
    client, headers, responses, requests = integration
    listing = {"name": "2020 Honda Civic", "description": "<p>Mileage: 45,000</p>", "regular_price": "18000.00",
               "categories": [{"id": 3}], "images": [{"id": 4}], "stock_status": "instock"}
    responses[("POST", PRODUCTS)] = httpx.Response(201, json={"id": 12, **listing, "status": "draft"})
    responses[("GET", f"{PRODUCTS}/12")] = httpx.Response(200, json={"id": 12, **listing, "status": "draft"})
    responses[("PUT", f"{PRODUCTS}/12")] = httpx.Response(200, json={"id": 12, **listing, "status": "publish"})
    response = client.post("/products", headers=headers, json=listing)
    assert response.status_code == 201
    assert json.loads(requests[0].content) == {**listing, "type": "simple", "status": "draft"}
    assert client.get("/products/12", headers=headers).json()["description"] == listing["description"]
    assert requests[1].url.params["context"] == "edit"
    response = client.patch("/products/12", headers=headers, json={"status": "publish", "sale_price": ""})
    assert response.status_code == 200
    assert response.json()["status"] == "publish"
    assert json.loads(requests[2].content) == {"status": "publish", "sale_price": ""}
    for disallowed in ({"meta_data": []}, {"type": "external"}, {"images": [{"src": "https://other.test/photo.jpg"}]}):
        assert client.patch("/products/12", headers=headers, json=disallowed).status_code == 422
    assert len(requests) == 3


def test_upload_uses_wordpress_media_credentials_and_validates_photos(integration, monkeypatch):
    client, headers, responses, requests = integration
    responses[("POST", "/wp-json/wp/v2/media")] = httpx.Response(
        201, json={"id": 4, "source_url": f"{STORE}/wp-content/uploads/car.png", "alt_text": "", "private": "ignored"},
    )
    with monkeypatch.context() as config:
        config.setattr(settings, "WORDPRESS_APPLICATION_PASSWORD", "")
        assert client.post("/products/media", headers=headers, files={"file": ("car.png", PNG)}).status_code == 503
    assert client.post("/products/media", headers=headers, files={"file": ("fake.png", b"text", "image/png")}).status_code == 415
    assert client.post("/products/media", headers=headers, files={"file": ("large.png", b"a" * (products.MAX_IMAGE_SIZE + 1))}).status_code == 413
    response = client.post("/products/media", headers=headers, files={"file": ("car.png", PNG, "image/png")})
    assert response.status_code == 201
    assert response.json() == {"id": 4, "src": f"{STORE}/wp-content/uploads/car.png", "alt": ""}
    assert len(requests) == 1
    assert requests[0].headers["authorization"] == "Basic " + base64.b64encode(b"test-upload-user:test-upload-password").decode()
    assert b'filename="car.png"' in requests[0].content
    assert PNG in requests[0].content


def test_store_redirects_are_not_followed_and_errors_are_safe(integration, monkeypatch):
    client, headers, responses, requests = integration
    responses[("GET", PRODUCTS)] = httpx.Response(302, headers={"Location": "https://elsewhere.test"})
    assert client.get("/products", headers=headers).status_code == 502
    responses[("GET", f"{PRODUCTS}/12")] = httpx.Response(404, json={"message": "test-private-upstream-error"})
    missing = client.get("/products/12", headers=headers)
    assert missing.status_code == 404
    assert "test-private-upstream-error" not in missing.text
    monkeypatch.setattr(settings, "WOOCOMMERCE_STORE_URL", "http://shop.example.test")
    assert client.get("/products", headers=headers).status_code == 503
    assert len(requests) == 2
