import cloudinary
import cloudinary.uploader
from cloudinary.utils import cloudinary_url
from app.config.env import settings
import os
from typing import Dict, Optional, BinaryIO, Union
import uuid
import logging

# Set up logging
logger = logging.getLogger(__name__)

# Configure Cloudinary
try:
    cloudinary.config(
        cloud_name=settings.CLOUDINARY_CLOUD_NAME,
        api_key=settings.CLOUDINARY_API_KEY,
        api_secret=settings.CLOUDINARY_API_SECRET,
        secure=True
    )
    logger.info(f"Cloudinary configured with cloud_name: {settings.CLOUDINARY_CLOUD_NAME}")
    
    # Validate configuration
    if not settings.CLOUDINARY_API_SECRET:
        logger.warning("Cloudinary API Secret is empty! Document uploads will fail.")
except Exception as e:
    logger.error(f"Failed to configure Cloudinary: {str(e)}")

async def upload_file(
    file: Union[BinaryIO, bytes],
    filename: str,
    folder: Optional[str] = None,
    resource_type: str = "auto"
) -> Dict:
    """
    Upload a file to Cloudinary
    
    Args:
        file: File-like object or bytes
        filename: Original filename
        folder: Cloudinary folder (defaults to settings.CLOUDINARY_FOLDER)
        resource_type: Resource type (auto, image, video, raw)
        
    Returns:
        Dict with upload result
    """
    if not folder:
        folder = settings.CLOUDINARY_FOLDER
    
    # Generate a unique public_id to avoid conflicts
    file_extension = os.path.splitext(filename)[1]
    public_id = f"{folder}/{uuid.uuid4()}"
    
    logger.info(f"Uploading file {filename} to Cloudinary folder: {folder}")
    
    # Validate Cloudinary configuration
    if not settings.CLOUDINARY_API_SECRET:
        logger.error("Cloudinary API Secret is not configured")
        return {
            "success": False,
            "error": "Cloudinary API Secret is not configured"
        }
    
    # Upload to Cloudinary
    try:
        logger.debug(f"Starting Cloudinary upload for {filename}")
        result = cloudinary.uploader.upload(
            file,
            public_id=public_id,
            resource_type=resource_type,
            unique_filename=True,
            overwrite=False,
            folder=None,  # We already included folder in public_id
        )
        
        logger.info(f"Successfully uploaded {filename} to Cloudinary. Public ID: {result['public_id']}")
        
        return {
            "success": True,
            "public_id": result["public_id"],
            "secure_url": result["secure_url"],
            "format": result.get("format", ""),
            "resource_type": result["resource_type"],
            "bytes": result["bytes"],
            "created_at": result["created_at"],
        }
    except Exception as e:
        logger.error(f"Cloudinary upload failed for {filename}: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }

def delete_file(public_id: str, resource_type: str = "auto") -> Dict:
    """
    Delete a file from Cloudinary
    
    Args:
        public_id: Cloudinary public_id
        resource_type: Resource type (auto, image, video, raw)
        
    Returns:
        Dict with deletion result
    """
    try:
        logger.info(f"Deleting file from Cloudinary. Public ID: {public_id}, Resource Type: {resource_type}")
        result = cloudinary.uploader.destroy(public_id, resource_type=resource_type)
        success = result == "ok"
        
        if success:
            logger.info(f"Successfully deleted file from Cloudinary. Public ID: {public_id}")
        else:
            logger.warning(f"Failed to delete file from Cloudinary. Public ID: {public_id}, Result: {result}")
            
        return {
            "success": success,
            "result": result
        }
    except Exception as e:
        logger.error(f"Error deleting file from Cloudinary. Public ID: {public_id}, Error: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        } 