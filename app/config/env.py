from pydantic_settings import BaseSettings

class Settings(BaseSettings):
    # MongoDB Connection
    MONGODB_URI: str = "mongodb+srv://clinton:Kindness1234@cluster0.jxd72pf.mongodb.net/easycars?retryWrites=true&w=majority&tls=true&tlsAllowInvalidCertificates=true"
    DATABASE_NAME: str = "easycars"
    
    # JWT Authentication
    SECRET_KEY: str = "your-secret-key-here"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    
    # For Migration
    MONGODB_SOURCE_URI: str = ""
    MONGODB_SOURCE_DB: str = ""
    
    # Service URLs
    FRONTEND_URL: str = "http://localhost:3000"
    
    # AWS S3 Configuration (for document uploads)
    AWS_ACCESS_KEY_ID: str = ""
    AWS_SECRET_ACCESS_KEY: str = ""
    AWS_REGION: str = "us-east-1"
    AWS_BUCKET_NAME: str = "easycars"

    class Config:
        env_file = ".env"


settings = Settings()
