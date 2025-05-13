from typing import Dict, List, Optional, Union, Any
from datetime import datetime
from pydantic import BaseModel, Field, EmailStr
from bson import ObjectId
from app.utils.mongodb import get_async_database, get_sync_database
from app.models.user import PyObjectId

class CustomerBase(BaseModel):
    """Base model for Customer data"""
    name: str
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipCode: Optional[str] = None
    notes: Optional[str] = None
    
    # Meta information
    createdAt: Optional[datetime] = Field(default_factory=datetime.now)
    updatedAt: Optional[datetime] = Field(default_factory=datetime.now)
    
    # References
    vehicles: List[str] = []  # List of vehicle IDs associated with this customer
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_schema_extra = {
            "example": {
                "name": "John Doe",
                "email": "john.doe@example.com",
                "phone": "555-123-4567",
                "address": "123 Main St",
                "city": "Anytown",
                "state": "CA",
                "zipCode": "90210",
                "notes": "Prefers to be contacted by email",
                "vehicles": []
            }
        }

class CustomerCreate(CustomerBase):
    """Customer creation model"""
    pass

class CustomerUpdate(BaseModel):
    """Customer update model with all fields optional"""
    name: Optional[str] = None
    email: Optional[str] = None
    phone: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    state: Optional[str] = None
    zipCode: Optional[str] = None
    notes: Optional[str] = None
    vehicles: Optional[List[str]] = None
    updatedAt: datetime = Field(default_factory=datetime.now)
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True

class CustomerInDB(CustomerBase):
    """Customer model as stored in the database"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}
        
class CustomerResponse(CustomerBase):
    """Customer model for API responses"""
    id: str
    
    class Config:
        populate_by_name = True
        
# Database Collection
COLLECTION_NAME = "customers"

async def get_customer_collection():
    """Get the customers collection from the database"""
    db = await get_async_database()
    return db[COLLECTION_NAME]

def format_customer_response(customer: Dict) -> Dict:
    """Format the customer document for API responses"""
    if not customer:
        return None
        
    # Convert _id to string
    if "_id" in customer:
        customer["id"] = str(customer["_id"])
        del customer["_id"]
        
    return customer
