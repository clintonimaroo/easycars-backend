import logging
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
    "connectTimeoutMS": 5000,
    "socketTimeoutMS": 10000,
    "serverSelectionTimeoutMS": 5000,
    "maxPoolSize": 10,
    "minPoolSize": 0,
    "maxIdleTimeMS": 30000,
    "retryWrites": True,
    "retryReads": True,
}

# Synchronous client (for scripts and non-async operations)
_sync_client: Optional[MongoClient] = None

# Asynchronous client (for FastAPI endpoints)
_async_client: Optional[AsyncIOMotorClient] = None

def get_sync_client() -> MongoClient:
    """
    Get or create a synchronous MongoDB client
    
    Returns:
        MongoClient: MongoDB client instance
    """
    global _sync_client
    
    if _sync_client is None:
        try:
            logger.info("Creating new synchronous MongoDB connection")
            _sync_client = MongoClient(MONGODB_URI, **connection_options)
            # Test connection with ping
            _sync_client.admin.command('ping')
            logger.info("MongoDB connection successfully established")
        except Exception as e:
            logger.error(f"Error connecting to MongoDB: {e}")
            raise
    
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
    Get or create an asynchronous MongoDB client
    
    Returns:
        AsyncIOMotorClient: Async MongoDB client instance
    """
    global _async_client
    
    if _async_client is None:
        try:
            logger.info("Creating new asynchronous MongoDB connection")
            _async_client = AsyncIOMotorClient(MONGODB_URI, **connection_options)
            # Test connection with ping
            await _async_client.admin.command('ping')
            logger.info("Async MongoDB connection successfully established")
        except Exception as e:
            logger.error(f"Error connecting to MongoDB: {e}")
            raise
    
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