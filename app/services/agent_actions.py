from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from hashlib import sha256
from html import escape, unescape
import json
import re
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from bson import ObjectId
from fastapi import HTTPException, Response
import httpx
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

from app.config.env import settings
from app.models.product import NewProduct
from app.services.vehicle_template import FEATURE_GROUPS, SHORT_DESCRIPTION, SPECS, plain_text, render_vehicle_description
from app.routes.products import (
    MAX_IMAGE_SIZE,
    photo_format,
    product_fields,
    request_store,
    request_woocommerce,
    require_products_access,
    store_url,
)


PRODUCT_OPERATIONS = "agent_product_operations"
REMINDERS = "agent_reminders"
MEDIA_ROUTE = "/wp-json/wp/v2/media"
PRODUCT_MARKER = "easycars_agent_operation"
PHOTO_TYPES = {"image/jpeg", "image/png", "image/webp", "image/gif", "image/avif"}
STOCK_LABELS = {"instock": "In stock", "outofstock": "Out of stock", "onbackorder": "On backorder"}
INVENTORY_FIELDS = ("year", "make", "model", "vin", "stockNo", "mileage", "color", "price", "status")
VIN_FIELDS = {
    "ModelYear": "year", "Make": "make", "Model": "model", "Trim": "trim", "Series": "series",
    "BodyClass": "body", "DriveType": "drive", "FuelTypePrimary": "fuel",
    "DisplacementL": "engine_liters", "EngineCylinders": "engine_cylinders",
    "TransmissionStyle": "transmission", "TransmissionSpeeds": "transmission_speeds",
}


def _digest(value):
    encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()
    return sha256(encoded).hexdigest()


def _utcnow():
    return datetime.now(timezone.utc)


def _iso(value):
    return value.replace(tzinfo=timezone.utc).isoformat()


async def ensure_agent_action_indexes(db):
    await db[PRODUCT_OPERATIONS].create_index([("owner", 1), ("store", 1), ("product_id", 1)])
    await db[REMINDERS].create_index([("delivered_at", 1), ("remind_at", 1)])


async def search_inventory(db, user, query):
    if not user.get("_id"):
        raise HTTPException(401, "Sign in to search inventory.")
    terms = query.strip().split() if isinstance(query, str) else []
    if not terms or len(query) > 120 or len(terms) > 10:
        raise HTTPException(422, "Provide a VIN, stock number, or vehicle name to search.")
    filters = []
    for term in terms:
        matches = [{field: {"$regex": re.escape(term), "$options": "i"}}
                   for field in ("make", "model", "vin", "stockNo", "status")]
        if term.isascii() and term.isdigit() and len(term) == 4:
            matches.append({"year": int(term)})
        filters.append({"$or": matches})
    cursor = db.vehicles.find({"$and": filters}, {field: 1 for field in INVENTORY_FIELDS})
    vehicles = await cursor.sort("updated_at", -1).limit(11).to_list(length=11)
    return {
        "vehicles": [
            {**{field: vehicle.get(field) for field in INVENTORY_FIELDS},
             "id": str(vehicle["_id"]), "link": f"/inventory/{vehicle['_id']}"}
            for vehicle in vehicles[:10]
        ],
        "has_more": len(vehicles) > 10,
    }


