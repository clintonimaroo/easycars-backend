import logging
import time
import ssl
from typing import Optional
from pymongo import MongoClient
from pymongo.database import Database
from motor.motor_asyncio import AsyncIOMotorClient

from app.config.env import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get MongoDB connection string from settings
MONGODB_URI = settings.MONGODB_URI
if not MONGODB_URI:
    logger.error("No MONGODB_URI found in settings")
    raise ValueError("Please add your MongoDB URI to settings or .env file")

# Get database name from settings
DB_NAME = settings.DATABASE_NAME

# MongoDB connection options optimized for both sync and async drivers
connection_options = {
    "connectTimeoutMS": 30000,  # Increased timeout
    "socketTimeoutMS": 45000,   # Increased timeout
    "serverSelectionTimeoutMS": 30000,  # Increased timeout
    "maxPoolSize": 10,
    "minPoolSize": 0,
    "maxIdleTimeMS": 30000,
    "retryWrites": True,
    "retryReads": True,
    "tls": True,  # Use tls instead of ssl
    "tlsAllowInvalidCertificates": True,  # For less strict cert validation
}

# Synchronous client (for scripts and non-async operations)
_sync_client: Optional[MongoClient] = None

# Asynchronous client (for FastAPI endpoints)
_async_client: Optional[AsyncIOMotorClient] = None

def get_sync_client() -> MongoClient:
    """
    Get or create a synchronous MongoDB client with retry logic
    
    Returns:
        MongoClient: MongoDB client instance
    """
    global _sync_client
    
    if _sync_client is None:
        max_retries = 5
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Creating new synchronous MongoDB connection (attempt {attempt+1}/{max_retries})")
                _sync_client = MongoClient(MONGODB_URI, **connection_options)
                # Test connection with ping
                _sync_client.admin.command('ping')
                logger.info("MongoDB connection successfully established")
                break
            except Exception as e:
                logger.error(f"Error connecting to MongoDB (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.critical("All connection attempts failed")
                    # Return a dummy client for graceful degradation
                    from pymongo.errors import ServerSelectionTimeoutError
                    raise ServerSelectionTimeoutError("Could not connect to MongoDB after multiple attempts")
    
    return _sync_client

def get_sync_database() -> Database:
    """
    Get a synchronous MongoDB database instance
    
    Returns:
        Database: MongoDB database instance
    """
    client = get_sync_client()
    return client[DB_NAME]

async def get_async_client() -> AsyncIOMotorClient:
    """
    Get or create an asynchronous MongoDB client with retry logic
    
    Returns:
        AsyncIOMotorClient: Async MongoDB client instance
    """
    global _async_client
    
    if _async_client is None:
        max_retries = 5
        retry_delay = 2  # seconds
        
        for attempt in range(max_retries):
            try:
                logger.info(f"Creating new asynchronous MongoDB connection (attempt {attempt+1}/{max_retries})")
                _async_client = AsyncIOMotorClient(MONGODB_URI, **connection_options)
                # Test connection with ping
                await _async_client.admin.command('ping')
                logger.info("Async MongoDB connection successfully established")
                break
            except Exception as e:
                logger.error(f"Error connecting to MongoDB (attempt {attempt+1}/{max_retries}): {e}")
                if attempt < max_retries - 1:
                    logger.info(f"Retrying in {retry_delay} seconds...")
                    time.sleep(retry_delay)
                    retry_delay *= 2  # Exponential backoff
                else:
                    logger.critical("All async connection attempts failed")
                    # Log the error but still return a client to avoid app crashes
                    # The application will show appropriate errors when database operations fail
    
    return _async_client

async def get_async_database():
    """
    Get an asynchronous MongoDB database instance
    
    Returns:
        Database: Async MongoDB database instance
    """
    client = await get_async_client()
    return client[DB_NAME]

async def close_async_connection():
    """Close the async MongoDB connection"""
    global _async_client
    
    if _async_client:
        logger.info("Closing async MongoDB connection")
        _async_client.close()
        _async_client = None

def close_sync_connection():
    """Close the sync MongoDB connection"""
    global _sync_client
    
    if _sync_client:
        logger.info("Closing sync MongoDB connection")
        _sync_client.close()
        _sync_client = None 