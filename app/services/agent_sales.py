import asyncio
import hashlib
import json
import re
from datetime import date, datetime, timedelta, timezone
from typing import Annotated, Literal

from bson import ObjectId
from fastapi import HTTPException
from motor.motor_asyncio import AsyncIOMotorGridFSBucket
from pydantic import BaseModel, ConfigDict, EmailStr, Field, StringConstraints, ValidationError
from pymongo import ReturnDocument
from pymongo.errors import DuplicateKeyError, PyMongoError
from pymongo.read_concern import ReadConcern
from pymongo.write_concern import WriteConcern

from app.models.document import DocumentCreate
from app.models.vehicle import VehicleCreate

Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=200)]
DocumentType = Literal[
    "bill_of_sale", "title_certificate", "odometer_certification", "power_of_attorney",
    "contract_signed", "buyers_guide", "other",
]
DOCUMENT_FLAGS = {
    "bill_of_sale": "hasBillOfSale",
    "title_certificate": "hasTitleCertificate",
    "odometer_certification": "hasOdometerCertification",
    "power_of_attorney": "hasPowerOfAttorney",
    "contract_signed": "hasContractSigned",
    "buyers_guide": "hasBuyersGuide",
}
VEHICLE_FIELDS = {
    "stock_number": "stockNo",
    "invoice_number": "invoiceNo",
    "buyer_address": "buyerAddress",
    "buyer_city": "buyerCity",
    "buyer_state": "buyerState",
    "buyer_zip": "buyerZip",
    "buyer_phone": "buyerPhone",
}
VEHICLE_REQUIRED = {
    "year": "the model year", "make": "the make", "model": "the model", "mileage": "the odometer mileage",
    "color": "the exterior color",
}
SALE_LABELS = {
    **VEHICLE_REQUIRED, "vin": "the complete VIN", "price": "the actual sale price",
    "sale_date": "the original sale date", "buyer_name": "the buyer’s name", "buyer_email": "the buyer’s email address",
    "buyer_phone": "the buyer’s phone number", "customer_id": "the correct existing customer",
}
BUYER_CONTACT = "the buyer’s email or phone number, or their existing customer record"


class SaleSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    attachment_id: str
    page: Annotated[int, Field(strict=True, ge=1, le=10000)] | None = None
    document_type: DocumentType | None = None


class SaleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    vin: Text
    sale_date: date
    buyer_name: Text
    price: Annotated[float, Field(strict=True, ge=0, le=100_000_000, allow_inf_nan=False)]
    make: Text | None = None
    model: Text | None = None
    year: Annotated[int, Field(strict=True, ge=1886, le=2100)] | None = None
    mileage: Annotated[int, Field(strict=True, ge=0, le=10_000_000)] | None = None
    color: Text | None = None
    stock_number: Text | None = None
    invoice_number: Text | None = None
    buyer_phone: Text | None = None
    buyer_email: EmailStr | None = None
    buyer_address: Text | None = None
    buyer_city: Text | None = None
    buyer_state: Text | None = None
    buyer_zip: Text | None = None
    customer_id: Text | None = None
    source: list[SaleSource] = Field(default_factory=list, max_length=50)


class InventoryVehicleProposal(BaseModel):
    model_config = ConfigDict(extra="forbid")
    make: Text
    model: Text
    year: Annotated[int, Field(strict=True, ge=1886, le=2100)]
    vin: Text
    price: Annotated[float, Field(strict=True, ge=0, le=100_000_000, allow_inf_nan=False)]
    mileage: Annotated[int, Field(strict=True, ge=0, le=10_000_000)]
    color: Text
    status: Literal["available", "reserved", "maintenance", "pending"]
    stockNo: Text | None = None


def _error(detail, status=422):
    return HTTPException(status_code=status, detail=detail)


def _clarification(fields):
    labels = list(dict.fromkeys(fields))
    text = " and ".join([", ".join(labels[:-1]), labels[-1]]) if len(labels) > 1 else labels[0]
    return _error(f"Can you share {text}?")


def _digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def _stable_id(*parts):
    return ObjectId(_digest(json.dumps(parts, ensure_ascii=False))[:24])


def _same(a, b):
    if isinstance(a, str) and isinstance(b, str):
        return " ".join(a.split()).casefold() == " ".join(b.split()).casefold()
    return a == b


