"""
Simple migration script for updating vehicle schema
"""
import asyncio
import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.vehicle import VehicleModel

async def update_vehicles():
    """Update existing vehicle records with new fields"""
    collection = await VehicleModel.get_collection()
    
    new_fields = {
        "stockNo": None,
        "isNew": True,
        "motorNo": None,
        "keyNo": None,
        "trunkNo": None,
        "modelNo": None,
        "intRoNo": None,
        "soldTo": None,
        "buyerAddress": None,
        "buyerCity": None,
        "buyerState": None,
        "buyerZip": None,
        "buyerPhone": None,
        "dateSold": None,
        "invoiceNo": None,
        "salesperson": None,
        "dateOfPurchase": None,
        "purchasedFrom": None,
        "sellerAddress": None,
        "sellerCity": None,
        "sellerState": None,
        "sellerZip": None,
        "titleNo": None,
        "licenseNo": None,
        "hasBillOfSale": False,
        "hasTitleCertificate": False,
        "hasOdometerCertification": False,
        "hasPowerOfAttorney": False,
        "hasContractSigned": False,
        "hasBuyersGuide": False
    }
    
    result = await collection.update_many(
        {"stockNo": {"$exists": False}},
        {"$set": new_fields}
    )
    
    print(f"Updated {result.modified_count} vehicle records")

if __name__ == "__main__":
    asyncio.run(update_vehicles()) 