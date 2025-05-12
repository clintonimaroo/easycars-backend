from fastapi import APIRouter, Depends, HTTPException, status, Query
from typing import Dict, List, Optional
from bson import ObjectId

from app.models.vehicle import VehicleModel, VehicleCreate, VehicleUpdate, VehicleResponse
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
    
    return {"success": True, "vehicle_id": result.get("vehicle_id")}

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
    
    return {
        "success": True,
        "vehicle": VehicleModel.format_vehicle_response(updated_vehicle)
    }

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