def _phone_digits(phone):
    digits = re.sub(r"[^0-9]", "", phone or "")
    return digits[1:] if len(digits) == 11 and digits.startswith("1") else digits


def _buyer_lookup(sale):
    conditions = []
    contacts = []
    if sale.get("buyer_email"):
        email = sale["buyer_email"].lower()
        conditions.append({"email": {"$regex": rf"^\s*{re.escape(email)}\s*$", "$options": "i"}})
        contacts.append(f"email:{email}")
    if sale.get("buyer_phone"):
        digits = _phone_digits(sale["buyer_phone"])
        if not 7 <= len(digits) <= 15:
            raise _error("Confirm the buyer’s complete phone number.")
        pattern = r"[^0-9]*".join(digits)
        prefix = r"(?:1[^0-9]*)?" if len(digits) == 10 else ""
        conditions.append({"phone": {"$regex": rf"^[^0-9]*{prefix}{pattern}[^0-9]*$"}})
        contacts.append(f"phone:{digits}")
    if contacts:
        conditions.append({"agent_sale_contacts": {"$in": contacts}})
    return conditions, contacts


async def _find_customer(db, sale, session=None):
    conditions, contacts = _buyer_lookup(sale)
    if not contacts and not sale.get("customer_id"):
        raise _error("What is the buyer’s email or phone number, or which existing customer should I use?")
    customer_id = sale.get("customer_id")
    if customer_id:
        if not ObjectId.is_valid(customer_id):
            raise _error("Choose a valid existing customer.")
        conditions.append({"_id": ObjectId(customer_id)})
    results = await db.customers.find({"$or": conditions}, session=session).to_list(length=2)
    if len(results) > 1:
        raise _error("The buyer details match more than one customer. Confirm the correct record and contact details.", 409)
    if not results and customer_id:
        raise _error("That customer could not be found. Confirm the buyer details.", 409)
    customer = results[0] if results else None
    if customer:
        if customer_id and str(customer["_id"]) != customer_id:
            raise _error("The contact details do not match the selected customer.", 409)
        if not _same(customer.get("name"), sale["buyer_name"]):
            raise _error("The buyer name conflicts with the customer who has these contact details.", 409)
        for field, normalize in (("email", str.casefold), ("phone", _phone_digits)):
            stored = customer.get(field)
            supplied = sale.get(f"buyer_{field}")
            if stored and supplied and normalize(stored.strip()) != normalize(supplied.strip()):
                raise _error(f"The buyer’s {field} conflicts with the matched customer. Confirm the correct contact details.", 409)
    return customer, contacts


async def _find_vehicle(db, vin, stock_number, session=None):
    conditions = [{"vin": {"$regex": rf"^\s*{re.escape(vin)}\s*$", "$options": "i"}}]
    if stock_number:
        conditions.append({"stockNo": {"$regex": rf"^\s*{re.escape(stock_number)}\s*$", "$options": "i"}})
    matches = await db.vehicles.find({"$or": conditions}, session=session).to_list(length=2)
    if len(matches) > 1:
        raise _error("The VIN and stock number match multiple inventory records. Which vehicle should I update?", 409)
    vehicle = matches[0] if matches else None
    if vehicle and not _same(vehicle.get("vin"), vin):
        raise _error("This stock number is assigned to a different VIN. Confirm the VIN and stock number.", 409)
    if vehicle and stock_number and vehicle.get("stockNo") and not _same(vehicle["stockNo"], stock_number):
        raise _error("This VIN has a different stock number in inventory. Confirm the correct number.", 409)
    return vehicle


def _inventory_fields(args):
    try:
        proposal = InventoryVehicleProposal.model_validate(args)
    except ValidationError as exc:
        labels = {
            **VEHICLE_REQUIRED, "vin": "the complete VIN", "price": "the inventory price",
            "status": "the inventory status", "stockNo": "the stock number if you want it included",
        }
        fields = [labels[error["loc"][0]] for error in exc.errors() if error["loc"] and error["loc"][0] in labels]
        raise _clarification(fields or ["the year, make, model, VIN and status"]) from None
    fields = proposal.model_dump()
    fields["vin"] = proposal.vin.upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", fields["vin"]):
        raise _error("Confirm the complete 17-character VIN.")
    if round(proposal.price, 2) != proposal.price:
        raise _error("Confirm the inventory price in dollars and cents.")
    return fields


