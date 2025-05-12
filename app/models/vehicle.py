from typing import Dict, List, Optional, Union, Any
from datetime import datetime
from enum import Enum
from pydantic import BaseModel, Field
from bson import ObjectId
from app.utils.mongodb import get_async_database, get_sync_database
from app.models.user import PyObjectId

class VehicleStatus(str, Enum):
    """Vehicle inventory status"""
    AVAILABLE = "available"
    SOLD = "sold"
    RESERVED = "reserved"
    MAINTENANCE = "maintenance"
    PENDING = "pending"

class VehicleBase(BaseModel):
    """Base model for Vehicle data"""
    make: str
    model: str
    year: int
    vin: str
    price: float
    mileage: int
    color: str
    status: VehicleStatus = VehicleStatus.AVAILABLE
    description: Optional[str] = None
    features: List[str] = []
    images: List[str] = []
    documents: List[Dict] = []

class VehicleCreate(VehicleBase):
    """Model for vehicle creation"""
    pass

class VehicleUpdate(BaseModel):
    """Model for vehicle updates"""
    make: Optional[str] = None
    model: Optional[str] = None
    year: Optional[int] = None
    vin: Optional[str] = None
    price: Optional[float] = None
    mileage: Optional[int] = None
    color: Optional[str] = None
    status: Optional[VehicleStatus] = None
    description: Optional[str] = None
    features: Optional[List[str]] = None
    images: Optional[List[str]] = None
    documents: Optional[List[Dict]] = None

class VehicleInDB(VehicleBase):
    """Model for vehicle in database"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    
    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class VehicleResponse(VehicleBase):
    """Vehicle model for responses"""
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    
    class Config:
        allow_population_by_field_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class VehicleModel:
    """Vehicle model for database operations"""
    collection_name = "vehicles"
    
    @classmethod
    async def get_collection(cls):
        """Get the vehicles collection from the database"""
        db = await get_async_database()
        return db[cls.collection_name]
    
    @classmethod
    def get_sync_collection(cls):
        """Get the vehicles collection from the database (synchronous)"""
        db = get_sync_database()
        return db[cls.collection_name]
    
    @classmethod
    async def find_by_id(cls, vehicle_id: str) -> Optional[Dict]:
        """Find a vehicle by ID"""
        collection = await cls.get_collection()
        return await collection.find_one({"_id": ObjectId(vehicle_id)})
    
    @classmethod
    async def find_by_vin(cls, vin: str) -> Optional[Dict]:
        """Find a vehicle by VIN"""
        collection = await cls.get_collection()
        return await collection.find_one({"vin": vin})
    
    @classmethod
    async def create_vehicle(cls, vehicle_data: VehicleCreate, user_id: str) -> Dict:
        """Create a new vehicle"""
        collection = await cls.get_collection()
        
        # Check if VIN already exists
        if await collection.find_one({"vin": vehicle_data.vin}):
            return {"success": False, "error": "Vehicle with this VIN already exists"}
        
        # Prepare vehicle document
        vehicle_doc = {
            **vehicle_data.dict(),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "created_by": user_id
        }
        
        # Insert vehicle
        result = await collection.insert_one(vehicle_doc)
        
        return {"success": True, "vehicle_id": str(result.inserted_id)}
    
    @classmethod
    async def update_vehicle(cls, vehicle_id: str, vehicle_data: VehicleUpdate) -> Dict:
        """Update a vehicle"""
        collection = await cls.get_collection()
        
        # Check if vehicle exists
        vehicle = await cls.find_by_id(vehicle_id)
        if not vehicle:
            return {"success": False, "error": "Vehicle not found"}
        
        # Prepare update document
        update_data = {k: v for k, v in vehicle_data.dict(exclude_unset=True).items() if v is not None}
        update_data["updated_at"] = datetime.utcnow()
        
        # Update vehicle
        await collection.update_one(
            {"_id": ObjectId(vehicle_id)},
            {"$set": update_data}
        )
        
        return {"success": True, "vehicle_id": vehicle_id}
    
    @classmethod
    async def delete_vehicle(cls, vehicle_id: str) -> Dict:
        """Delete a vehicle"""
        collection = await cls.get_collection()
        
        # Check if vehicle exists
        vehicle = await cls.find_by_id(vehicle_id)
        if not vehicle:
            return {"success": False, "error": "Vehicle not found"}
        
        # Delete vehicle
        await collection.delete_one({"_id": ObjectId(vehicle_id)})
        
        return {"success": True, "vehicle_id": vehicle_id}
    
    @classmethod
    async def get_vehicles(cls, skip: int = 0, limit: int = 100, 
                          status: Optional[str] = None, 
                          make: Optional[str] = None,
                          model: Optional[str] = None,
                          year: Optional[int] = None) -> List[Dict]:
        """Get vehicles with filtering"""
        collection = await cls.get_collection()
        
        # Build query
        query = {}
        if status:
            query["status"] = status
        if make:
            query["make"] = {"$regex": make, "$options": "i"}
        if model:
            query["model"] = {"$regex": model, "$options": "i"}
        if year:
            query["year"] = year
        
        # Execute query
        cursor = collection.find(query).skip(skip).limit(limit).sort("created_at", -1)
        return await cursor.to_list(length=limit)
    
    @classmethod
    def format_vehicle_response(cls, vehicle: Dict) -> Dict:
        """Format vehicle data for response"""
        if not vehicle:
            return None
            
        return {
            "id": str(vehicle.get("_id")),
            "make": vehicle.get("make"),
            "model": vehicle.get("model"),
            "year": vehicle.get("year"),
            "vin": vehicle.get("vin"),
            "price": vehicle.get("price"),
            "mileage": vehicle.get("mileage"),
            "color": vehicle.get("color"),
            "status": vehicle.get("status"),
            "description": vehicle.get("description"),
            "features": vehicle.get("features", []),
            "images": vehicle.get("images", []),
            "documents": vehicle.get("documents", []),
            "created_at": vehicle.get("created_at"),
            "updated_at": vehicle.get("updated_at"),
            "created_by": vehicle.get("created_by")
        }
