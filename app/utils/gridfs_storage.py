import logging
import uuid
import io
from typing import Dict, Optional, BinaryIO, Union
from bson import ObjectId
from gridfs import GridFSBucket
from motor.motor_asyncio import AsyncIOMotorGridFSBucket

from app.utils.mongodb import get_async_database, get_sync_database
from app.config.env import settings

# Set up logging
logger = logging.getLogger(__name__)

async def get_gridfs_bucket():
    """
    Get a GridFS bucket for the database
    
    Returns:
        AsyncIOMotorGridFSBucket: GridFS bucket instance
    """
    db = await get_async_database()
    return AsyncIOMotorGridFSBucket(db)

def get_sync_gridfs_bucket():
    """
    Get a synchronous GridFS bucket for the database
    
    Returns:
        GridFSBucket: GridFS bucket instance
    """
    db = get_sync_database()
    return GridFSBucket(db)

async def upload_file_to_gridfs(
    file: Union[BinaryIO, bytes],
    filename: str,
    content_type: str,
    metadata: Optional[Dict] = None
) -> Dict:
    """
    Upload a file to GridFS
    
    Args:
        file: File-like object or bytes
        filename: Original filename
        content_type: File content type
        metadata: Additional metadata for the file
        
    Returns:
        Dict with upload result
    """
    try:
        # Get GridFS bucket
        fs = await get_gridfs_bucket()
        
        # Prepare metadata
        file_metadata = {
            "contentType": content_type,
            "filename": filename
        }
        
        # Add additional metadata if provided
        if metadata:
            file_metadata.update(metadata)
        
        # Convert to bytes if it's not already
        if isinstance(file, bytes):
            file_bytes = file
        else:
            # Read file-like object
            file_bytes = file.read() if hasattr(file, 'read') else file
            
        # Generate unique filename
        unique_filename = f"{uuid.uuid4()}_{filename}"
        
        # Upload to GridFS
        file_id = await fs.upload_from_stream(
            unique_filename,
            io.BytesIO(file_bytes),
            metadata=file_metadata
        )
        
        file_size = len(file_bytes)
        # Get API URL with fallback
        api_url = getattr(settings, 'API_URL', 'http://localhost:8000')
        
        # Construct URL for accessing the file
        file_url = f"{api_url}/documents/download/{file_id}"
        
        return {
            "success": True,
            "file_id": str(file_id),
            "filename": unique_filename,
            "url": file_url,
            "size": file_size,
            "content_type": content_type
        }
    except Exception as e:
        logger.error("GridFS upload failed (%s)", type(e).__name__)
        return {
            "success": False,
            "error": str(e)
        }

async def get_file_from_gridfs(file_id: str) -> Optional[Dict]:
    """
    Get a file from GridFS by ID
    
    Args:
        file_id: GridFS file ID
        
    Returns:
        Optional[Dict]: File data or None if not found
    """
    try:
        logger.info(f"Retrieving file from GridFS. ID: {file_id}")
        
        # Get GridFS bucket
        fs = await get_gridfs_bucket()
        
        # Convert string ID to ObjectId
        file_id_obj = ObjectId(file_id)
        
        # Get file info
        grid_out = await fs.open_download_stream(file_id_obj)
        
        # Read file content
        content = await grid_out.read()
        
        # Get file metadata
        metadata = grid_out.metadata or {}
        content_type = metadata.get("contentType", "application/octet-stream")
        original_filename = metadata.get("filename", grid_out.filename)
        
        return {
            "success": True,
            "file_id": str(file_id_obj),
            "content": content,
            "filename": grid_out.filename,
            "original_filename": original_filename,
            "content_type": content_type,
            "length": grid_out.length,
            "metadata": metadata
        }
    except Exception as e:
        logger.error(f"Error retrieving file from GridFS. ID: {file_id}, Error: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }

async def delete_file_from_gridfs(file_id: str) -> Dict:
    """
    Delete a file from GridFS
    
    Args:
        file_id: GridFS file ID
        
    Returns:
        Dict: Deletion result
    """
    try:
        logger.info(f"Deleting file from GridFS. ID: {file_id}")
        
        # Get GridFS bucket
        fs = await get_gridfs_bucket()
        
        # Convert string ID to ObjectId
        file_id_obj = ObjectId(file_id)
        
        # Delete file
        await fs.delete(file_id_obj)
        
        logger.info(f"Successfully deleted file from GridFS. ID: {file_id}")
        
        return {
            "success": True,
            "file_id": file_id
        }
    except Exception as e:
        logger.error(f"Error deleting file from GridFS. ID: {file_id}, Error: {str(e)}", exc_info=True)
        return {
            "success": False,
            "error": str(e)
        }