async def count_logged_vehicles(db, user, period, timezone_name, as_of=None):
    owner = user.get("_id")
    if not owner:
        raise HTTPException(401, "Sign in to count your inventory records.")
    try:
        zone = ZoneInfo(timezone_name)
    except (TypeError, ValueError, ZoneInfoNotFoundError):
        raise HTTPException(422, "Choose a valid time zone to count your inventory records.") from None

    instant = as_of if as_of is not None else _utcnow()
    if instant.tzinfo is None:
        instant = instant.replace(tzinfo=timezone.utc)
    local_now = instant.astimezone(zone)
    today = local_now.replace(hour=0, minute=0, second=0, microsecond=0)
    month_start = today.replace(day=1)
    period = "_".join(period.lower().replace("_", " ").split()) if isinstance(period, str) else ""
    end = local_now
    if period == "this_month":
        start = month_start
        label = start.strftime("%B %Y")
    elif period == "last_month":
        start = (month_start - timedelta(days=1)).replace(day=1)
        end = month_start
        label = start.strftime("%B %Y")
    elif period == "this_week":
        start = today - timedelta(days=today.weekday())
        label = f"the week of {start:%B} {start.day}, {start:%Y}"
    elif period == "today":
        start = today
        label = f"{start:%B} {start.day}, {start:%Y}"
    else:
        raise HTTPException(422, "Choose today, this week, this month, or last month.")

    owner_ids = [str(owner)]
    if ObjectId.is_valid(owner_ids[0]):
        owner_ids.append(ObjectId(owner_ids[0]))
    created_at = {"$ifNull": ["$created_at", "$createdAt"]}
    date = {"$cond": [
        {"$in": [{"$type": created_at}, ["date", "string"]]},
        {"$convert": {"input": created_at, "to": "date", "onError": None, "onNull": None}},
        None,
    ]}
    count = await db.vehicles.count_documents({
        "created_by": {"$in": owner_ids},
        "$expr": {"$let": {"vars": {"created": date}, "in": {"$and": [
            {"$gte": ["$$created", start]},
            {"$lt": ["$$created", end]},
        ]}}},
    })
    return {
        "count": count,
        "period": label,
        "start_at": start.isoformat(),
        "end_at": end.isoformat(),
        "timezone": timezone_name,
    }


async def decode_vin(vin):
    vin = vin.strip().upper() if isinstance(vin, str) else ""
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", vin):
        raise HTTPException(422, "A complete 17-character VIN is required.")
    endpoint = f"https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/{vin}"
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(endpoint, params={"format": "json"})
            response.raise_for_status()
            result = response.json()["Results"][0]
    except (httpx.HTTPError, KeyError, IndexError, TypeError, ValueError):
        raise HTTPException(502, "The NHTSA VIN decoder is temporarily unavailable.") from None
    if not isinstance(result, dict):
        raise HTTPException(502, "The NHTSA VIN decoder returned an invalid response.")
    return {
        "vin": vin,
        "specs": {field: result[key] for key, field in VIN_FIELDS.items() if result.get(key)},
        "decoder_note": result.get("ErrorText") if result.get("ErrorCode") != "0" else None,
        "source": f"{endpoint}?format=json",
        "scope": "Manufacturer-reported vehicle specifications. Verify optional equipment with the vehicle build sheet.",
    }


def _amount(value):
    try:
        return Decimal(value) if value not in (None, "") else None
    except (InvalidOperation, TypeError, ValueError):
        return None


def _text(value):
    if not isinstance(value, str):
        return ""
    return " ".join(unescape(re.sub(r"<[^>]*>", " ", value)).split())


def _snapshot(product, *, ignore_images=False):
    ignored = {"status", "slug", "price_html", "permalink", "date_modified", "date_modified_gmt"}
    if ignore_images:
        ignored.add("images")
    return _digest({key: value for key, value in product_fields(product).items() if key not in ignored})


def _product_details(product, photos, specs=None, features=None, overview=None):
    price = product.get("price") or product.get("regular_price")
    stock = product.get("stock_status")
    details = [
        {"label": "Listing", "value": product["name"]},
        {"label": "Price", "value": price or "Unset"},
        {"label": "SKU", "value": product.get("sku") or "Generated when saved"},
        {"label": "Stock", "value": STOCK_LABELS.get(stock, "Unset")},
        {"label": "Photos", "value": ", ".join(photos) if photos else "None"},
    ]
    if specs is not None:
        details.append({"label": "Specs", "value": [
            {"label": label, "value": specs[key]} for key, label, _ in SPECS if specs.get(key)
        ]})
        details.append({"label": "Features", "value": [
            {"label": label, "value": features.get(key, [])} for key, label, _ in FEATURE_GROUPS if features.get(key)
        ]})
        details.append({"label": "Description", "value": list(overview or [])})
    else:
        details.append({"label": "Description", "value": _text(product.get("description")) or "None"})
    if product.get("sale_price"):
        details.append({"label": "Sale price", "value": product["sale_price"]})
    if product.get("catalog_visibility"):
        details.append({"label": "Catalog visibility", "value": product["catalog_visibility"].capitalize()})
    return details


def _wordpress_credentials():
    credentials = (settings.WORDPRESS_USERNAME, settings.WORDPRESS_APPLICATION_PASSWORD)
    if not all(credentials):
        raise HTTPException(503, "WordPress photo uploads have not been configured.")
    return credentials


