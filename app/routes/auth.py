from fastapi import APIRouter, Depends, HTTPException, status, Request
from fastapi.security import OAuth2PasswordBearer, OAuth2PasswordRequestForm
from typing import Dict, Optional
from datetime import timedelta

from app.models.user import UserModel, UserCreate, UserResponse
from app.utils.auth import create_access_token, decode_token

# Create router
router = APIRouter()

# OAuth2 password bearer scheme
oauth2_scheme = OAuth2PasswordBearer(tokenUrl="/auth/login")

# Dependency to get current user
async def get_current_user(request: Request, token: str = Depends(oauth2_scheme)):
    # Try to get token from header first, then from cookie
    if not token:
        token = request.cookies.get("auth_token")
    token_data = decode_token(token)
    if not token_data:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid authentication credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
    user_id = token_data.get("id")
    user = await UserModel.find_by_id(user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found",
            headers={"WWW-Authenticate": "Bearer"},
        )
    
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