"""Test suite verifying administrator management routes."""

import sys
from pathlib import Path

# Add project root to sys.path
PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import text

from backend.main import app
from backend.database.session import engine
from backend.auth.security import get_password_hash

client = TestClient(app)


@pytest.fixture(scope="module")
def admin_headers():
    admin_email = "admin@retail.com"
    admin_password = "Admin@123"

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO users (email, hashed_password, role, is_approved)
                VALUES (:email, :pwd, 'admin', TRUE)
                ON DUPLICATE KEY UPDATE hashed_password=VALUES(hashed_password), role='admin', is_approved=TRUE, is_active=TRUE
            """),
            {"email": admin_email, "pwd": get_password_hash(admin_password)},
        )

    login_resp = client.post("/api/auth/login", json={"email": admin_email, "password": admin_password})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture(scope="module")
def analyst_headers():
    analyst_email = "analyst_admin_test@retail.com"
    analyst_password = "Password@123"

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO users (email, hashed_password, role, is_approved)
                VALUES (:email, :pwd, 'analyst', TRUE)
                ON DUPLICATE KEY UPDATE role='analyst', is_approved=TRUE
            """),
            {"email": analyst_email, "pwd": get_password_hash(analyst_password)},
        )

    login_resp = client.post("/api/auth/login", json={"email": analyst_email, "password": analyst_password})
    assert login_resp.status_code == 200
    token = login_resp.json()["access_token"]
    yield {"Authorization": f"Bearer {token}"}

    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": analyst_email})


def test_get_all_users_admin(admin_headers):
    """Verify GET /api/admin/users returns HTTP 200 and complete list of registered users."""
    res = client.get("/api/admin/users", headers=admin_headers)
    assert res.status_code == 200, f"Expected 200, got {res.status_code}: {res.text}"
    users = res.json()
    assert isinstance(users, list)
    assert len(users) >= 1

    first = users[0]
    assert "id" in first
    assert "email" in first
    assert "role" in first
    assert "is_approved" in first

    admin_record = next((u for u in users if u["email"] == "admin@retail.com"), None)
    assert admin_record is not None
    assert admin_record["role"] == "admin"
    assert admin_record["is_approved"] is True


def test_get_pending_users_admin(admin_headers):
    """Verify GET /api/admin/pending-users returns HTTP 200 with list of unapproved analysts."""
    res = client.get("/api/admin/pending-users", headers=admin_headers)
    assert res.status_code == 200
    pending = res.json()
    assert isinstance(pending, list)
    for u in pending:
        assert u["is_approved"] is False


def test_admin_routes_forbidden_for_analyst(analyst_headers):
    """Verify analysts cannot access admin routes (HTTP 403)."""
    res_users = client.get("/api/admin/users", headers=analyst_headers)
    assert res_users.status_code == 403

    res_pending = client.get("/api/admin/pending-users", headers=analyst_headers)
    assert res_pending.status_code == 403


def test_admin_routes_unauthenticated():
    """Verify unauthenticated requests are rejected with HTTP 401."""
    res = client.get("/api/admin/users")
    assert res.status_code == 401