async def prepare_inventory_vehicle(db, user, args):
    fields = _inventory_fields(args)
    if await _find_vehicle(db, fields["vin"], fields["stockNo"]):
        raise _error("This VIN is already in Inventory. Review the existing vehicle record.", 409)
    name = f"{fields['year']} {fields['make']} {fields['model']}"
    return {
        "title": f"Add {name} to Inventory",
        "summary": "Create an internal vehicle record.",
        "details": [
            {"label": "VIN", "value": fields["vin"]},
            {"label": "Status", "value": fields["status"].title()},
            {"label": "Price", "value": f"${fields['price']:,.2f}"},
            {"label": "Mileage", "value": f"{fields['mileage']:,}"},
            {"label": "Color", "value": fields["color"]},
            *([{"label": "Stock number", "value": fields["stockNo"]}] if fields["stockNo"] else []),
        ],
        "payload": fields,
    }


async def _lock_stock(db, stock_number, session):
    if stock_number:
        await db.agent_sale_locks.update_one(
            {"_id": _digest("stock:" + stock_number.casefold())},
            {"$inc": {"version": 1}}, upsert=True, session=session,
        )


async def _run_transaction(db, callback, conflict_detail):
    async with await db.client.start_session() as session:
        for attempt in range(5):
            session.start_transaction(read_concern=ReadConcern("snapshot"), write_concern=WriteConcern("majority"))
            try:
                result = await callback(session)
                for commit_attempt in range(5):
                    try:
                        await session.commit_transaction()
                        return result
                    except PyMongoError as exc:
                        if not exc.has_error_label("UnknownTransactionCommitResult") or commit_attempt == 4:
                            raise
                        await asyncio.sleep(0.05 * (commit_attempt + 1))
            except BaseException as exc:
                if session.in_transaction:
                    try:
                        await session.abort_transaction()
                    except PyMongoError:
                        pass
                retryable = isinstance(exc, DuplicateKeyError) or (
                    isinstance(exc, PyMongoError) and exc.has_error_label("TransientTransactionError")
                    and not exc.has_error_label("UnknownTransactionCommitResult")
                )
                if not retryable:
                    raise
                if attempt == 4:
                    raise _error(conflict_detail, 409) from None
                await asyncio.sleep(0.05 * (attempt + 1))


async def _commit_inventory_vehicle(db, actor, fields, receipt, vehicle_id, session=None):
    receipt_query = {"_id": vehicle_id, "agent_inventory_receipt": receipt}
    if not await db.vehicles.find_one(receipt_query, session=session):
        await _lock_stock(db, fields["stockNo"], session)
        if await _find_vehicle(db, fields["vin"], fields["stockNo"], session):
            raise _error("This VIN is already in Inventory. Review the existing vehicle record.", 409)
        now = datetime.now(timezone.utc)
        vehicle = VehicleCreate.model_validate({**fields, "isNew": None}).model_dump(mode="json")
        await db.vehicles.insert_one({
            **vehicle, "_id": vehicle_id, "agent_inventory_receipt": receipt,
            "created_by": actor, "created_at": now, "updated_at": now,
        }, session=session)
    return {"message": "Vehicle added to Inventory.", "vehicle_id": str(vehicle_id), "url": f"/inventory/{vehicle_id}"}


async def execute_inventory_vehicle(db, user, payload, operation_id):
    fields = _inventory_fields(payload)
    actor = str(user["_id"])
    receipt = _digest(json.dumps([actor, operation_id, fields], sort_keys=True))
    vehicle_id = _stable_id("sale-vehicle", fields["vin"])
    if fields["stockNo"]:
        topology = await db.client.admin.command("hello")
        if not topology.get("setName") and topology.get("msg") != "isdbgrid":
            raise _error("Saving stock numbers requires a MongoDB replica set so inventory changes can save together.", 503)
        return await _run_transaction(
            db, lambda active: _commit_inventory_vehicle(db, actor, fields, receipt, vehicle_id, active),
            "This VIN or stock number is already in Inventory. Review the existing record.",
        )
    else:
        try:
            return await _commit_inventory_vehicle(db, actor, fields, receipt, vehicle_id)
        except DuplicateKeyError:
            if not await db.vehicles.find_one({"_id": vehicle_id, "agent_inventory_receipt": receipt}):
                raise _error("This VIN is already in Inventory. Review the existing record.", 409) from None
            return {"message": "Vehicle added to Inventory.", "vehicle_id": str(vehicle_id), "url": f"/inventory/{vehicle_id}"}


