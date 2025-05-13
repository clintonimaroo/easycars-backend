from fastapi import APIRouter, Depends, HTTPException, status, Query, UploadFile, File, Form
from fastapi.responses import JSONResponse
from typing import Dict, List, Optional
from bson import ObjectId
import os
import shutil
import uuid
from datetime import datetime
import logging

from app.models.document import DocumentModel, DocumentCreate
from app.routes.auth import get_current_user
from app.utils.cloudinary import upload_file, delete_file

# Create router
router = APIRouter()

# Set up logging
logger = logging.getLogger(__name__)

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
    user_id = str(current_user.get("_id"))
    uploaded_docs = []
    
    logger.info(f"Uploading documents for vehicle {vehicleId}. Document type: {documentType}")
    logger.info(f"Received {len(documents)} files")
    
    try:
        for document in documents:
            # Log file details
            logger.info(f"Processing file: {document.filename}, content_type: {document.content_type}")
            
            # Read file content
            file_content = await document.read()
            logger.info(f"File content read, size: {len(file_content)} bytes")
            
            # Upload to Cloudinary
            upload_result = await upload_file(
                file=file_content,
                filename=document.filename,
                folder=f"vehicles/{vehicleId}"
            )
            
            if not upload_result["success"]:
                error_msg = f"Failed to upload {document.filename}: {upload_result.get('error', 'Unknown error')}"
                logger.error(error_msg)
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=error_msg
                )
            
            logger.info(f"File uploaded to Cloudinary: {upload_result['public_id']}")
            
            try:
                # Create document record
                document_data = {
                    "vehicleId": vehicleId,
                    "originalFilename": document.filename,
                    "fileType": document.content_type,
                    "fileSize": upload_result["bytes"],
                    "path": upload_result["secure_url"],
                    "public_id": upload_result["public_id"],
                    "resource_type": upload_result["resource_type"],
                    "documentType": documentType,
                    "description": description
                }
                
                # Add format if available
                if "format" in upload_result:
                    document_data["format"] = upload_result["format"]
                
                logger.info(f"Creating document record with data: {document_data}")
                
                # Create DocumentCreate model
                document_create = DocumentCreate(**document_data)
                
                result = await DocumentModel.create_document(document_create, user_id)
                
                if result.get("success"):
                    doc = await DocumentModel.find_by_id(result.get("document_id"))
                    formatted_doc = DocumentModel.format_document_response(doc)
                    uploaded_docs.append(formatted_doc)
                    logger.info(f"Document record created successfully: {result.get('document_id')}")
                else:
                    # If document record creation fails, delete the file from Cloudinary
                    delete_result = delete_file(upload_result["public_id"], upload_result["resource_type"])
                    logger.error(f"Failed to create document record. Cloudinary cleanup result: {delete_result}")
                    raise HTTPException(
                        status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                        detail=f"Failed to create document record for {document.filename}"
                    )
            except Exception as e:
                # If creating document record fails, delete the file from Cloudinary
                delete_result = delete_file(upload_result["public_id"], upload_result["resource_type"])
                logger.error(f"Exception creating document record: {str(e)}. Cloudinary cleanup result: {delete_result}")
                raise HTTPException(
                    status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                    detail=f"Failed to create document record for {document.filename}: {str(e)}"
                )
        
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
    except Exception as e:
        logger.error(f"Error in upload_documents: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to upload documents: {str(e)}"
        )

@router.get("/", response_model=Dict)
async def get_documents(
    vehicleId: str = Query(...),
    current_user: Dict = Depends(get_current_user)
):
    """
    Get documents for a vehicle
    """
    try:
        documents = await DocumentModel.find_by_vehicle_id(vehicleId)
        
        return {
            "success": True,
            "total": len(documents),
            "documents": [DocumentModel.format_document_response(doc) for doc in documents]
        }
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to retrieve documents: {str(e)}"
        )

@router.get("/{document_id}", response_model=Dict)
async def get_document(
    document_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Get a specific document by ID
    """
    if not ObjectId.is_valid(document_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid document ID format"
        )
    
    document = await DocumentModel.find_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    return {"document": DocumentModel.format_document_response(document)}

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
    document = await DocumentModel.find_by_id(document_id)
    
    if not document:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Document not found"
        )
    
    # Delete document from database
    result = await DocumentModel.delete_document(document_id)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=result.get("error", "Failed to delete document")
        )
    
    # Delete file from Cloudinary if public_id exists
    if document.get("public_id"):
        delete_result = delete_file(
            document["public_id"], 
            document.get("resource_type", "auto")
        )
        if not delete_result["success"]:
            print(f"Warning: Failed to delete file from Cloudinary: {delete_result.get('error')}")
    
    return {"success": True, "document_id": document_id} 