from fastapi import APIRouter, Depends, HTTPException, status, Query, Body
from typing import List, Dict, Optional
from pymongo import ReturnDocument
from bson import ObjectId
from bson.errors import InvalidId
from datetime import datetime

from app.models.customer import (
    CustomerBase, CustomerCreate, CustomerUpdate, CustomerInDB, CustomerResponse,
    format_customer_response, get_customer_collection
)
from app.models.vehicle import VehicleModel
from app.routes.auth import get_current_user

router = APIRouter(
    prefix="/customers",
    tags=["customers"],
    dependencies=[Depends(get_current_user)]
)

@router.get("", response_description="List all customers")
async def list_customers(
    search: Optional[str] = Query(None, description="Search term for customer name, email, or phone"),
    skip: int = Query(0, ge=0, description="Skip this many results"),
    limit: int = Query(100, ge=1, le=1000, description="Return this many results")
):
    """
    List all customers with optional filtering.
    """
    customers_collection = await get_customer_collection()
    query = {}
    
    if search:
        # Support searching by name, email, or phone
        query = {
            "$or": [
                {"name": {"$regex": search, "$options": "i"}},
                {"email": {"$regex": search, "$options": "i"}},
                {"phone": {"$regex": search, "$options": "i"}}
            ]
        }
    
    cursor = customers_collection.find(query).skip(skip).limit(limit)
    customers = await cursor.to_list(length=limit)
    
    return {
        "success": True,
        "customers": [format_customer_response(customer) for customer in customers]
    }

@router.post("", response_description="Create a new customer")
async def create_customer(customer: CustomerCreate = Body(...)):
    """
    Create a new customer.
    """
    customers_collection = await get_customer_collection()
    
    # Check if customer already exists with same name and email/phone
    existing_query = {"$or": []}
    
    if customer.email:
        existing_query["$or"].append({"email": customer.email})
    
    if customer.phone:
        existing_query["$or"].append({"phone": customer.phone})
    
    if existing_query["$or"]:
        existing = await customers_collection.find_one(existing_query)
        if existing:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail="A customer with this email or phone already exists"
            )
    
    customer_dict = customer.model_dump(by_alias=True)
    customer_dict["vehicles"] = []  # Initialize with empty list
    
    result = await customers_collection.insert_one(customer_dict)
    
    new_customer = await customers_collection.find_one({"_id": result.inserted_id})
    
    return {
        "success": True,
        "customer_id": str(result.inserted_id),
        "customer": format_customer_response(new_customer)
    }

@router.get("/{customer_id}", response_description="Get a customer by ID")
async def get_customer(customer_id: str):
    """
    Get a specific customer by ID.
    """
    try:
        customers_collection = await get_customer_collection()
        customer = await customers_collection.find_one({"_id": ObjectId(customer_id)})
        
        if customer is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Customer with ID {customer_id} not found"
            )
        
        return {
            "success": True,
            "customer": format_customer_response(customer)
        }
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format: {customer_id}"
        )

@router.put("/{customer_id}", response_description="Update a customer")
async def update_customer(customer_id: str, update_data: CustomerUpdate = Body(...)):
    """
    Update a customer's information.
    """
    try:
        customers_collection = await get_customer_collection()
        
        # Check if customer exists
        existing = await customers_collection.find_one({"_id": ObjectId(customer_id)})
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Customer with ID {customer_id} not found"
            )
        
        # Filter out None values from update_data
        update_dict = {
            k: v for k, v in update_data.model_dump(by_alias=True).items() 
            if v is not None
        }
        
        if not update_dict:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="No valid fields to update"
            )
        
        # Check for email/phone uniqueness if being updated
        if "email" in update_dict or "phone" in update_dict:
            existing_query = {"_id": {"$ne": ObjectId(customer_id)}, "$or": []}
            
            if "email" in update_dict and update_dict["email"]:
                existing_query["$or"].append({"email": update_dict["email"]})
                
            if "phone" in update_dict and update_dict["phone"]:
                existing_query["$or"].append({"phone": update_dict["phone"]})
                
            if existing_query["$or"]:
                existing = await customers_collection.find_one(existing_query)
                if existing:
                    raise HTTPException(
                        status_code=status.HTTP_409_CONFLICT,
                        detail="Another customer with this email or phone already exists"
                    )
        
        # Update the customer
        result = await customers_collection.find_one_and_update(
            {"_id": ObjectId(customer_id)},
            {"$set": update_dict},
            return_document=ReturnDocument.AFTER
        )
        
        return {
            "success": True,
            "customer": format_customer_response(result)
        }
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format: {customer_id}"
        )

