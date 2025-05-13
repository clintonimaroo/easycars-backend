from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Dict, List, Optional
from bson import ObjectId
from datetime import datetime
from pymongo import ReturnDocument

from app.models.vehicle import VehicleModel, VehicleCreate, VehicleUpdate, VehicleResponse
from app.models.customer import get_customer_collection, format_customer_response
from app.routes.auth import get_current_user

# Create router
router = APIRouter()

@router.post("/", response_model=Dict)
async def create_vehicle(
    vehicle_data: VehicleCreate,
    current_user: Dict = Depends(get_current_user)
):
    """
    Create a new vehicle in inventory
    """
    user_id = str(current_user.get("_id"))
    
    result = await VehicleModel.create_vehicle(vehicle_data, user_id)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=result.get("error", "Failed to create vehicle")
        )
    
    vehicle_id = result.get("vehicle_id")
    response = {"success": True, "vehicle_id": vehicle_id}
    
    # Automatically extract customer info if customer details are provided
    if vehicle_data.soldTo:
        try:
            # Get collections
            vehicles_collection = await VehicleModel.get_collection()
            customers_collection = await get_customer_collection()
            
            # Find the vehicle we just created
            vehicle = await vehicles_collection.find_one({"_id": ObjectId(vehicle_id)})
            if not vehicle:
                raise HTTPException(
                    status_code=status.HTTP_404_NOT_FOUND,
                    detail=f"Vehicle with ID {vehicle_id} not found"
                )
            
            # Extract customer data
            customer_data = {}
            customer_data["name"] = vehicle_data.soldTo
            
            # Optional fields
            if vehicle_data.buyerAddress:
                customer_data["address"] = vehicle_data.buyerAddress
            
            if vehicle_data.buyerCity:
                customer_data["city"] = vehicle_data.buyerCity
                
            if vehicle_data.buyerState:
                customer_data["state"] = vehicle_data.buyerState
                
            if vehicle_data.buyerZip:
                customer_data["zipCode"] = vehicle_data.buyerZip
                
            if vehicle_data.buyerPhone:
                customer_data["phone"] = vehicle_data.buyerPhone
            
            # Check if customer already exists with same name and address/phone
            existing_query = {"name": customer_data["name"]}
            
            if "phone" in customer_data and customer_data["phone"]:
                existing_query["phone"] = customer_data["phone"]
            
            existing = await customers_collection.find_one(existing_query)
            
            if existing:
                # Update existing customer with vehicle reference
                result = await customers_collection.find_one_and_update(
                    {"_id": existing["_id"]},
                    {
                        "$addToSet": {"vehicles": vehicle_id},
                        "$set": {"updatedAt": datetime.now()}
                    },
                    return_document=ReturnDocument.AFTER
                )
                
                response["customer"] = {
                    "success": True,
                    "message": "Customer already exists, added vehicle reference",
                    "customer": format_customer_response(result)
                }
            else:
                # Create new customer
                customer_data["vehicles"] = [vehicle_id]
                
                result = await customers_collection.insert_one(customer_data)
                
                new_customer = await customers_collection.find_one({"_id": result.inserted_id})
                
                response["customer"] = {
                    "success": True,
                    "message": "Customer extracted and created automatically",
                    "customer_id": str(result.inserted_id),
                    "customer": format_customer_response(new_customer)
                }
        except Exception as e:
            # Don't fail the vehicle creation if customer extraction fails
            response["customer"] = {
                "success": False,
                "message": f"Vehicle created but failed to extract customer data: {str(e)}"
            }
    
    return response

@router.get("/", response_model=Dict)
async def get_vehicles(
    skip: int = Query(0, ge=0),
    limit: int = Query(100, ge=1, le=1000),
    status: Optional[str] = None,
    make: Optional[str] = None,
    model: Optional[str] = None,
    year: Optional[int] = None,
    current_user: Dict = Depends(get_current_user)
):
    """
    Get vehicles with filtering options
    """
    vehicles = await VehicleModel.get_vehicles(
        skip=skip,
        limit=limit,
        status=status,
        make=make,
        model=model,
        year=year
    )
    
    return {
        "total": len(vehicles),
        "skip": skip,
        "limit": limit,
        "vehicles": [VehicleModel.format_vehicle_response(v) for v in vehicles]
    }