async def _photo_record(db, owner, photo_id):
    if not ObjectId.is_valid(photo_id):
        raise HTTPException(422, "Choose the vehicle photos from your attachments.")
    record = await db["agent_uploads.files"].find_one({"_id": ObjectId(photo_id), "metadata.owner": owner})
    if not record or record.get("metadata", {}).get("type") not in PHOTO_TYPES:
        raise HTTPException(422, "Only attached vehicle images can be used in a listing.")
    if not 0 < record.get("length", 0) <= MAX_IMAGE_SIZE:
        raise HTTPException(413, "Use vehicle photos smaller than 10 MB each.")
    return record


def _validate_publish(fields, photos):
    amount = _amount(fields.get("regular_price"))
    if amount is None or not amount.is_finite() or amount <= 0:
        raise HTTPException(422, "Confirm a positive listing price before publishing.")
    if not _text(fields.get("description")):
        raise HTTPException(422, "Confirm a listing description before publishing.")
    if fields.get("stock_status") not in STOCK_LABELS:
        raise HTTPException(422, "Confirm whether the vehicle is in stock before publishing.")
    if not photos:
        raise HTTPException(422, "Choose at least one vehicle photo before publishing.")


async def prepare_product(db, user, args, attachments):
    await require_products_access(Response(), user)
    owner = str(user["_id"])
    store = store_url()
    publish = args.get("publish")
    if not isinstance(publish, bool):
        raise HTTPException(422, "Choose whether to create a draft or publish the listing.")
    product_id = args.get("product_id")
    if product_id is not None:
        if not publish or type(product_id) is not int or product_id <= 0:
            raise HTTPException(422, "Choose a valid EasyCars agent draft to publish.")
        if args.get("photo_ids") or any(args.get(field) is not None for field in (
            "name", "description", "regular_price", "sku", "stock_status",
        )):
            raise HTTPException(422, "Publish the existing draft as it is, or review changes in Products first.")
        operation = await db[PRODUCT_OPERATIONS].find_one({
            "owner": owner, "store": store, "product_id": product_id, "receipt.status": "draft",
        })
        if not operation:
            raise HTTPException(404, "An EasyCars agent draft with that ID was not found.")
        async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5)) as client:
            product = await _get_product(client, product_id, operation["_id"])
        if product.get("status") != "draft":
            raise HTTPException(409, "This listing is no longer a draft.")
        images = product.get("images") or []
        _validate_publish(product, images)
        photos = [image.get("name") or f"Photo {index}" for index, image in enumerate(images, 1)]
        payload = {
            "store": store, "publish": True, "product_id": product_id,
            "source_operation": operation["_id"], "snapshot": _snapshot(product),
        }
        return {
            "title": f"Publish {product['name']}", "summary": "Make this WooCommerce draft live.",
            "details": _product_details(product, photos), "payload": payload,
        }

    values = {
        field: args[field] for field in ("name", "description", "regular_price", "sku", "stock_status")
        if args.get(field) is not None
    }
    if isinstance(values.get("regular_price"), (int, float)) and not isinstance(values["regular_price"], bool):
        values["regular_price"] = f"{values['regular_price']:.2f}".rstrip("0").rstrip(".")
    if isinstance(values.get("regular_price"), str):
        price = re.sub(r"[^0-9.]", "", values["regular_price"].replace(",", ""))
        if price.count(".") > 1:
            price = price.replace(".", "", price.count(".") - 1)
        values["regular_price"] = price.rstrip(".") if price not in {"", "."} else values["regular_price"].strip()
    if isinstance(values.get("name"), str):
        values["name"] = " ".join(values["name"].split())[:500]
    if isinstance(values.get("sku"), str) and not values["sku"].strip():
        values.pop("sku")
    if values.get("stock_status") not in STOCK_LABELS:
        values.pop("stock_status", None)
    if isinstance(values.get("name"), str):
        values["name"] = plain_text(values["name"])
    if isinstance(values.get("description"), str):
        values["description"] = plain_text(values["description"])
    specs = {key: plain_text(args.get(key)) for key, _, _ in SPECS if isinstance(args.get(key), str) and plain_text(args.get(key))}
    raw_features = args.get("features") if isinstance(args.get("features"), dict) else {}
    features = {
        key: [plain_text(item) for item in raw_features.get(key, []) if isinstance(item, str) and plain_text(item)]
        for key, _, _ in FEATURE_GROUPS
    }
    overview = [plain_text(line) for line in (args.get("overview") or []) if isinstance(line, str) and plain_text(line)]
    templated = values.get("name") and "description" not in values and (specs or overview or any(features.values()))
    if templated:
        values["description"] = render_vehicle_description(values["name"], specs, features, overview)
        values["short_description"] = SHORT_DESCRIPTION
    if values.get("stock_status") is None and templated:
        values["stock_status"] = "instock"
    try:
        fields = NewProduct.model_validate(values, strict=True).model_dump(exclude_unset=True)
    except ValidationError:
        raise HTTPException(422, "Check the listing name, price, and stock status.") from None
    if fields.get("sku"):
        fields["sku"] = fields["sku"].strip()
        if "," in fields["sku"]:
            raise HTTPException(422, "Use a SKU without commas.")
    photo_ids = args.get("photo_ids", [])
    allowed = {str(item.get("id") or item.get("_id")) for item in attachments}
    images = [str(item.get("id") or item.get("_id")) for item in attachments if str(item.get("type", "")).startswith("image/")]
    if not isinstance(photo_ids, list):
        photo_ids = []
    photo_ids = [photo_id for photo_id in photo_ids if isinstance(photo_id, str) and photo_id in allowed][:50]
    if not photo_ids and images:
        photo_ids = images
    if not photo_ids and images == [] and publish:
        raise HTTPException(422, "Attach at least one vehicle photo so I can publish the listing.")
    photo_ids = list(dict.fromkeys(photo_ids))
    if publish:
        _validate_publish(fields, photo_ids)
    filenames = []
    if photo_ids:
        _wordpress_credentials()
        for photo_id in photo_ids:
            photo = await _photo_record(db, owner, photo_id)
            filenames.append(photo.get("metadata", {}).get("name") or photo.get("filename") or "Vehicle photo")
    details = _product_details(fields, filenames, specs if templated else None, features if templated else None, overview if templated else None)
    media = {"photos": [{"id": photo_id, "name": name} for photo_id, name in zip(photo_ids, filenames)]}
    if fields.get("description") and not templated:
        fields["description"] = "<p>" + escape(fields["description"].strip()).replace("\n", "<br>") + "</p>"
    return {
        "title": f"{'Publish' if publish else 'Draft'} {fields['name']}",
        "summary": "Create a WooCommerce draft and publish it." if publish else "Save a draft in Products.",
        "details": details,
        "media": media,
        "payload": {"store": store, "fields": fields, "photo_ids": photo_ids, "publish": publish},
    }


