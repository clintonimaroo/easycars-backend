import jwt
import time
from datetime import datetime, timedelta
from typing import Dict, Optional, Union
import logging

from app.config.env import settings

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Get JWT settings from config
JWT_SECRET = settings.SECRET_KEY
JWT_ALGORITHM = settings.ALGORITHM
ACCESS_TOKEN_EXPIRE_MINUTES = settings.ACCESS_TOKEN_EXPIRE_MINUTES

def create_access_token(data: Dict, expires_delta: Optional[timedelta] = None) -> str:
    """
    Create a JWT access token
    
    Args:
        data: Data to encode in the token
        expires_delta: Optional custom expiration time
        
    Returns:
        str: Encoded JWT token
    """
    to_encode = data.copy()
    
    # Set expiration time
    if expires_delta:
        expire = datetime.utcnow() + expires_delta
    else:
        expire = datetime.utcnow() + timedelta(minutes=ACCESS_TOKEN_EXPIRE_MINUTES)
    
    # Add standard JWT claims
    to_encode.update({
        "exp": expire,
        "iat": datetime.utcnow(),
        "sub": str(data.get("id", ""))
    })
    
    # Create the token
    encoded_jwt = jwt.encode(to_encode, JWT_SECRET, algorithm=JWT_ALGORITHM)
    return encoded_jwt

def decode_token(token: str) -> Union[Dict, None]:
    """
    Decode and validate a JWT token
    
    Args:
        token: JWT token to decode
        
    Returns:
        Dict: Decoded token data or None if invalid
    """
    try:
        payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        
        # Check if token is expired
        if "exp" in payload and payload["exp"] < time.time():
            logger.warning("Token expired")
            return None
            
        return payload
    except jwt.PyJWTError as e:
        logger.error(f"Error decoding token: {e}")
        return None

def is_token_valid(token: str) -> bool:
    """
    Check if a token is valid
    
    Args:
        token: JWT token to validate
        
    Returns:
        bool: True if token is valid, False otherwise
    """
    return decode_token(token) is not None 