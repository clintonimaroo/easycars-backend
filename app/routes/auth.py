from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import Dict, Optional
from datetime import timedelta
import logging

from app.models.user import UserModel, UserCreate, UserResponse
from app.utils.auth import create_access_token, decode_token

# Create router
router = APIRouter()

# Configure logging
logger = logging.getLogger(__name__)

# OAuth2 password bearer scheme with auto error disabled to handle cookies as backup
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login", auto_error=False)

# Dependency to get current user
async def get_current_user(request: Request, token: Optional[str] = Depends(oauth2_scheme)):
    """
    Get the current authenticated user from token in header or cookie
    """
    logger.info(f"Getting current user. Token in header: {bool(token)}")
    
    # If no token in header, try to get from cookie
    if not token:
        logger.info("No token in header, checking cookies")
        token = request.cookies.get("auth_token")
        logger.info(f"Token in cookie: {bool(token)}")

    # If still no token, authentication fails
    if not token:
        logger.warning("No authentication token found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Authentication required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Decode and validate token
    token_data = decode_token(token)
    if not token_data:
        logger.warning("Invalid token")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    # Get user from database
    user_id = token_data.get("id")
    logger.info(f"Token valid for user ID: {user_id}")
    user = await UserModel.find_by_id(user_id)
    
    if not user:
        logger.warning(f"User not found for ID: {user_id}")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    logger.info(f"User authenticated: {user.get('username')}")
    return user

@router.post("/register", response_model=Dict)
async def register(user_data: UserCreate):
    """
    Register a new user
    """
    result = await UserModel.create_user(user_data)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Registration failed")
        )
    
    return {"success": True, "user_id": result.get("user_id")}

@router.post("/login")
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    """
    Authenticate a user and return a JWT token
    """
    print("Login endpoint hit")
    user = await UserModel.find_by_username_or_email(form_data.username)
    print("User found:", user)
    if not user:
        print("User not found")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    is_valid = await UserModel.verify_password(form_data.password, user.get("password"))
    print("Password valid:", is_valid)
    if not is_valid:
        print("Invalid password")
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )
    # Create JWT token
    token_data = {
        "id": str(user.get("_id")),
        "username": user.get("username"),
        "role": user.get("role")
    }
    access_token = create_access_token(token_data)
    print("Access token created:", access_token)
    response = {
        "access_token": access_token,
        "token_type": "bearer",
        "user": UserModel.format_user_response(user)
    }
    print("Response:", response)
    return response

@router.get("/verify", response_model=Dict)
async def verify_token(token: str):
    """
    Verify a token's validity
    """
    token_data = decode_token(token)
    
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    return {"valid": True, "user_id": token_data.get("id")}

@router.get("/me", response_model=Dict)
async def get_current_user_info(current_user: Dict = Depends(get_current_user)):
    """
    Get information about the current authenticated user
    """
    return {"user": UserModel.format_user_response(current_user)} 
