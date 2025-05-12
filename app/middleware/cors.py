from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from app.config.env import settings

def setup_cors_middleware(app: FastAPI) -> None:
    """
    Setup CORS middleware for the application
    
    Args:
        app: FastAPI application instance
    """
    # For production, you might want to restrict this to only your frontend domain
    origins = [
        settings.FRONTEND_URL,  # Frontend URL from settings
        "http://localhost:3000",  # Next.js default
        "http://localhost:3001",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:3001",
    ]
    
    # Add CORS middleware
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["Content-Disposition"],  # For file downloads
    ) 