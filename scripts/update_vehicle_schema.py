"""
Script to update vehicle schema in the database to add new fields
"""

import sys
import os
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.vehicle import VehicleModel

def update_vehicle_schema():
    """
    Update the schema of existing vehicle records to include new fields
    """
    try:
        collection = VehicleModel.get_sync_collection()
        
        # Add default values for new fields to all existing vehicles
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
        
        # Update all documents that don't have these fields
        result = collection.update_many(
            {"stockNo": {"$exists": False}},
            {"$set": new_fields}
        )
        
        print(f"Updated {result.modified_count} vehicles to include new fields")
        return True
    except Exception as e:
        print(f"Error updating vehicle schema: {e}")
        return False

if __name__ == "__main__":
    print("Starting vehicle schema update...")
    success = update_vehicle_schema()
    if success:
        print("Vehicle schema update completed successfully")
    else:
        print("Vehicle schema update failed") 