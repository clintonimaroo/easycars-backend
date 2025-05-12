from typing import Dict, List, Optional, Union
from datetime import datetime
from pydantic import BaseModel, EmailStr, Field
import bcrypt
from bson import ObjectId
from app.utils.mongodb import get_async_database, get_sync_database

class PyObjectId(ObjectId):
    """Custom ObjectId class for Pydantic models"""
    @classmethod
    def __get_validators__(cls):
        yield cls.validate
        
    @classmethod
    def validate(cls, v):
        if not ObjectId.is_valid(v):
            raise ValueError("Invalid ObjectId")
        return ObjectId(v)
        
    @classmethod
    def __get_pydantic_json_schema__(cls, _schema_generator, _field_schema):
        return {"type": "string"}

class UserBase(BaseModel):
    """Base model for User data"""
    username: str
    email: EmailStr
    org_id: Optional[str] = None
    role: str = "user"

class UserCreate(UserBase):
    """Model for user creation with password"""
    password: str

class UserInDB(UserBase):
    """Model for user in database"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    password: str
    created_at: datetime = Field(default_factory=datetime.utcnow)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class UserResponse(UserBase):
    """User model for responses (no sensitive data)"""
    id: str
    created_at: datetime
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class UserModel:
    """User model for database operations"""
    collection_name = "users"
    
    @classmethod
    async def get_collection(cls):
        """Get the users collection from the database"""
        db = await get_async_database()
        return db[cls.collection_name]
    
    @classmethod
    def get_sync_collection(cls):
        """Get the users collection from the database (synchronous)"""
        db = get_sync_database()
        return db[cls.collection_name]
    
    @classmethod
    async def find_by_id(cls, user_id: str) -> Optional[Dict]:
        """Find a user by ID"""
        collection = await cls.get_collection()
        return await collection.find_one({"_id": ObjectId(user_id)})
    
    @classmethod
    async def find_by_username_or_email(cls, username_or_email: str) -> Optional[Dict]:
        """Find a user by username or email"""
        collection = await cls.get_collection()
        is_email = "@" in username_or_email
        query = {"email": username_or_email.lower()} if is_email else {"username": username_or_email}
        return await collection.find_one(query)
    
    @classmethod
    async def create_user(cls, user_data: UserCreate) -> Dict:
        """Create a new user"""
        collection = await cls.get_collection()
        
        # Check if username already exists
        if await collection.find_one({"username": user_data.username}):
            return {"success": False, "error": "Username already exists"}
        
        # Check if email already exists
        if await collection.find_one({"email": user_data.email.lower()}):
            return {"success": False, "error": "Email already exists"}
        
        # Hash password
        hashed_password = bcrypt.hashpw(user_data.password.encode(), bcrypt.gensalt())
        
        # Prepare user document
        user_doc = {
            "username": user_data.username,
            "email": user_data.email.lower(),
            "password": hashed_password.decode(),
            "org_id": user_data.org_id,
            "role": user_data.role,
            "created_at": datetime.utcnow()
        }
        
        # Insert user
        result = await collection.insert_one(user_doc)
        
        return {"success": True, "user_id": str(result.inserted_id)}
    
    @classmethod
    async def verify_password(cls, plain_password: str, hashed_password: str) -> bool:
        """Verify a password against a hash"""
        return bcrypt.checkpw(plain_password.encode(), hashed_password.encode())
    
    @classmethod
    def format_user_response(cls, user: Dict) -> Dict:
        """Format user data for response (removing sensitive fields)"""
        if not user:
            return None
            
        return {
            "id": str(user.get("_id")),
            "username": user.get("username"),
            "email": user.get("email"),
            "org_id": user.get("org_id"),
            "role": user.get("role", "user"),
            "created_at": user.get("created_at")
        } 