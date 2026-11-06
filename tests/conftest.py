from os import environ


environ.setdefault("MONGODB_URI", "mongodb://127.0.0.1:27017")
environ.setdefault("SECRET_KEY", "test-only-placeholder-with-no-service-access")
