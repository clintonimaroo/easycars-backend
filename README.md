# EasyCars Backend

This is the backend API for the EasyCars application. It's built with FastAPI and MongoDB.

## Setup

1. Clone the repository
2. Create a virtual environment:
   ```
   python -m venv venv
   ```
3. Activate the virtual environment:
   - On Windows: `venv\Scripts\activate`
   - On macOS/Linux: `source venv/bin/activate`
4. Install dependencies:
   ```
   pip install -r requirements.txt
   ```
5. Configure your application:
   - The application uses Pydantic settings in `app/config/env.py`
   - You can either modify the default values in this file directly
   - Or create a `.env` file with the variables you want to override
   - Example `.env` for overriding settings:
     ```
     MONGODB_URI=your_mongodb_connection_string
     DATABASE_NAME=easycars
     SECRET_KEY=your_jwt_secret
     ACCESS_TOKEN_EXPIRE_MINUTES=60
     ```

## Running the Application

```
uvicorn app.main:app --reload
```

## API Documentation

Once the application is running, you can access the Swagger documentation at:
```
http://localhost:8000/docs
```

## Running Tests

```
pytest
```

## Database Migration

To migrate data between MongoDB instances:

```
python scripts/db_migration.py --source-uri=<source-uri> --target-uri=<target-uri>
```

The migration script also reads configuration from `app/config/env.py` or your `.env` file.