def _verify_product(data, marker):
    product_fields(data)
    if not any(isinstance(item, dict) and item.get("key") == PRODUCT_MARKER and item.get("value") == marker
               for item in data.get("meta_data") or []):
        raise HTTPException(409, "This listing belongs to another operation. Check Products before continuing.")
    return data


async def _get_product(client, product_id, marker):
    data, _ = await request_woocommerce(client, "GET", f"/{product_id}", params={"context": "edit"})
    return _verify_product(data, marker)


async def _find_draft(client, sku, marker):
    data, _ = await request_woocommerce(client, "GET", "", params={"sku": sku, "status": "any", "context": "edit"})
    if not isinstance(data, list):
        raise HTTPException(502, "WooCommerce returned an invalid listing search.")
    matches = [product for product in data if isinstance(product, dict) and product.get("sku", "").casefold() == sku.casefold()]
    if len(matches) > 1:
        raise HTTPException(409, "Multiple listings use this SKU. Resolve the conflict in Products before continuing.")
    return _verify_product(matches[0], marker) if matches else None


async def _claim_write(collection, key, step):
    result = await collection.update_one({"_id": key, step: {"$exists": False}}, {"$set": {step: _utcnow()}})
    return bool(result.modified_count)


async def _make_draft(db, client, operation, fields):
    key = operation["_id"]
    collection = db[PRODUCT_OPERATIONS]
    if operation.get("product_id"):
        return await _get_product(client, operation["product_id"], key)
    sku = fields.get("sku") or f"easycars-{key[:24]}"
    product = await _find_draft(client, sku, key)
    if product is None:
        if not await _claim_write(collection, key, "draft_requested_at"):
            raise HTTPException(503, "WooCommerce has not confirmed the draft yet. Check Products before trying again.")
        try:
            data, _ = await request_woocommerce(client, "POST", "", json={
                **fields, "sku": sku, "type": "simple", "status": "draft",
                "meta_data": [{"key": PRODUCT_MARKER, "value": key}],
            })
            product = _verify_product(data, key)
        except HTTPException as error:
            if error.status_code not in {502, 503, 504}:
                raise
            product = await _find_draft(client, sku, key)
            if product is None:
                raise HTTPException(503, "WooCommerce has not confirmed the draft yet. Check Products before trying again.") from None
    await collection.update_one(
        {"_id": key, "draft_snapshot": {"$exists": False}},
        {"$set": {"product_id": product["id"], "draft_snapshot": _snapshot(product, ignore_images=True)}},
    )
    return product


