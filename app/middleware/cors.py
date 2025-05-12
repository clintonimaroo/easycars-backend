from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config.env import settings
import logging

logger = logging.getLogger(__name__)

def setup_cors_middleware(app: FastAPI) -> None:
    """
    Setup CORS middleware for the application
    
    Args:
        app: FastAPI application instance
    """
    # Set up origins from environment and add common development origins
    origins = [
        settings.FRONTEND_URL,  # Frontend URL from settings
        "https://dashboard.easycarsllc.com",  # Production frontend
        "https://www.dashboard.easycarsllc.com",  # With www prefix
        "http://localhost:3000",  # Next.js default
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ]
    
    logger.info(f"Setting up CORS middleware with origins: {origins}")
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=[
            "Content-Disposition",   # For file downloads
            "Authorization",         # For auth token
            "Set-Cookie"             # For cookies
        ],
    ) 