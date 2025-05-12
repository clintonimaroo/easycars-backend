from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Dict, List, Optional
from bson import ObjectId

from app.models.notification import NotificationModel, NotificationCreate, NotificationUpdate, NotificationResponse
from app.routes.auth import get_current_user

# Create router
router = APIRouter()

@router.get("/", response_model=Dict)
async def get_notifications(
    read: Optional[bool] = None,
    current_user: Dict = Depends(get_current_user)
):
    """
    Get notifications for the current user
    """
    user_id = str(current_user.get("_id"))
    
    notifications = await NotificationModel.get_user_notifications(user_id, read)
    
    return {
        "total": len(notifications),
        "notifications": [NotificationModel.format_notification_response(n) for n in notifications]
    }

@router.get("/{notification_id}", response_model=Dict)
async def get_notification(
    notification_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Get a specific notification by ID
    """
    user_id = str(current_user.get("_id"))
    
    if not ObjectId.is_valid(notification_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid notification ID format"
        )
    
    notification = await NotificationModel.find_by_id(notification_id)
    
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    
    # Check if the notification belongs to the current user
    if notification.get("user_id") != user_id and current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to view this notification"
        )
    
    return {"notification": NotificationModel.format_notification_response(notification)}

@router.post("/", response_model=Dict)
async def create_notification(
    notification_data: NotificationCreate,
    current_user: Dict = Depends(get_current_user)
):
    """
    Create a new notification (admin only)
    """
    # Check if user is admin
    if current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only admins can create notifications"
        )
    
    result = await NotificationModel.create_notification(notification_data)
    
    return {"success": True, "notification_id": result.get("notification_id")}

@router.put("/{notification_id}/read", response_model=Dict)
async def mark_as_read(
    notification_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Mark a notification as read
    """
    user_id = str(current_user.get("_id"))
    
    if not ObjectId.is_valid(notification_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid notification ID format"
        )
    
    notification = await NotificationModel.find_by_id(notification_id)
    
    if not notification:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Notification not found"
        )
    
    # Check if the notification belongs to the current user
    if notification.get("user_id") != user_id and current_user.get("role") != "admin":
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Not authorized to update this notification"
        )
    
    update_data = NotificationUpdate(read=True)
    result = await NotificationModel.update_notification(notification_id, update_data)
    
    return {"success": True, "notification_id": result.get("notification_id")}

@router.put("/read-all", response_model=Dict)
async def mark_all_as_read(current_user: Dict = Depends(get_current_user)):
    """
    Mark all notifications as read for the current user
    """
    user_id = str(current_user.get("_id"))
    
    result = await NotificationModel.mark_all_as_read(user_id)
    
    return {"success": True, "count": result.get("count")} 