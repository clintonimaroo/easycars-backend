from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form, Request, Response
from fastapi.responses import StreamingResponse
from typing import Dict, List, Optional
from bson import ObjectId
import re
from urllib.parse import quote
import logging
from gridfs.errors import NoFile
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from app.config.env import settings
from app.models.document import DocumentModel, DocumentCreate
from app.routes.auth import get_current_user
from app.utils.gridfs_storage import upload_file_to_gridfs, delete_file_from_gridfs
from app.utils.mongodb import get_async_database

# Create router
router = APIRouter()

# Set up logging
logger = logging.getLogger(__name__)

PRIVATE_HEADERS = {
    "Cache-Control": "private, no-store",
    "X-Content-Type-Options": "nosniff",
    "Cross-Origin-Resource-Policy": "same-origin",
    "Content-Security-Policy": "frame-ancestors 'self'",
}


def _can_manage_documents(user):
    allowed = settings.DOCUMENTS_ADMIN_USER_IDS
    return str(user["_id"]) in {entry.strip() for entry in allowed.split(",") if entry.strip()}


async def _vehicle_owner(db, vehicle_id):
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(status_code=404, detail="Vehicle not found")
    vehicle = await db.vehicles.find_one({"_id": ObjectId(vehicle_id)}, {"created_by": 1})
    if not vehicle:
        raise HTTPException(status_code=404, detail="Vehicle not found")
    return str(vehicle.get("created_by") or "")


async def _find_authorized_document(db, user, query):
    actor = str(user["_id"])
    admin = _can_manage_documents(user)
    async for document in db.documents.find(query):
        if actor == str(document.get("created_by")) or admin:
            return document
        vehicle_id = document.get("vehicleId")
        if ObjectId.is_valid(vehicle_id) and await db.vehicles.find_one({
            "_id": ObjectId(vehicle_id), "created_by": actor,
        }, {"_id": 1}):
            return document
    raise HTTPException(status_code=404, detail="Document not found")


def _preview_type(prefix):
    if prefix.startswith(b"%PDF-"):
        return "application/pdf"
    if prefix.startswith(b"\x89PNG\r\n\x1a\n"):
        return "image/png"
    if prefix.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if prefix.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    if prefix.startswith(b"RIFF") and prefix[8:12] == b"WEBP":
        return "image/webp"
    if prefix[4:12] == b"ftypavif" or prefix[4:12] == b"ftypavis":
        return "image/avif"
    return None


def _byte_range(header, length):
    if not header:
        return 0, length - 1
    match = re.fullmatch(r"bytes=([0-9]{0,19})-([0-9]{0,19})", header)
    if match and any(match.groups()) and length:
        first, last = match.groups()
        start = int(first) if first else max(length - int(last), 0)
        end = min(int(last), length - 1) if first and last else length - 1
        if start <= end:
            return start, end
    raise HTTPException(status_code=416, detail="Invalid document range", headers={
        **PRIVATE_HEADERS,
        "Content-Range": f"bytes */{length}",
    })


async def _serve_document(file_id, request, user, inline):
    if not ObjectId.is_valid(file_id):
        raise HTTPException(status_code=404, detail="Document not found")
    db = await get_async_database()
    document = await _find_authorized_document(db, user, {"file_id": file_id})
    bucket = "agent_uploads" if document.get("file_bucket") == "agent_uploads" else "fs"
    try:
        stream = await AsyncIOMotorGridFSBucket(db, bucket_name=bucket).open_download_stream(ObjectId(file_id))
    except NoFile:
        raise HTTPException(status_code=404, detail="File not found") from None

    media_type = _preview_type(await stream.read(16))
    stream.seek(0)
    if inline and not media_type:
        raise HTTPException(status_code=415, detail="This document type is available as a download.")
    range_header = request.headers.get("range")
    first, last = _byte_range(range_header, stream.length)
    size = last - first + 1
    stream.seek(first)
    filename = quote(str(document.get("originalFilename") or "document"), safe="")
    disposition = "inline" if inline else "attachment"
    headers = {
        **PRIVATE_HEADERS,
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
        "Content-Length": str(size),
        "Accept-Ranges": "bytes",
    }
    if range_header:
        headers["Content-Range"] = f"bytes {first}-{last}/{stream.length}"

    async def contents():
        remaining = size
        while remaining > 0:
            chunk = await stream.read(min(remaining, 256 * 1024))
            if not chunk:
                break
            remaining -= len(chunk)
            yield chunk

    return StreamingResponse(
        contents(), status_code=206 if range_header else 200,
        media_type=media_type or "application/octet-stream", headers=headers,
    )