async def _find_photo(client, credentials, slug, product_id):
    data, _ = await request_store(client, "GET", MEDIA_ROUTE, credentials, params={
        "context": "edit", "slug": slug, "parent": product_id, "media_type": "image",
    })
    if not isinstance(data, list):
        raise HTTPException(502, "WordPress returned an invalid photo search.")
    matches = [item["id"] for item in data if isinstance(item, dict) and item.get("slug") == slug
               and item.get("post") == product_id and type(item.get("id")) is int]
    if len(matches) > 1:
        raise HTTPException(409, "Multiple uploaded photos matched. Check the draft gallery before continuing.")
    return matches[0] if matches else None


async def _upload_photo(db, client, operation, product_id, photo_id):
    collection = db[PRODUCT_OPERATIONS]
    key = operation["_id"]
    stored_id = operation.get("media", {}).get(photo_id)
    if stored_id:
        return stored_id
    credentials = _wordpress_credentials()
    slug = f"easycars-photo-{_digest([key, photo_id])[:24]}"
    media_id = await _find_photo(client, credentials, slug, product_id)
    if media_id is None:
        record = await _photo_record(db, operation["owner"], photo_id)
        bucket = AsyncIOMotorGridFSBucket(db, bucket_name="agent_uploads")
        stream = await bucket.open_download_stream(record["_id"])
        content = await stream.read(MAX_IMAGE_SIZE + 1)
        if len(content) > MAX_IMAGE_SIZE:
            raise HTTPException(413, "Use vehicle photos smaller than 10 MB each.")
        extension, media_type = photo_format(content)
        if media_type != record["metadata"]["type"]:
            raise HTTPException(415, "The photo content does not match its declared image type.")
        if not await _claim_write(collection, key, f"media_requested.{photo_id}"):
            raise HTTPException(503, "WordPress has not confirmed the photo yet. Check the draft gallery before trying again.")
        try:
            data, _ = await request_store(
                client, "POST", MEDIA_ROUTE, credentials,
                data={"slug": slug, "post": str(product_id)},
                files={"file": (f"{slug}.{extension}", content, media_type)},
            )
            if not isinstance(data, dict) or type(data.get("id")) is not int or data.get("post") != product_id:
                raise HTTPException(502, "WordPress did not confirm the uploaded photo.")
            media_id = data["id"]
        except HTTPException as error:
            if error.status_code not in {502, 503, 504}:
                raise
            media_id = await _find_photo(client, credentials, slug, product_id)
            if media_id is None:
                raise HTTPException(503, "WordPress has not confirmed the photo yet. Check the draft gallery before trying again.") from None
    await collection.update_one({"_id": key}, {"$set": {f"media.{photo_id}": media_id}})
    return media_id


def _product_matches(product, fields, sku):
    if _text(product.get("name")) != _text(fields["name"]):
        return False
    if product.get("sku") != sku:
        return False
    if _text(product.get("description")) != _text(fields.get("description")):
        return False
    if fields.get("stock_status") and product.get("stock_status") != fields["stock_status"]:
        return False
    return _amount(product.get("regular_price")) == _amount(fields.get("regular_price"))