def _vehicle_changes(sale, vehicle):
    fields = {name: sale[name] for name in ("make", "model", "year", "mileage", "color") if sale.get(name) is not None}
    for field in ("make", "model", "year"):
        if vehicle and vehicle.get(field) is not None and field in fields and not _same(vehicle[field], fields[field]):
            raise _error(f"The {field} on the sale paperwork conflicts with inventory. Confirm the correct value.", 409)
    if not vehicle:
        missing = [field for field in ("make", "model", "year", "mileage", "color") if field not in fields]
        if missing:
            raise _clarification([VEHICLE_REQUIRED[field] for field in missing])
    fields.update({
        "vin": sale["vin"],
        "status": "sold",
        "dateSold": sale["sale_date"],
        "price": sale["price"],
        "soldTo": sale["buyer_name"],
        **{target: sale[key] for key, target in VEHICLE_FIELDS.items() if sale.get(key) is not None},
    })
    if vehicle and _same(vehicle.get("status"), "sold"):
        for field in ("dateSold", "soldTo", "price", "invoiceNo", "buyerPhone"):
            existing = vehicle.get(field)
            supplied = fields.get(field)
            if field == "dateSold" and isinstance(existing, (str, date)):
                existing = str(existing)[:10]
            if field == "buyerPhone" and existing and supplied:
                existing, supplied = _phone_digits(existing), _phone_digits(supplied)
            if existing not in (None, "") and supplied is not None and not _same(existing, supplied):
                raise _error("This VIN is already marked sold with different sale details. Review the existing inventory record.", 409)
    return fields


async def _owned_sources(db, actor, sources, attachment_ids=None):
    files = {}
    for source in sources:
        file_id = source["attachment_id"]
        if not ObjectId.is_valid(file_id) or (attachment_ids is not None and file_id not in attachment_ids):
            raise _error("I can only attach files you provided in this conversation.")
        if file_id not in files:
            record = await db["agent_uploads.files"].find_one({"_id": ObjectId(file_id), "metadata.owner": actor})
            if not record:
                raise _error("An attachment is no longer available. Upload the file again.", 409)
            metadata = record.get("metadata", {})
            if not isinstance(record.get("length"), int) or record["length"] <= 0:
                raise _error("A sale attachment is empty or unavailable.")
            files[file_id] = {
                "id": file_id,
                "name": metadata.get("name") or record.get("filename") or "Sale document",
                "type": metadata.get("type") or "application/octet-stream",
                "size": record["length"],
                "digest": metadata.get("sha256"),
                "document_types": [],
                "pages": [],
            }
        item = files[file_id]
        document_type = source.get("document_type")
        if document_type in DOCUMENT_FLAGS and document_type not in item["document_types"]:
            item["document_types"].append(document_type)
        if source["page"] is not None and source["page"] not in item["pages"]:
            item["pages"].append(source["page"])
    return list(files.values())


