from pydantic import Field, PositiveInt, SecretStr
from pydantic_settings import BaseSettings
import os

class Settings(BaseSettings):
    # Environment
    ENVIRONMENT: str = "production"  # "development" or "production"
    
    # MongoDB Connection
    MONGODB_URI: str = Field(min_length=1)
    DATABASE_NAME: str = "easycars"
    MONGODB_TLS: bool = True
    
    # JWT Authentication
    SECRET_KEY: str = Field(min_length=1)
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: PositiveInt = 43200
    
    # For Migration
    MONGODB_SOURCE_URI: str = ""
    MONGODB_SOURCE_DB: str = ""
    
    # Service URLs
    FRONTEND_URL: str = "https://dashboard.easycarsllc.com"
    API_URL: str = os.environ.get("API_URL", "http://localhost:8000")

    WOOCOMMERCE_STORE_URL: str = ""
    WOOCOMMERCE_CONSUMER_KEY: str = ""
    WOOCOMMERCE_CONSUMER_SECRET: str = ""
    WOOCOMMERCE_ALLOWED_USER_IDS: str = ""
    WORDPRESS_USERNAME: str = ""
    WORDPRESS_APPLICATION_PASSWORD: str = ""

    OPENAI_API_KEY: SecretStr = SecretStr("")
    AGENT_ENABLED: bool = False
    AGENT_ALLOWED_USER_IDS: str = ""
    DOCUMENTS_ADMIN_USER_IDS: str = ""
    AGENT_MODEL: str = "gpt-5-mini"
    AGENT_TRANSCRIBE_MODEL: str = "gpt-4o-mini-transcribe"
    AGENT_ALLOW_VEHICLE_PHOTO_ANALYSIS: bool = False
    AGENT_ALLOW_PRIVATE_FILE_INPUTS: bool = False
    AGENT_WORKER_IN_WEB: bool = False
    
    # AWS S3 Configuration (for document uploads)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_BUCKET_NAME: str = "easycars"
    
    # Cloudinary Configuration (for document uploads)
    CLOUDINARY_CLOUD_NAME: str = os.environ.get("CLOUDINARY_CLOUD_NAME", "kardusers")
    CLOUDINARY_API_KEY: str = os.environ.get("CLOUDINARY_API_KEY", "KmUswGUuGJAttg45gGMIuFegh-Q")
    CLOUDINARY_API_SECRET: str = os.environ.get("CLOUDINARY_API_SECRET", "your_cloudinary_api_secret")
    CLOUDINARY_FOLDER: str = os.environ.get("CLOUDINARY_FOLDER", "easycars_documents")

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"
        hide_input_in_errors = True


settings = Settings()