async def _finish_listing(client, product, marker, publish, media_ids=None, owned=()):
    current_status = product.get("status")
    if current_status != "draft" and not (current_status == "publish" and publish):
        raise HTTPException(409, "This listing changed in Products. Review it before continuing.")
    changes = {}
    if media_ids is not None:
        existing = [image["id"] for image in product_fields(product)["images"] if "id" in image]
        foreign = [image_id for image_id in existing if image_id not in set(owned) | set(media_ids)]
        if foreign and existing != media_ids:
            raise HTTPException(409, "The draft gallery changed in Products. Review it before continuing.")
        if media_ids != existing:
            changes["images"] = [{"id": media_id} for media_id in media_ids]
    if publish and current_status != "publish":
        changes["status"] = "publish"
    if not changes:
        return product
    try:
        data, _ = await request_woocommerce(client, "PUT", f"/{product['id']}", json=changes)
        updated = _verify_product(data, marker)
    except HTTPException as error:
        if error.status_code not in {502, 503, 504}:
            raise
        updated = await _get_product(client, product["id"], marker)
        if (updated.get("status") != changes.get("status", current_status)
                or ("images" in changes and set(image.get("id") for image in product_fields(updated)["images"]) != set(media_ids))):
            raise error
    if media_ids is not None and set(image.get("id") for image in product_fields(updated)["images"]) != set(media_ids):
        raise HTTPException(502, "WooCommerce did not confirm the listing photos.")
    return updated


async def execute_product(db, user, payload, operation_id):
    await require_products_access(Response(), user)
    store = store_url()
    if store != payload["store"]:
        raise HTTPException(409, "The connected store changed. Review the listing again before continuing.")
    owner = str(user["_id"])
    key = _digest(["product", owner, str(operation_id)])
    fingerprint = _digest(payload)
    collection = db[PRODUCT_OPERATIONS]
    try:
        await collection.update_one({"_id": key}, {"$setOnInsert": {
            "owner": owner, "store": store, "fingerprint": fingerprint, "created_at": _utcnow(),
        }}, upsert=True)
    except DuplicateKeyError:
        pass
    operation = await collection.find_one({"_id": key})
    if not operation or operation["fingerprint"] != fingerprint:
        raise HTTPException(409, "This listing operation changed. Review it again before continuing.")
    if operation.get("receipt"):
        return operation["receipt"]
    publish = payload["publish"]
    async with httpx.AsyncClient(timeout=httpx.Timeout(25, connect=5)) as client:
        if "product_id" in payload:
            marker = payload["source_operation"]
            product = await _get_product(client, payload["product_id"], marker)
            if _snapshot(product) != payload["snapshot"]:
                raise HTTPException(409, "The draft changed since review. Review the updated listing before publishing.")
            product = await _finish_listing(client, product, marker, publish=True)
        else:
            fields = payload["fields"]
            sku = fields.get("sku") or f"easycars-{key[:24]}"
            if payload["photo_ids"]:
                _wordpress_credentials()
            product = await _make_draft(db, client, operation, fields)
            operation = await collection.find_one({"_id": key})
            if (_snapshot(product, ignore_images=True) != operation.get("draft_snapshot")
                    or not _product_matches(product, fields, sku)):
                raise HTTPException(409, "The draft changed in Products. Review it before continuing.")
            if product.get("status") == "draft":
                media_ids = [
                    await _upload_photo(db, client, operation, product["id"], photo_id)
                    for photo_id in payload["photo_ids"]
                ]
                product = await _get_product(client, product["id"], key)
                if _snapshot(product, ignore_images=True) != operation["draft_snapshot"]:
                    raise HTTPException(409, "The draft changed in Products. Review it before continuing.")
                operation = await collection.find_one({"_id": key})
                owned = list((operation.get("media") or {}).values())
                product = await _finish_listing(client, product, key, False, media_ids, owned)
                if publish:
                    product = await _get_product(client, product["id"], key)
                    if _snapshot(product, ignore_images=True) != operation["draft_snapshot"]:
                        raise HTTPException(409, "The draft changed in Products. Review it before continuing.")
                    gallery = [image.get("id") for image in product_fields(product)["images"]]
                    if gallery != media_ids and any(image_id not in set(owned) | set(media_ids) for image_id in gallery):
                        raise HTTPException(409, "The draft gallery changed in Products. Review it before continuing.")
            else:
                media_ids = [operation.get("media", {}).get(photo_id) for photo_id in payload["photo_ids"]]
            product = await _finish_listing(client, product, key, publish, media_ids)
    expected_status = "publish" if publish else "draft"
    if product.get("status") != expected_status:
        raise HTTPException(502, "WooCommerce did not confirm the expected listing status.")
    link = product.get("permalink") if publish else f"/products/{product['id']}/edit"
    if publish and (not isinstance(link, str) or not link.startswith("https://")):
        raise HTTPException(502, "WooCommerce did not return the published listing link.")
    result = {"product_id": product["id"], "status": expected_status, "link": link, "name": product["name"]}
    await collection.update_one({"_id": key}, {"$set": {"receipt": result, "completed_at": _utcnow()}})
    return result