async def prepare_sale(db, user, args, attachments):
    has_buyer_contact = isinstance(args, dict) and any(args.get(field) for field in ("buyer_email", "buyer_phone", "customer_id"))
    try:
        proposal = SaleProposal.model_validate(args)
    except ValidationError as exc:
        fields = [SALE_LABELS[error["loc"][0]] for error in exc.errors() if error["loc"] and error["loc"][0] in SALE_LABELS]
        if not has_buyer_contact:
            fields.append(BUYER_CONTACT)
        raise _clarification(fields or ["the correct sale and supporting paperwork"]) from None
    sale = proposal.model_dump(mode="json")
    sale["vin"] = proposal.vin.upper()
    if not re.fullmatch(r"[A-HJ-NPR-Z0-9]{17}", sale["vin"]):
        raise _error("Confirm the complete 17-character VIN.")
    if round(proposal.price, 2) != proposal.price:
        raise _error("Confirm the sale price in dollars and cents.")
    if proposal.sale_date < date(1886, 1, 1) or proposal.sale_date > date.today() + timedelta(days=1):
        raise _error("Confirm the original sale date.")
    if proposal.year is not None and proposal.year > proposal.sale_date.year + 2:
        raise _error("Confirm the model year and original sale date.")

    actor = str(user["_id"])
    attachment_ids = {str(item.get("_id") or item.get("id")) for item in attachments}
    sources = await _owned_sources(db, actor, sale["source"], attachment_ids)
    vehicle = await _find_vehicle(db, sale["vin"], sale["stock_number"])
    if not has_buyer_contact:
        missing = [label for field, label in VEHICLE_REQUIRED.items() if not vehicle and sale.get(field) is None]
        raise _clarification([*missing, BUYER_CONTACT])
    fields = _vehicle_changes(sale, vehicle)
    customer, _ = await _find_customer(db, sale)
    name = " ".join(str(fields.get(key) or (vehicle or {}).get(key) or "") for key in ("year", "make", "model")).strip()
    changes = []
    if vehicle:
        for field in ("status", "dateSold", "price", "mileage", "color", "stockNo", "invoiceNo"):
            if field in fields and not _same(vehicle.get(field), fields[field]):
                changes.append({"field": field, "before": vehicle.get(field), "after": fields[field]})

    return {
        "title": f"Record sale · {name or sale['vin']}",
        "summary": f"{'Update the existing' if vehicle else 'Create a sold'} vehicle and {'link the existing' if customer else 'create the'} customer.",
        "details": {
            "VIN": sale["vin"],
            "Sale date": sale["sale_date"],
            "Sale price": f"${sale['price']:,.2f}",
            "Buyer": sale["buyer_name"],
            "Inventory": "Update existing vehicle" if vehicle else "Create vehicle",
            "Attachments": [
                f"{item['name']} · pages {', '.join(map(str, sorted(item['pages'])))}" if item["pages"] else item["name"]
                for item in sources
            ],
            "Changes": changes,
        },
        "payload": {
            "sale": sale,
            "vehicle_id": str(vehicle["_id"]) if vehicle else None,
            "vehicle_version": vehicle["updated_at"].isoformat() if vehicle and isinstance(vehicle.get("updated_at"), datetime) else None,
            "customer_id": str(customer["_id"]) if customer else None,
        },
    }


async def _attachment_digest(db, source):
    digest = source.get("digest")
    if isinstance(digest, str) and re.fullmatch(r"[a-fA-F0-9]{64}", digest):
        return digest.lower()
    stream = await AsyncIOMotorGridFSBucket(db, bucket_name="agent_uploads").open_download_stream(ObjectId(source["id"]))
    checksum = hashlib.sha256()
    while chunk := await stream.readchunk():
        checksum.update(chunk)
    return checksum.hexdigest()


async def _link_documents(db, vehicle_id, actor, sources, session):
    linked = []
    flags = {}
    now = datetime.now(timezone.utc)
    for source in sources:
        document_id = _stable_id("sale-attachment", vehicle_id, source["digest"])
        file_id = source["id"]
        document_types = source["document_types"]
        primary_type = "bill_of_sale" if "bill_of_sale" in document_types else next(iter(document_types), None)
        linked_to_another_vehicle = await db.documents.find_one(
            {"agent_upload_digest": source["digest"], "vehicleId": {"$ne": vehicle_id}}, session=session,
        )
        if linked_to_another_vehicle:
            raise _error("This file is already attached to another vehicle. Upload the pages for this sale separately.", 409)
        doc = DocumentCreate(
            vehicleId=vehicle_id, originalFilename=source["name"], fileType=source["type"], fileSize=source["size"],
            file_id=file_id, path=f"/documents/download/{file_id}", documentType=primary_type,
        ).model_dump()
        result = await db.documents.find_one_and_update(
            {"_id": document_id},
            {
                "$setOnInsert": {
                    **doc, "file_bucket": "agent_uploads", "created_at": now, "updated_at": now, "created_by": actor,
                    "agent_upload_digest": source["digest"],
                },
                "$addToSet": {"agent_document_types": {"$each": document_types}},
            },
            upsert=True,
            return_document=ReturnDocument.AFTER,
            session=session,
        )
        linked.append(str(result["_id"]))
        for document_type in result.get("agent_document_types", []):
            flags[DOCUMENT_FLAGS[document_type]] = True
    return list(dict.fromkeys(linked)), flags


