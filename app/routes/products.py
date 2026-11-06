from hashlib import sha256
import re
from pathlib import PurePath
from typing import Annotated
from urllib.parse import urlsplit

import httpx
from fastapi import APIRouter, Depends, File, HTTPException, Path, Query, Request, Response, UploadFile

from app.config.env import settings
from app.models.product import NewProduct, ProductChanges, ProductStatus, StockStatus
from app.routes.auth import get_current_user


LEGACY_SIGNING_KEY_FINGERPRINT = "4e34bbb554ae741f4c118a862f9dd17392b633e54c6be6ea731a9904b3617135"


async def require_products_access(response: Response, current_user: Annotated[dict, Depends(get_current_user)]):
    if not all((settings.WOOCOMMERCE_STORE_URL, settings.WOOCOMMERCE_CONSUMER_KEY,
                settings.WOOCOMMERCE_CONSUMER_SECRET)):
        raise HTTPException(503, "The WooCommerce connection has not been configured.")

    require_store_access(response, current_user, "Products")


def require_store_access(response, current_user, section):
    signing_key = settings.SECRET_KEY.strip().encode()
    if len(signing_key) < 32 or sha256(signing_key).hexdigest() == LEGACY_SIGNING_KEY_FINGERPRINT:
        raise HTTPException(503, f"Configure a private EasyCars signing key to enable {section} access.")

    allowed_ids = {user_id.strip() for user_id in settings.WOOCOMMERCE_ALLOWED_USER_IDS.split(",")}
    allowed_ids.discard("")
    if not allowed_ids:
        raise HTTPException(503, f"EasyCars {section} access has not been configured.")
    if str(current_user.get("_id")) not in allowed_ids:
        raise HTTPException(403, f"Your EasyCars account does not have {section} access.")
    response.headers["Cache-Control"] = "no-store"


async def require_write_header(request: Request):
    if request.headers.get("x-easycars-products") != "1":
        raise HTTPException(403, "The Products write request could not be verified.")


async def get_store_client():
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5), follow_redirects=False) as client:
        yield client


router = APIRouter(dependencies=[Depends(require_products_access)])
StoreClient = Annotated[httpx.AsyncClient, Depends(get_store_client)]
ProductId = Annotated[int, Path(gt=0)]
write_dependencies = [Depends(require_write_header)]

PRODUCT_FIELDS = (
    "id", "name", "slug", "permalink", "type", "status", "featured", "catalog_visibility",
    "description", "short_description", "sku", "price", "price_html", "regular_price", "sale_price",
    "stock_status", "manage_stock", "stock_quantity", "date_modified", "date_modified_gmt",
)
PRODUCT_COLLECTIONS = {
    "images": ("id", "src", "name", "alt"),
    "categories": ("id", "name", "slug"),
    "tags": ("id", "name", "slug"),
    "attributes": ("id", "name", "position", "visible", "variation", "options"),
}
MAX_IMAGE_SIZE = 10 * 1024 * 1024


def store_url():
    url = settings.WOOCOMMERCE_STORE_URL.strip().rstrip("/")
    try:
        parsed = urlsplit(url)
        if (parsed.scheme == "https" and parsed.hostname and parsed.username is None
                and parsed.password is None and not parsed.query and not parsed.fragment):
            return url
    except ValueError:
        pass
    raise HTTPException(503, "Set the WooCommerce store URL to its canonical HTTPS address.")


def store_error(response: httpx.Response):
    if response.is_redirect or response.status_code in (401, 403):
        raise HTTPException(502, "WordPress rejected the connection. Check the store URL and API permissions.")
    if response.status_code == 404:
        raise HTTPException(404, "WordPress could not find the requested listing or endpoint.")
    if response.status_code == 413:
        raise HTTPException(413, "The photo exceeds the WordPress upload limit.")
    if response.status_code == 429:
        raise HTTPException(503, "WordPress is temporarily limiting requests. Try again in a moment.")
    if response.status_code in (400, 409, 422):
        try:
            error = response.json()
        except ValueError:
            error = None
        if isinstance(error, dict) and error.get("code") in ("product_invalid_sku", "woocommerce_rest_product_invalid_sku"):
            raise HTTPException(422, "WooCommerce rejected this SKU. Check whether another listing already uses it.")
        raise HTTPException(422, "WordPress could not accept the submitted data. Check the listing fields or photo format.")
    raise HTTPException(502, "WordPress could not complete the request.")


async def request_store(client, method, path, credentials, **kwargs):
    try:
        response = await client.request(
            method, f"{store_url()}{path}", auth=credentials, headers={"Accept": "application/json"}, **kwargs,
        )
    except httpx.InvalidURL:
        raise HTTPException(503, "Set the WooCommerce store URL to its canonical HTTPS address.") from None
    except httpx.TimeoutException:
        detail = "WordPress took too long to respond."
        if method != "GET":
            detail += " Check whether your changes were saved before retrying."
        raise HTTPException(504, detail) from None
    except httpx.RequestError:
        raise HTTPException(502, "EasyCars could not reach WordPress.") from None

    if not response.is_success:
        store_error(response)
    try:
        return response.json(), response.headers
    except ValueError:
        raise HTTPException(502, "WordPress returned an unreadable response.") from None