def _parse_reminder(args):
    raw_url = args.get("lot_url")
    try:
        parsed = urlsplit(raw_url.strip()) if isinstance(raw_url, str) else None
        match = re.fullmatch(r"/lot/(\d{4,12})(?:/[\w-]+)*/?", parsed.path, re.ASCII) if parsed else None
        valid_url = (match and parsed.scheme == "https" and parsed.hostname in {"copart.com", "www.copart.com"}
                     and parsed.username is None and parsed.password is None and parsed.port in {None, 443})
    except ValueError:
        valid_url = False
    if not valid_url:
        raise HTTPException(422, "Choose the exact lot using its Copart HTTPS link.")
    lot = match.group(1)
    stamp = args.get("remind_at")
    try:
        remind_at = datetime.fromisoformat(stamp.replace("Z", "+00:00"))
        if remind_at.utcoffset() is None:
            raise ValueError
    except (AttributeError, TypeError, ValueError):
        raise HTTPException(422, "Confirm a reminder date and time with a time zone.") from None
    label = args.get("label")
    if label is not None and (not isinstance(label, str) or len(label) > 120):
        raise HTTPException(422, "Use a reminder label up to 120 characters.")
    return lot, remind_at, (label or "").strip() or f"Copart lot {lot}"


async def prepare_reminder(db, user, args):
    lot, remind_at, label = _parse_reminder(args)
    if remind_at <= _utcnow():
        raise HTTPException(422, "Choose a reminder time in the future.")
    url = f"https://www.copart.com/lot/{lot}"
    return {
        "title": f"Schedule a reminder for lot {lot}",
        "summary": "Send an EasyCars notification at the chosen time.",
        "details": [
            {"label": "Lot", "value": label},
            {"label": "Remind at", "value": remind_at.isoformat()},
            {"label": "Copart", "value": url},
        ],
        "payload": {"lot_url": url, "remind_at": remind_at.isoformat(), "label": label},
    }


async def execute_reminder(db, user, payload, operation_id):
    owner = str(user["_id"])
    lot, remind_at, label = _parse_reminder(payload)
    stamp = remind_at.astimezone(timezone.utc)
    key = _digest(["reminder", owner, lot, stamp.isoformat()])
    collection = db[REMINDERS]
    reminder = await collection.find_one({"_id": key})
    if reminder is None:
        if stamp <= _utcnow():
            raise HTTPException(422, "That reminder time passed. Choose a new time.")
        reminder = {
            "owner": owner, "lot": lot, "lot_url": f"https://www.copart.com/lot/{lot}",
            "remind_at": stamp, "label": label, "created_at": _utcnow(),
        }
        try:
            await collection.update_one({"_id": key}, {"$setOnInsert": reminder}, upsert=True)
        except DuplicateKeyError:
            pass
    return {
        "id": key, "lot_url": reminder["lot_url"], "remind_at": _iso(reminder["remind_at"]),
        "delivery": "EasyCars notifications", "notifications_url": "/notifications",
    }


async def deliver_due_reminders(db):
    now = _utcnow()
    collection = db[REMINDERS]
    cursor = collection.find({"delivered_at": None, "remind_at": {"$lte": now}}).sort("remind_at", 1)
    reminders = await cursor.limit(100).to_list(length=100)
    for reminder in reminders:
        notification_id = ObjectId(reminder["_id"][:24])
        try:
            await db.notifications.update_one({"_id": notification_id}, {"$setOnInsert": {
                "user_id": reminder["owner"], "title": "Copart reminder",
                "message": f"It's time to check {reminder['label']}.",
                "type": "info", "link": reminder["lot_url"], "read": False,
                "created_at": now,
            }}, upsert=True)
        except DuplicateKeyError:
            pass
        await collection.update_one({"_id": reminder["_id"], "delivered_at": None}, {"$set": {"delivered_at": now}})
    return len(reminders)
