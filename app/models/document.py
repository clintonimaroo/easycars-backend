from typing import Dict, List, Optional, Annotated, Any
from datetime import datetime
from pydantic import BaseModel, Field
from bson import ObjectId
from app.utils.mongodb import get_async_database, get_sync_database

class PyObjectId(ObjectId):
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

class DocumentBase(BaseModel):
    """Base model for Document data"""
    vehicleId: str
    originalFilename: str
    fileType: str
    fileSize: int
    file_id: str  # GridFS file ID
    path: str  # URL path for accessing the file
    documentType: Optional[str] = None  # e.g., "bill_of_sale", "title_certificate", etc.
    description: Optional[str] = None

class DocumentCreate(BaseModel):
    """Model for document creation"""
    vehicleId: str
    originalFilename: str
    fileType: str
    fileSize: int
    file_id: str
    path: str
    documentType: Optional[str] = None
    description: Optional[str] = None

class DocumentInDB(DocumentBase):
    """Model for document in database"""
    id: PyObjectId = Field(default_factory=PyObjectId, alias="_id")
    created_at: datetime = Field(default_factory=datetime.utcnow)
    updated_at: datetime = Field(default_factory=datetime.utcnow)
    created_by: Optional[str] = None
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class DocumentResponse(DocumentBase):
    """Document model for responses"""
    id: str
    created_at: datetime
    updated_at: datetime
    created_by: Optional[str] = None
    
    class Config:
        populate_by_name = True
        arbitrary_types_allowed = True
        json_encoders = {ObjectId: str}

class DocumentModel:
    """Document model for database operations"""
    collection_name = "documents"
    
    @classmethod
    async def get_collection(cls):
        """Get the documents collection from the database"""
        db = await get_async_database()
        return db[cls.collection_name]
    
    @classmethod
    def get_sync_collection(cls):
        """Get the documents collection from the database (synchronous)"""
        db = get_sync_database()
        return db[cls.collection_name]
    
    @classmethod
    async def find_by_id(cls, document_id: str) -> Optional[Dict]:
        """Find a document by ID"""
        collection = await cls.get_collection()
        return await collection.find_one({"_id": ObjectId(document_id)})
    
    @classmethod
    async def find_by_vehicle_id(cls, vehicle_id: str) -> List[Dict]:
        """Find documents by vehicle ID"""
        collection = await cls.get_collection()
        cursor = collection.find({"vehicleId": vehicle_id}).sort("created_at", -1)
        return await cursor.to_list(length=100)
    
    @classmethod
    async def create_document(cls, document_data: DocumentCreate, user_id: str) -> Dict:
        """Create a new document"""
        collection = await cls.get_collection()
        
        # Prepare document
        document_doc = {
            **document_data.model_dump(),
            "created_at": datetime.utcnow(),
            "updated_at": datetime.utcnow(),
            "created_by": user_id
        }
        
        # Insert document
        result = await collection.insert_one(document_doc)
        
        return {"success": True, "document_id": str(result.inserted_id)}
    
    @classmethod
    async def delete_document(cls, document_id: str) -> Dict:
        """Delete a document"""
        collection = await cls.get_collection()
        
        # Check if document exists
        document = await cls.find_by_id(document_id)
        if not document:
            return {"success": False, "error": "Document not found"}
        
        # Delete document
        await collection.delete_one({"_id": ObjectId(document_id)})
        
        return {"success": True, "document_id": document_id}
    
    @classmethod
    def format_document_response(cls, document: Dict) -> Dict:
        """Format document data for response"""
        return {
            "id": str(document["_id"]),
            "vehicleId": document["vehicleId"],
            "originalFilename": document["originalFilename"],
            "fileType": document.get("fileType", ""),
            "fileSize": document.get("fileSize", 0),
            "file_id": document.get("file_id", ""),
            "path": document.get("path", ""),
            "url": document.get("path", ""),
            "documentType": document.get("documentType"),
            "description": document.get("description"),
            "created_at": document["created_at"],
            "updated_at": document["updated_at"],
            "created_by": document.get("created_by")
        } 