from fastapi import FastAPI

from app.routes import auth, users, vehicles, notifications, health
from app.middleware.cors import setup_cors_middleware

app = FastAPI(
    title="EasyCars API",
    description="Backend API for EasyCars application",
    version="1.0.0"
)

# Setup middlewares
setup_cors_middleware(app)

# Include routers
app.include_router(health.router, tags=["Health"])
app.include_router(auth.router, prefix="/auth", tags=["Authentication"])
app.include_router(users.router, prefix="/users", tags=["Users"])
app.include_router(vehicles.router, prefix="/inventory", tags=["Inventory"])
app.include_router(notifications.router, prefix="/notifications", tags=["Notifications"])

@app.get("/", tags=["Root"])
async def read_root():
    return {"message": "Welcome to EasyCars API"} 