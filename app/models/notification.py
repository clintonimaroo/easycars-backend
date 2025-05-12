from typing import Dict, List, Optional, Union
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from bson import ObjectId
from app.utils.mongodb import get_async_database, get_sync_database
from app.models.user import PyObjectId

class NotificationType(str, Enum):
    """Notification type enum"""
    INFO = "info"
    SUCCESS = "success"
    WARNING = "warning"
    ERROR = "error"

class NotificationBase(BaseModel):
    """Base model for Notification data"""
    user_id: str
    title: str
    message: str
    type: NotificationType
    link: Optional[str] = None
    read: bool = False

class NotificationCreate(NotificationBase):
    """Model for notification creation"""
    pass

class NotificationUpdate(BaseModel):
    """Model for notification updates"""
    read: Optional[bool] = None
    
class NotificationInDB(NotificationBase):
    """Model for notification in database"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class NotificationResponse(NotificationBase):
    """Notification model for responses"""
    id: str
    created_at: datetime
    
    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class NotificationModel:
    """Notification model for database operations"""
    collection_name = "notifications"
    
    @classmethod
    async def get_collection(cls):
        """Get the notifications collection from the database"""
        db = await get_async_database()
        return db[cls.collection_name]
    
    @classmethod
    def get_sync_collection(cls):
        """Get the notifications collection from the database (synchronous)"""
        db = get_sync_database()
        return db[cls.collection_name]
    
    @classmethod
    async def find_by_id(cls, notification_id: str) -> Optional[Dict]:
        """Find a notification by ID"""
        collection = await cls.get_collection()
        return await collection.find_one({"_id": ObjectId(notification_id)})
    
    @classmethod
    async def create_notification(cls, notification_data: NotificationCreate) -> Dict:
        """Create a new notification"""
        collection = await cls.get_collection()
        
        # Prepare notification document
        notification_doc = {
            **notification_data.dict(),
            "created_at": datetime.utcnow()
        }
        
        # Insert notification
        result = await collection.insert_one(notification_doc)
        
        return {"success": True, "notification_id": str(result.inserted_id)}
    
    @classmethod
    async def update_notification(cls, notification_id: str, notification_data: NotificationUpdate) -> Dict:
        """Update a notification"""
        collection = await cls.get_collection()
        
        # Check if notification exists
        notification = await cls.find_by_id(notification_id)
        if not notification:
            return {"success": False, "error": "Notification not found"}
        
        # Prepare update document
        update_data = {k: v for k, v in notification_data.dict().items() if v is not None}
        
        # Update notification
        await collection.update_one(
            {"_id": ObjectId(notification_id)},
            {"$set": update_data}
        )
        
        return {"success": True, "notification_id": notification_id}
    
    @classmethod
    async def delete_notification(cls, notification_id: str) -> Dict:
        """Delete a notification"""
        collection = await cls.get_collection()
        
        # Check if notification exists
        notification = await cls.find_by_id(notification_id)
        if not notification:
            return {"success": False, "error": "Notification not found"}
        
        # Delete notification
        await collection.delete_one({"_id": ObjectId(notification_id)})
        
        return {"success": True, "notification_id": notification_id}
    
    @classmethod
    async def get_user_notifications(cls, user_id: str, read: Optional[bool] = None) -> List[Dict]:
        """Get notifications for a user"""
        collection = await cls.get_collection()
        
        # Build query
        query = {"user_id": user_id}
        if read is not None:
            query["read"] = read
        
        # Execute query
        cursor = collection.find(query).sort("created_at", -1)
        return await cursor.to_list(length=100)
    
    @classmethod
    async def mark_all_as_read(cls, user_id: str) -> Dict:
        """Mark all notifications as read for a user"""
        collection = await cls.get_collection()
        
        result = await collection.update_many(
            {"user_id": user_id, "read": False},
            {"$set": {"read": True}}
        )
        
        return {
            "success": True,
            "count": result.modified_count
        }
    
    @classmethod
    def format_notification_response(cls, notification: Dict) -> Dict:
        """Format notification data for response"""
        if not notification:
            return None
            
        return {
            "id": str(notification.get("_id")),
            "user_id": notification.get("user_id"),
            "title": notification.get("title"),
            "message": notification.get("message"),
            "type": notification.get("type"),
            "link": notification.get("link"),
            "read": notification.get("read", False),
            "created_at": notification.get("created_at")
        } 