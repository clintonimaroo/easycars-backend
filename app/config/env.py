from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # Environment
    ENVIRONMENT: str = "production"  # "development" or "production"
    
    # MongoDB Connection
    MONGODB_URI: str = "mongodb+srv://preciouskayili:hZNuireqDH2NKBZH@easycars.85zhfws.mongodb.net/?retryWrites=true&w=majority&appName=easycars"
    DATABASE_NAME: str = "easycars"
    
    # JWT Authentication
    SECRET_KEY: str = "e3264125dff3c5f737092ca8510d5b8b214e64ee295252e1700e260136f7ca79"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # For Migration
    MONGODB_SOURCE_URI: str = ""
    MONGODB_SOURCE_DB: str = ""
    
    # Service URLs
    FRONTEND_URL: str = "https://dashboard.easycarsllc.com"
    
    # AWS S3 Configuration (for document uploads)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_BUCKET_NAME: str = "easycars"

    class Config:
        env_file = ".env"


settings = Settings()
