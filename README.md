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
## Slizzy's Agent

The dashboard uses the existing backend URL. On the backend, set `AGENT_ENABLED=true`, put the authorized EasyCars account IDs in the comma-separated `AGENT_ALLOWED_USER_IDS`, and set `OPENAI_API_KEY` in the service's secret environment. Optional `AGENT_MODEL` and `AGENT_TRANSCRIBE_MODEL` override the defaults. Set `AGENT_ALLOW_VEHICLE_PHOTO_ANALYSIS=true` to allow the agent to send attached images to OpenAI for parts research. The public web search receives text search terms and candidate part names. Leave `AGENT_ALLOW_PRIVATE_FILE_INPUTS` off until the OpenAI project's data retention policy has been reviewed for dealership paperwork.

Run `python -m app.agent_worker` as a continuously running service with the same Mongo database and agent environment as the web API. For local development, `AGENT_WORKER_IN_WEB=true` starts the worker with the API. Historical sale imports and agent inventory creation with stock numbers require MongoDB transactions on a replica set. Copart time reminders appear in EasyCars notifications. Live lot updates require a separate authorized feed.

Existing inventory document previews and downloads require a signed-in owner. To grant access to a trusted administrator for shared or legacy documents, set `DOCUMENTS_ADMIN_USER_IDS` to their EasyCars account IDs.
