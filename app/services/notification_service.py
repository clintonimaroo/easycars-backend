from app.utils.notifications import (
    create_info_notification,
    create_success_notification,
    create_warning_notification,
    create_error_notification
)

class NotificationService:
    """Notification service for system events"""
    
    @classmethod
    async def notify_vehicle_added(cls, user_id, vehicle_data):
        """
        Notify user when a vehicle is added
        """
        title = 'Vehicle Added'
        message = f"A new vehicle has been added: {vehicle_data.get('year')} {vehicle_data.get('make')} {vehicle_data.get('model')} (VIN: {vehicle_data.get('vin')})"
        link = f"/inventory/{vehicle_data.get('id')}"
        
        return await create_success_notification(user_id, title, message, link)
    
    @classmethod
    async def notify_vehicle_updated(cls, user_id, vehicle_data):
        """
        Notify user when a vehicle is updated
        """
        title = 'Vehicle Updated'
        message = f"{vehicle_data.get('year')} {vehicle_data.get('make')} {vehicle_data.get('model')} has been updated"
        link = f"/inventory/{vehicle_data.get('id')}"
        
        return await create_info_notification(user_id, title, message, link)
    
    @classmethod
    async def notify_vehicle_deleted(cls, user_id, vehicle_data):
        """
        Notify user when a vehicle is deleted
        """
        title = 'Vehicle Deleted'
        message = f"{vehicle_data.get('year')} {vehicle_data.get('make')} {vehicle_data.get('model')} has been removed from inventory"
        
        return await create_warning_notification(user_id, title, message)
    
    @classmethod
    async def notify_vehicle_sold(cls, user_id, vehicle_data, sale_data):
        """
        Notify user when a vehicle is sold
        """
        title = 'Vehicle Sold'
        message = f"{vehicle_data.get('year')} {vehicle_data.get('make')} {vehicle_data.get('model')} has been sold for ${sale_data.get('salePrice')}"
        link = f"/inventory/{vehicle_data.get('id')}"
        
        return await create_success_notification(user_id, title, message, link)
    
    @classmethod
    async def notify_low_inventory(cls, user_id, count):
        """
        Notify user about low inventory
        """
        title = 'Low Inventory'
        message = f"Your inventory is running low. Current vehicle count: {count}"
        link = '/inventory'
        
        return await create_warning_notification(user_id, title, message, link)
    
    @classmethod
    async def notify_document_uploaded(cls, user_id, document_data, vehicle_data):
        """
        Notify user about a new document upload
        """
        title = 'Document Uploaded'
        message = f"New document \"{document_data.get('originalFilename')}\" has been uploaded for {vehicle_data.get('year')} {vehicle_data.get('make')} {vehicle_data.get('model')}"
        link = f"/inventory/{vehicle_data.get('id')}"
        
        return await create_info_notification(user_id, title, message, link)
    
    @classmethod
    async def notify_system_maintenance(cls, user_id, message, scheduled_time):
        """
        Notify user about system maintenance
        """
        title = 'System Maintenance'
        formatted_time = scheduled_time.isoformat()
        full_message = f"System maintenance scheduled for {formatted_time}. {message}"
        
        return await create_warning_notification(user_id, title, full_message)
    
    @classmethod
    async def notify_system_error(cls, user_id, error_message, source):
        """
        Notify admin about an error
        """
        title = 'System Error'
        message = f"Error in {source}: {error_message}"
        
        return await create_error_notification(user_id, title, message) 