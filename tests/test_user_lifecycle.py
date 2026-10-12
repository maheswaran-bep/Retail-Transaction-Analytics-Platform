"""Integration tests covering full user lifecycle: Approve, Decline, Deactivate, Reactivate."""

import sys
from pathlib import Path

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


def cleanup_users(emails: list[str]):
    """Clean up specified user accounts from database."""
    with engine.begin() as conn:
        for em in emails:
            conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": em})


@pytest.fixture(scope="module")
def admin_auth():
    admin_email = "admin@retail.com"
    admin_password = "Admin@123"

    with engine.begin() as conn:
        conn.execute(
            text("""
                INSERT INTO users (email, hashed_password, role, is_approved, is_active)
                VALUES (:email, :pwd, 'admin', TRUE, TRUE)
                ON DUPLICATE KEY UPDATE hashed_password=VALUES(hashed_password), role='admin', is_approved=TRUE, is_active=TRUE, is_active=TRUE
            """),
            {"email": admin_email, "pwd": get_password_hash(admin_password)},
        )

    login_resp = client.post("/api/auth/login", json={"email": admin_email, "password": admin_password})
    assert login_resp.status_code == 200
    data = login_resp.json()
    token = data["access_token"]
    user_id = data["user_id"]
    return {
        "headers": {"Authorization": f"Bearer {token}"},
        "admin_id": user_id,
        "admin_email": admin_email,
        "token": token,
    }


def test_full_user_lifecycle(admin_auth):
    """Test full cycle: Register -> Blocked (Pending) -> Approve -> Login -> Deactivate -> Blocked (Deactivated) -> Reactivate -> Login."""
    analyst_email = "lifecycle_test@retail.com"
    analyst_pwd = "Password@123"

    cleanup_users([analyst_email])

    try:
        # 1. Register analyst
        reg_res = client.post("/api/auth/register", json={"email": analyst_email, "password": analyst_pwd})
        assert reg_res.status_code == 201
        reg_data = reg_res.json()
        assert reg_data["role"] == "analyst"
        assert reg_data["is_approved"] is False
        assert reg_data["is_active"] is True
        analyst_id = reg_data["user_id"]

        # 2. Blocked login when unapproved
        login_res = client.post("/api/auth/login", json={"email": analyst_email, "password": analyst_pwd})
        assert login_res.status_code == 403
        assert "pending administrator approval" in login_res.json()["detail"].lower()

        # 3. Check pending users
        pending_res = client.get("/api/admin/pending-users", headers=admin_auth["headers"])
        assert pending_res.status_code == 200
        pending_ids = [u["id"] for u in pending_res.json()]
        assert analyst_id in pending_ids

        # 4. Admin approves user
        approve_res = client.post(
            "/api/admin/approve-user",
            headers=admin_auth["headers"],
            json={"user_id": analyst_id, "approve": True},
        )
        assert approve_res.status_code == 200
        assert approve_res.json()["is_approved"] is True
        assert approve_res.json()["is_active"] is True

        # 5. Approved user can log in
        login_approved = client.post("/api/auth/login", json={"email": analyst_email, "password": analyst_pwd})
        assert login_approved.status_code == 200
        analyst_token = login_approved.json()["access_token"]
        analyst_headers = {"Authorization": f"Bearer {analyst_token}"}

        # 6. Analyst can query /api/auth/me
        me_res = client.get("/api/auth/me", headers=analyst_headers)
        assert me_res.status_code == 200
        assert me_res.json()["is_approved"] is True
        assert me_res.json()["is_active"] is True

        # 7. Admin deactivates analyst
        deact_res = client.post(
            "/api/admin/toggle-user-status",
            headers=admin_auth["headers"],
            json={"user_id": analyst_id, "is_active": False},
        )
        assert deact_res.status_code == 200
        assert deact_res.json()["is_active"] is False

        # 8. Deactivated analyst login blocked
        login_deact = client.post("/api/auth/login", json={"email": analyst_email, "password": analyst_pwd})
        assert login_deact.status_code == 403
        assert "deactivated" in login_deact.json()["detail"].lower()
        assert login_deact.json()["detail"] == "Your account has been deactivated. Contact an administrator."

        # 9. Existing token of deactivated analyst blocked on API call
        blocked_me = client.get("/api/auth/me", headers=analyst_headers)
        assert blocked_me.status_code == 403
        assert blocked_me.json()["detail"] == "Your account has been deactivated. Contact an administrator."

        # 10. Admin reactivates analyst
        react_res = client.post(
            "/api/admin/toggle-user-status",
            headers=admin_auth["headers"],
            json={"user_id": analyst_id, "is_active": True},
        )
        assert react_res.status_code == 200
        assert react_res.json()["is_active"] is True

        # 11. Reactivated analyst can log in again
        login_react = client.post("/api/auth/login", json={"email": analyst_email, "password": analyst_pwd})
        assert login_react.status_code == 200
        assert "access_token" in login_react.json()

    finally:
        cleanup_users([analyst_email])


def test_decline_analyst(admin_auth):
    """Test declining an analyst account sets is_approved=False, is_active=False and removes from pending."""
    analyst_email = "decline_test@retail.com"
    analyst_pwd = "Password@123"

    cleanup_users([analyst_email])

    try:
        # Register analyst
        reg_res = client.post("/api/auth/register", json={"email": analyst_email, "password": analyst_pwd})
        assert reg_res.status_code == 201
        analyst_id = reg_res.json()["user_id"]

        # Decline analyst
        decline_res = client.post(
            "/api/admin/approve-user",
            headers=admin_auth["headers"],
            json={"user_id": analyst_id, "approve": False},
        )
        assert decline_res.status_code == 200
        assert decline_res.json()["is_approved"] is False
        assert decline_res.json()["is_active"] is False

        # Ensure analyst is no longer in pending list
        pending_res = client.get("/api/admin/pending-users", headers=admin_auth["headers"])
        assert pending_res.status_code == 200
        pending_ids = [u["id"] for u in pending_res.json()]
        assert analyst_id not in pending_ids

        # Attempting to log in shows deactivated message
        login_res = client.post("/api/auth/login", json={"email": analyst_email, "password": analyst_pwd})
        assert login_res.status_code == 403
        assert login_res.json()["detail"] == "Your account has been deactivated. Contact an administrator."

    finally:
        cleanup_users([analyst_email])


def test_admin_cannot_deactivate_self(admin_auth):
    """Test administrator cannot deactivate their own account (HTTP 400)."""
    res = client.post(
        "/api/admin/toggle-user-status",
        headers=admin_auth["headers"],
        json={"user_id": admin_auth["admin_id"], "is_active": False},
    )
    assert res.status_code == 400
    assert res.json()["detail"] == "Administrators cannot deactivate their own account."