@router.get("/{vehicle_id}", response_model=Dict)
async def get_vehicle(
    vehicle_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Get a specific vehicle by ID
    """
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid vehicle ID format"
        )
    
    vehicle = await VehicleModel.find_by_id(vehicle_id)
    
    if not vehicle:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="Vehicle not found"
        )
    
    return {"vehicle": VehicleModel.format_vehicle_response(vehicle)}

@router.put("/{vehicle_id}", response_model=Dict)
async def update_vehicle(
    vehicle_id: str,
    vehicle_data: VehicleUpdate,
    current_user: Dict = Depends(get_current_user)
):
    """
    Update a vehicle in inventory
    """
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid vehicle ID format"
        )
    
    result = await VehicleModel.update_vehicle(vehicle_id, vehicle_data)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("error", "Failed to update vehicle")
        )
    
    # Get the updated vehicle
    updated_vehicle = await VehicleModel.find_by_id(vehicle_id)
    
    response = {
        "success": True,
        "vehicle": VehicleModel.format_vehicle_response(updated_vehicle)
    }
    
    # Check if customer info is provided in the update
    if vehicle_data.soldTo is not None:
        try:
            # Get collections
            customers_collection = await get_customer_collection()
            
            # Extract customer data
            customer_data = {}
            customer_data["name"] = vehicle_data.soldTo
            
            # Optional fields
            if vehicle_data.buyerAddress is not None:
                customer_data["address"] = vehicle_data.buyerAddress
            
            if vehicle_data.buyerCity is not None:
                customer_data["city"] = vehicle_data.buyerCity
                
            if vehicle_data.buyerState is not None:
                customer_data["state"] = vehicle_data.buyerState
                
            if vehicle_data.buyerZip is not None:
                customer_data["zipCode"] = vehicle_data.buyerZip
                
            if vehicle_data.buyerPhone is not None:
                customer_data["phone"] = vehicle_data.buyerPhone
            
            # Check if customer already exists with same name and address/phone
            existing_query = {"name": customer_data["name"]}
            
            if "phone" in customer_data and customer_data["phone"]:
                existing_query["phone"] = customer_data["phone"]
            
            existing = await customers_collection.find_one(existing_query)
            
            if existing:
                # Update existing customer with vehicle reference
                result = await customers_collection.find_one_and_update(
                    {"_id": existing["_id"]},
                    {
                        "$addToSet": {"vehicles": vehicle_id},
                        "$set": {"updatedAt": datetime.now()}
                    },
                    return_document=ReturnDocument.AFTER
                )
                
                response["customer"] = {
                    "success": True,
                    "message": "Customer already exists, added vehicle reference",
                    "customer": format_customer_response(result)
                }
            else:
                # Create new customer
                customer_data["vehicles"] = [vehicle_id]
                
                result = await customers_collection.insert_one(customer_data)
                
                new_customer = await customers_collection.find_one({"_id": result.inserted_id})
                
                response["customer"] = {
                    "success": True,
                    "message": "Customer extracted and created automatically",
                    "customer_id": str(result.inserted_id),
                    "customer": format_customer_response(new_customer)
                }
        except Exception as e:
            # Don't fail the vehicle update if customer extraction fails
            response["customer"] = {
                "success": False,
                "message": f"Vehicle updated but failed to extract customer data: {str(e)}"
            }
    
    return response

@router.delete("/{vehicle_id}", response_model=Dict)
async def delete_vehicle(
    vehicle_id: str,
    current_user: Dict = Depends(get_current_user)
):
    """
    Delete a vehicle from inventory
    """
    if not ObjectId.is_valid(vehicle_id):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Invalid vehicle ID format"
        )
    
    result = await VehicleModel.delete_vehicle(vehicle_id)
    
    if not result.get("success"):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=result.get("error", "Failed to delete vehicle")
        )
    
    return {"success": True, "vehicle_id": vehicle_id} 