async def _commit_sale(db, actor, payload, sources, receipt_id, payload_hash, session):
    previous = await db.agent_sale_receipts.find_one({"_id": receipt_id}, session=session)
    if previous:
        if previous["payload_hash"] != payload_hash:
            raise _error("This sale operation has already been used for a different proposal.", 409)
        return previous["result"]

    sale = payload["sale"]
    await _lock_stock(db, sale.get("stock_number"), session)
    current = await _find_vehicle(db, sale["vin"], sale["stock_number"], session)
    fields = _vehicle_changes(sale, current)
    expected_vehicle_id = payload["vehicle_id"]
    if expected_vehicle_id and (not current or str(current["_id"]) != expected_vehicle_id):
        raise _error("The inventory record changed since this sale was prepared. Prepare it again.", 409)
    if current:
        unchanged = all(_same(current.get(key), value) for key, value in fields.items())
        current_version = current["updated_at"].isoformat() if isinstance(current.get("updated_at"), datetime) else None
        if (not expected_vehicle_id or current_version != payload["vehicle_version"]) and not unchanged:
            raise _error("Inventory changed since this sale was prepared. Review the current record and try again.", 409)

    customer, contacts = await _find_customer(db, sale, session)
    if payload["customer_id"] and (not customer or str(customer["_id"]) != payload["customer_id"]):
        raise _error("The matched customer changed since this sale was prepared. Prepare it again.", 409)
    if current and current.get("agent_sale_customer_id") and (not customer or current["agent_sale_customer_id"] != str(customer["_id"])):
        raise _error("This sale is already linked to a different customer.", 409)
    now = datetime.now(timezone.utc)
    vehicle_id = current["_id"] if current else _stable_id("sale-vehicle", sale["vin"])
    if not customer:
        customer_id = _stable_id("sale-customer", contacts[0])
        customer_fields = {
            "name": sale["buyer_name"], "vehicles": [], "createdAt": now,
            **{key: sale[f"buyer_{suffix}"] for key, suffix in (
                ("email", "email"), ("phone", "phone"), ("address", "address"), ("city", "city"),
                ("state", "state"), ("zipCode", "zip"),
            ) if sale.get(f"buyer_{suffix}")},
        }
        await db.customers.insert_one({"_id": customer_id, **customer_fields, "agent_sale_contacts": contacts}, session=session)
    else:
        customer_id = customer["_id"]

    document_ids, flags = await _link_documents(db, str(vehicle_id), actor, sources, session)
    fields.update({"updated_at": now, "agent_sale_customer_id": str(customer_id), **flags})
    if current:
        await db.vehicles.update_one({"_id": vehicle_id}, {"$set": fields}, session=session)
    else:
        validated = VehicleCreate.model_validate({key: value for key, value in fields.items() if key != "updated_at"}).model_dump(mode="json")
        await db.vehicles.insert_one({
            **validated, **fields, "_id": vehicle_id, "created_at": now, "created_by": actor,
        }, session=session)
    customer_update = {"$addToSet": {"vehicles": str(vehicle_id)}, "$set": {"updatedAt": now}}
    if contacts:
        customer_update["$addToSet"]["agent_sale_contacts"] = {"$each": contacts}
    await db.customers.update_one({"_id": customer_id}, customer_update, session=session)
    result = {
        "message": "Sale saved to Inventory.",
        "vehicle_id": str(vehicle_id),
        "customer_id": str(customer_id),
        "document_ids": document_ids,
        "url": f"/inventory/{vehicle_id}",
    }
    await db.agent_sale_receipts.insert_one({
        "_id": receipt_id, "payload_hash": payload_hash, "result": result, "created_at": now,
    }, session=session)
    return result


async def execute_sale(db, user, payload, operation_id):
    actor = str(user["_id"])
    receipt_id = _stable_id("sale-operation", actor, operation_id)
    payload_hash = _digest(json.dumps(payload, sort_keys=True, ensure_ascii=False))
    previous = await db.agent_sale_receipts.find_one({"_id": receipt_id})
    if previous:
        if previous["payload_hash"] != payload_hash:
            raise _error("This sale operation has already been used for a different proposal.", 409)
        return previous["result"]
    sources = await _owned_sources(db, actor, payload["sale"]["source"])
    for source in sources:
        source["digest"] = await _attachment_digest(db, source)
    await db.customers.create_index("agent_sale_contacts", unique=True, sparse=True)
    await db.documents.create_index("agent_upload_digest", unique=True, sparse=True)
    topology = await db.client.admin.command("hello")
    if not topology.get("setName") and topology.get("msg") != "isdbgrid":
        raise _error("Sale imports require a MongoDB replica set so vehicle, customer and documents can save together.", 503)
    return await _run_transaction(
        db, lambda active: _commit_sale(db, actor, payload, sources, receipt_id, payload_hash, active),
        "An inventory or customer record changed while the sale was saving. Review it and try again.",
    )
