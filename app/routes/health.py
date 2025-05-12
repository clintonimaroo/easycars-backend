from fastapi import APIRouter, status
from typing import Dict
import os
import platform
import time

from app.utils.mongodb import get_async_database

# Create router
router = APIRouter()

@router.get("/health", response_model=Dict)
async def health_check():
    """
    Health check endpoint for monitoring and status
    """
    start_time = time.time()
    
    # Get environment information
    environment = os.getenv("ENV", "development")
    python_version = platform.python_version()
    os_info = f"{platform.system()} {platform.release()}"
    
    # Check database connection
    db_status = "connected"
    collections = []
    db_name = ""
    
    try:
        db = await get_async_database()
        db_name = db.name
        collections_cursor = await db.list_collections()
        collections = [coll["name"] async for coll in collections_cursor]
    except Exception as e:
        db_status = f"error: {str(e)}"
    
    # Calculate response time
    response_time = time.time() - start_time
    
    return {
        "status": "ok",
        "environment": environment,
        "version": "1.0.0",
        "python_version": python_version,
        "os": os_info,
        "response_time_ms": round(response_time * 1000, 2),
        "database": {
            "status": db_status,
            "name": db_name,
            "collections": collections
        }
    }

@router.get("/ping", response_model=Dict)
async def ping():
    """
    Simple ping endpoint
    """
    return {"ping": "pong", "status": "ok"} 