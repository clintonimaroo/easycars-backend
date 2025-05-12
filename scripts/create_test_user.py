import asyncio
import sys
import os

# Add the parent directory to the Python path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.models.user import UserModel, UserCreate
from app.utils.mongodb import get_async_client

async def create_test_user():
    # Create user data
    user_data = UserCreate(
        username="Slizzyobeto@easycarsllc.com",  # Using email as username
        email="Slizzyobeto@easycarsllc.com",
        password="Oshone1996@",
        role="user"
    )
    
    try:
        # Initialize MongoDB connection
        await get_async_client()
        
        # Create the user
        result = await UserModel.create_user(user_data)
        
        if result.get("success"):
            print(f"Successfully created user with ID: {result.get('user_id')}")
        else:
            print(f"Failed to create user: {result.get('error')}")
            
    except Exception as e:
        print(f"Error creating user: {str(e)}")
    finally:
        # Close MongoDB connection
        client = await get_async_client()
        client.close()

if __name__ == "__main__":
    asyncio.run(create_test_user()) 