@router.post("/", response_model=Dict)
async def upload_documents(
    vehicleId: str = Form(...),
    documents: List[UploadFile] = File(...),
    documentType: Optional[str] = Form(None),
    description: Optional[str] = Form(None),
    current_user: Dict = Depends(get_current_user)
):
    """
    Upload documents for a vehicle
    """
    user_id = str(current_user["_id"])
    db = await get_async_database()
    if await _vehicle_owner(db, vehicleId) != user_id and not _can_manage_documents(current_user):
        raise HTTPException(status_code=404, detail="Vehicle not found")
    uploaded_docs = []

    try:
        for document in documents:
            # Read file content
            file_content = await document.read()
            
            # Add metadata for GridFS
            metadata = {
                "vehicleId": vehicleId,
                "documentType": documentType,
                "description": description,
                "uploaded_by": user_id
            }
            
            # Upload to GridFS
            upload_result = await upload_file_to_gridfs(
                file=file_content,
                filename=document.filename,
                content_type=document.content_type,
                metadata=metadata
            )
            
            if not upload_result["success"]:
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to store the uploaded document"
                )

            try:
                # Create document record
                document_data = {
                    "vehicleId": vehicleId,
                    "originalFilename": document.filename,
                    "fileType": document.content_type,
                    "fileSize": upload_result["size"],
                    "file_id": upload_result["file_id"],
                    "path": upload_result["url"],
                    "documentType": documentType,
                    "description": description
                }

                # Create DocumentCreate model
                document_create = DocumentCreate(**document_data)
                
                result = await DocumentModel.create_document(document_create, user_id)
                
                if result.get("success"):
                    doc = await DocumentModel.find_by_id(result.get("document_id"))
                    formatted_doc = DocumentModel.format_document_response(doc)
                    uploaded_docs.append(formatted_doc)
                else:
                    # If document record creation fails, delete the file from GridFS
                    delete_result = await delete_file_from_gridfs(upload_result["file_id"])
                    logger.error(f"Failed to create document record. GridFS cleanup result: {delete_result}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail="Failed to save the document record"
                    )
            except HTTPException:
                raise
            except Exception as exc:
                await delete_file_from_gridfs(upload_result["file_id"])
                logger.error("Document record save failed (%s)", type(exc).__name__)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail="Failed to save the document record"
                ) from None
        
        # Update vehicle document flags based on document type
        if documentType and vehicleId:
            update_fields = {}
            if documentType == 'bill_of_sale':
                update_fields["hasBillOfSale"] = True
            elif documentType == 'title_certificate':
                update_fields["hasTitleCertificate"] = True
            elif documentType == 'odometer_certification':
                update_fields["hasOdometerCertification"] = True
            elif documentType == 'power_of_attorney':
                update_fields["hasPowerOfAttorney"] = True
            elif documentType == 'contract_signed':
                update_fields["hasContractSigned"] = True
            elif documentType == 'buyers_guide':
                update_fields["hasBuyersGuide"] = True
                
            if update_fields:
                from app.models.vehicle import VehicleModel
                await VehicleModel.update_vehicle(vehicleId, update_fields)
                logger.info(f"Updated vehicle {vehicleId} with document flags: {update_fields}")
        
        return {
            "success": True,
            "message": f"Successfully uploaded {len(uploaded_docs)} documents",
            "documents": uploaded_docs
        }
    except HTTPException:
        raise
    except Exception as exc:
        logger.error("Document upload failed (%s)", type(exc).__name__)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Failed to upload documents"
        ) from None

@router.get("/", response_model=Dict)
async def get_documents(
    response: Response,
    vehicleId: str = Query(...),
    current_user: Dict = Depends(get_current_user),
):
    db = await get_async_database()
    actor = str(current_user["_id"])
    query = {"vehicleId": vehicleId}
    if not _can_manage_documents(current_user) and actor != await _vehicle_owner(db, vehicleId):
        query["created_by"] = actor
    records = await db.documents.find(query).sort("created_at", -1).to_list(length=100)
    response.headers.update(PRIVATE_HEADERS)
    return {
        "success": True,
        "total": len(records),
        "documents": [DocumentModel.format_document_response(doc) for doc in records],
    }


@router.get("/{document_id}", response_model=Dict)
async def get_document(
    document_id: str,
    response: Response,
    current_user: Dict = Depends(get_current_user),
):
    if not ObjectId.is_valid(document_id):
        raise HTTPException(status_code=400, detail="Invalid document ID format")
    db = await get_async_database()
    document = await _find_authorized_document(db, current_user, {"_id": ObjectId(document_id)})
    response.headers.update(PRIVATE_HEADERS)
    return {"document": DocumentModel.format_document_response(document)}


@router.get("/download/{file_id}")
async def download_file(file_id: str, request: Request, current_user: Dict = Depends(get_current_user)):
    return await _serve_document(file_id, request, current_user, inline=False)


@router.get("/preview/{file_id}")
async def preview_file(file_id: str, request: Request, current_user: Dict = Depends(get_current_user)):
    return await _serve_document(file_id, request, current_user, inline=True)


@router.delete("/{document_id}", response_model=Dict)
async def delete_document(
    document_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Delete a document
    """
    if not ObjectId.is_valid(document_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document ID format"
        )
    
    # Get document before deletion to get file path
    db = await get_async_database()
    document = await _find_authorized_document(db, current_user, {"_id": ObjectId(document_id)})
    
    # Delete document from database
    result = await DocumentModel.delete_document(document_id)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "Failed to delete document")
        )
    
    # Delete file from GridFS if file_id exists
    if document.get("file_id") and document.get("file_bucket") != "agent_uploads":
        delete_result = await delete_file_from_gridfs(document["file_id"])
        if not delete_result["success"]:
            logger.warning(f"Failed to delete file from GridFS: {delete_result.get('error')}")
    
    return {"success": True, "document_id": document_id} 