async def request_woocommerce(client, method, path, **kwargs):
    credentials = (settings.WOOCOMMERCE_CONSUMER_KEY, settings.WOOCOMMERCE_CONSUMER_SECRET)
    return await request_store(client, method, f"/wp-json/wc/v3/products{path}", credentials, **kwargs)


def product_fields(product):
    if not isinstance(product, dict) or not isinstance(product.get("id"), int):
        raise HTTPException(502, "WooCommerce returned an invalid product.")
    listing = {field: product[field] for field in PRODUCT_FIELDS if field in product}
    for field, keys in PRODUCT_COLLECTIONS.items():
        items = product.get(field) or []
        if not isinstance(items, list):
            raise HTTPException(502, "WooCommerce returned invalid product details.")
        listing[field] = [{key: item[key] for key in keys if key in item} for item in items if isinstance(item, dict)]
    return listing


def pagination_total(headers, name, default):
    try:
        return max(0, int(headers.get(name, default)))
    except (ValueError, TypeError):
        return default


@router.get("")
async def list_products(
    client: StoreClient,
    page: Annotated[int, Query(ge=1)] = 1,
    per_page: Annotated[int, Query(ge=1, le=100)] = 20,
    search: Annotated[str | None, Query(max_length=200)] = None,
    status: ProductStatus | None = None,
    stock_status: StockStatus | None = None,
):
    filters = {"page": page, "per_page": per_page, "status": status or "any"}
    if search:
        filters["search"] = search
    if stock_status:
        filters["stock_status"] = stock_status
    data, headers = await request_woocommerce(client, "GET", "", params=filters)
    if not isinstance(data, list):
        raise HTTPException(502, "WooCommerce returned an invalid product list.")
    total = pagination_total(headers, "X-WP-Total", (page - 1) * per_page + len(data))
    return {
        "items": [product_fields(product) for product in data],
        "total": total,
        "page": page,
        "total_pages": pagination_total(headers, "X-WP-TotalPages", (total + per_page - 1) // per_page),
    }


async def list_terms(client, term):
    page = 1
    options = []
    while True:
        data, headers = await request_woocommerce(
            client, "GET", f"/{term}", params={"page": page, "per_page": 100, "hide_empty": "false"},
        )
        if not isinstance(data, list):
            raise HTTPException(502, "WooCommerce returned invalid product options.")
        options.extend(
            {"id": item["id"], "name": item["name"]}
            for item in data
            if isinstance(item, dict) and isinstance(item.get("id"), int) and isinstance(item.get("name"), str)
        )
        if page >= pagination_total(headers, "X-WP-TotalPages", page):
            return options
        page += 1


@router.get("/options")
async def product_options(client: StoreClient):
    return {"categories": await list_terms(client, "categories"), "tags": await list_terms(client, "tags")}


@router.get("/{product_id}")
async def get_product(product_id: ProductId, client: StoreClient):
    data, _ = await request_woocommerce(client, "GET", f"/{product_id}", params={"context": "edit"})
    return product_fields(data)


@router.post("", status_code=201, dependencies=write_dependencies)
async def create_product(product: NewProduct, client: StoreClient):
    fields = product.model_dump(exclude_unset=True)
    fields.update(type="simple", status=product.status)
    data, _ = await request_woocommerce(client, "POST", "", json=fields)
    return product_fields(data)


@router.patch("/{product_id}", dependencies=write_dependencies)
async def update_product(product_id: ProductId, product: ProductChanges, client: StoreClient):
    fields = product.model_dump(exclude_unset=True)
    if not fields:
        return await get_product(product_id, client)
    data, _ = await request_woocommerce(client, "PUT", f"/{product_id}", json=fields)
    return product_fields(data)


def photo_format(content):
    if content.startswith(b"\xff\xd8\xff"):
        return "jpg", "image/jpeg"
    if content.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png", "image/png"
    if content.startswith((b"GIF87a", b"GIF89a")):
        return "gif", "image/gif"
    if content.startswith(b"RIFF") and content[8:12] == b"WEBP":
        return "webp", "image/webp"
    if content[4:8] == b"ftyp" and content[8:12] in (b"avif", b"avis"):
        return "avif", "image/avif"
    raise HTTPException(415, "Upload a JPEG, PNG, WebP, GIF, or AVIF image.")


@router.post("/media", status_code=201, dependencies=write_dependencies)
async def upload_product_photo(file: Annotated[UploadFile, File()], client: StoreClient):
    credentials = (settings.WORDPRESS_USERNAME, settings.WORDPRESS_APPLICATION_PASSWORD)
    if not all(credentials):
        raise HTTPException(503, "WordPress photo uploads have not been configured.")

    content = await file.read(MAX_IMAGE_SIZE + 1)
    if len(content) > MAX_IMAGE_SIZE:
        raise HTTPException(413, "Upload photos smaller than 10 MB each.")
    extension, media_type = photo_format(content)
    stem = re.sub(r"[^a-zA-Z0-9_-]+", "-", PurePath(file.filename or "").stem).strip("-")[:64]
    filename = f"{stem or 'vehicle-photo'}.{extension}"
    data, _ = await request_store(
        client, "POST", "/wp-json/wp/v2/media", credentials, files={"file": (filename, content, media_type)},
    )
    if not isinstance(data, dict) or not isinstance(data.get("id"), int) or not data.get("source_url"):
        raise HTTPException(502, "WordPress did not return the uploaded photo.")
    return {"id": data["id"], "src": data["source_url"], "alt": data.get("alt_text", "")}