@router.delete("/{customer_id}", response_description="Delete a customer")
async def delete_customer(customer_id: str):
    """
    Delete a customer.
    """
    try:
        customers_collection = await get_customer_collection()
        
        # Check if customer exists
        customer = await customers_collection.find_one({"_id": ObjectId(customer_id)})
        if customer is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Customer with ID {customer_id} not found"
            )
        
        # Delete the customer
        result = await customers_collection.delete_one({"_id": ObjectId(customer_id)})
        
        if result.deleted_count == 1:
            return {"success": True, "message": "Customer deleted successfully"}
        else:
            raise HTTPException(
                status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
                detail="Failed to delete customer"
            )
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format: {customer_id}"
        )

@router.post("/extract-from-vehicle/{vehicle_id}", response_description="Extract customer from vehicle")
async def extract_customer_from_vehicle(vehicle_id: str):
    """
    Extract customer data from a vehicle and create a new customer record.
    """
    try:
        vehicles_collection = await VehicleModel.get_collection()
        customers_collection = await get_customer_collection()
        
        # Find the vehicle
        vehicle = await vehicles_collection.find_one({"_id": ObjectId(vehicle_id)})
        if vehicle is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Vehicle with ID {vehicle_id} not found"
            )
        
        # Extract customer data
        customer_data = {}
        
        # Required fields
        if not vehicle.get("soldTo"):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="Vehicle does not have customer information (missing name/soldTo field)"
            )
        
        customer_data["name"] = vehicle.get("soldTo")
        
        # Optional fields
        if vehicle.get("buyerAddress"):
            customer_data["address"] = vehicle.get("buyerAddress")
        
        if vehicle.get("buyerCity"):
            customer_data["city"] = vehicle.get("buyerCity")
            
        if vehicle.get("buyerState"):
            customer_data["state"] = vehicle.get("buyerState")
            
        if vehicle.get("buyerZip"):
            customer_data["zipCode"] = vehicle.get("buyerZip")
            
        if vehicle.get("buyerPhone"):
            customer_data["phone"] = vehicle.get("buyerPhone")
        
        # Check if customer already exists with same name and address
        existing_query = {"name": customer_data["name"]}
        
        if "phone" in customer_data and customer_data["phone"]:
            existing_query["phone"] = customer_data["phone"]
        
        existing = await customers_collection.find_one(existing_query)
        
        if existing:
            # Update existing customer with vehicle reference
            result = await customers_collection.find_one_and_update(
                {"_id": existing["_id"]},
                {
                    "$addToSet": {"vehicles": str(vehicle["_id"])},
                    "$set": {"updatedAt": datetime.now()}
                },
                return_document=ReturnDocument.AFTER
            )
            
            return {
                "success": True,
                "message": "Customer already exists, added vehicle reference",
                "customer": format_customer_response(result)
            }
        
        # Create new customer
        customer_data["vehicles"] = [str(vehicle["_id"])]
        
        result = await customers_collection.insert_one(customer_data)
        
        new_customer = await customers_collection.find_one({"_id": result.inserted_id})
        
        return {
            "success": True,
            "message": "Customer extracted and created successfully",
            "customer_id": str(result.inserted_id),
            "customer": format_customer_response(new_customer)
        }
    except InvalidId:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=f"Invalid ID format: {vehicle_id}"
        )
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"Failed to extract customer: {str(e)}"
        ) 