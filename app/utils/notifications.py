import logging
from typing import Optional
from app.models.notification import NotificationModel, NotificationCreate, NotificationType

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

async def create_notification(
    user_id: str,
    title: str,
    message: str,
    type: NotificationType = NotificationType.INFO,
    link: Optional[str] = None
):
    """
    Create a notification for a user
    
    Args:
        user_id: User ID to create notification for
        title: Notification title
        message: Notification message
        type: Notification type (info, success, warning, error)
        link: Optional link for the notification
        
    Returns:
        Dict: Result of notification creation
    """
    try:
        notification_data = NotificationCreate(
            user_id=user_id,
            title=title,
            message=message,
            type=type,
            link=link
        )
        
        result = await NotificationModel.create_notification(notification_data)
        
        logger.info(f"Created {type} notification for user {user_id}: {title}")
        
        return result
    except Exception as e:
        logger.error(f"Error creating notification: {e}")
        return {"success": False, "error": str(e)}

async def create_info_notification(user_id: str, title: str, message: str, link: Optional[str] = None):
    """Create an info notification"""
    return await create_notification(user_id, title, message, NotificationType.INFO, link)

async def create_success_notification(user_id: str, title: str, message: str, link: Optional[str] = None):
    """Create a success notification"""
    return await create_notification(user_id, title, message, NotificationType.SUCCESS, link)

async def create_warning_notification(user_id: str, title: str, message: str, link: Optional[str] = None):
    """Create a warning notification"""
    return await create_notification(user_id, title, message, NotificationType.WARNING, link)

async def create_error_notification(user_id: str, title: str, message: str, link: Optional[str] = None):
    """Create an error notification"""
    return await create_notification(user_id, title, message, NotificationType.ERROR, link) 