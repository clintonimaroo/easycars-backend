from datetime import timedelta
from http.cookies import SimpleCookie

from fastapi import FastAPI
from fastapi.testclient import TestClient
from pydantic import ValidationError
import pytest

from app.config.env import Settings, settings
from app.models.user import UserModel
from app.routes.auth import router
from app.utils import auth


def test_settings_require_credentials_without_showing_other_secret_values(monkeypatch):
    monkeypatch.delenv("MONGODB_URI")
    monkeypatch.delenv("SECRET_KEY")
    marker = "synthetic-secret-must-not-appear-in-errors"
    monkeypatch.setenv("WORDPRESS_APPLICATION_PASSWORD", marker)

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)

    assert {item["loc"] for item in error.value.errors(include_input=False)} == {("MONGODB_URI",), ("SECRET_KEY",)}
    assert marker not in str(error.value)


def test_login_uses_the_same_configured_lifetime_for_the_token_and_cookie(monkeypatch):
    monkeypatch.setattr(settings, "ENVIRONMENT", "development")
    monkeypatch.setattr(settings, "ACCESS_TOKEN_EXPIRE_MINUTES", 45)
    monkeypatch.setattr(auth, "JWT_SECRET", "test-only-session-signing-key-long-enough")
    user = {"_id": "507f1f77bcf86cd799439011", "username": "test-user", "password": "test-hash"}

    async def find_user(username):
        return user if username == user["username"] else None

    async def verify_password(password, password_hash):
        return (password, password_hash) == ("test-password", user["password"])

    monkeypatch.setattr(UserModel, "find_by_username_or_email", find_user)
    monkeypatch.setattr(UserModel, "verify_password", verify_password)
    app = FastAPI()
    app.include_router(router, prefix="/auth")

    with TestClient(app) as client:
        response = client.post("/auth/login", data={"username": "test-user", "password": "test-password"})

    assert response.status_code == 200
    body = response.json()
    payload = auth.decode_token(body["access_token"])
    cookie = SimpleCookie(response.headers["set-cookie"])["auth_token"]
    assert body["expires_in"] == payload["exp"] - payload["iat"] == int(cookie["max-age"]) == 2700
    assert cookie.value == body["access_token"]
    assert response.headers["cache-control"] == "no-store"
    expired = auth.create_access_token({"id": user["_id"]}, expires_delta=timedelta(seconds=-1))
    assert auth.decode_token(expired) is None
