from fastapi import APIRouter, Depends, HTTPException, status
from typing import Dict, List
from bson import ObjectId

from app.models.user import UserModel, UserResponse
from app.routes.auth import get_current_user

# Create router
router = APIRouter()

@router.get("/me", response_model=Dict)
async def get_my_profile(current_user: Dict = Depends(get_current_user)):
    """
    Get the current user's profile information
    """
    return {"user": UserModel.format_user_response(current_user)}

@router.get("/{user_id}", response_model=Dict)
async def get_user(
    user_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Get user information by ID
    """
    # Check if the user has permission to view other users
    if current_user.get("role") != "admin" and str(current_user.get("_id")) != user_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this user"
        )
    
    if not ObjectId.is_valid(user_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid user ID format"
        )
    
    user = await UserModel.find_by_id(user_id)
    
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="User not found"
        )
    
    return {"user": UserModel.format_user_response(user)} 