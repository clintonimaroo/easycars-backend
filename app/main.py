from fastapi import FastAPI
import logging
from app.routes import auth, users, vehicles, notifications, health, customers, documents
from app.middleware.cors import setup_cors_middleware
from app.utils.mongodb import get_async_client, close_async_connection
from fastapi.staticfiles import StaticFiles
import os

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

app = FastAPI(
    title="EasyCars API",
    description="Backend API for EasyCars application",
    version="1.0.0"
)

# Setup middlewares
setup_cors_middleware(app)

# Mount static files directory for uploads
os.makedirs("uploads", exist_ok=True)
app.mount("/uploads", StaticFiles(directory="uploads"), name="uploads")

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(vehicles.router, prefix="/inventory", tags=["Inventory"])
app.include_router(documents.router, prefix="/documents", tags=["Documents"])  # Main documents endpoint
app.include_router(documents.router, prefix="/inventory/documents", tags=["Documents"])  # Legacy/compatibility endpoint
app.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])
app.include_router(customers.router, tags=["Customers"])

@app.on_event("startup")
async def startup_db_client():
    """Initialize database connection on startup"""
    try:
        logger.info("Initializing database connection")
        await get_async_client()
        logger.info("Database connection initialized successfully")
    except Exception as e:
        logger.error(f"Failed to initialize database connection: {e}")
        # We don't raise the exception here to allow the app to start
        # Individual endpoints will handle database errors

@app.on_event("shutdown")
async def shutdown_db_client():
    """Close database connection on shutdown"""
    logger.info("Closing database connection")
    await close_async_connection()

@app.get("/", tags=["Root"])
async def read_root():
    return {"message": "Welcome to EasyCars API"} 