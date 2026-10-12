"""Test suite for ETL audit logs and incremental upload endpoints."""

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

TEST_INVOICE = "TEST_INC_999991"
TEST_SKU = "TEST_INC_SKU_1"


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


@pytest.fixture(autouse=True)
def cleanup_incremental_records():
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM fact_sales WHERE invoice_no = :inv"), {"inv": TEST_INVOICE})
        conn.execute(text("DELETE FROM dim_product WHERE stock_code = :sku"), {"sku": TEST_SKU})
        conn.execute(text("DELETE FROM etl_audit_log WHERE file_name = 'test_incremental.csv'"))
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM fact_sales WHERE invoice_no = :inv"), {"inv": TEST_INVOICE})
        conn.execute(text("DELETE FROM dim_product WHERE stock_code = :sku"), {"sku": TEST_SKU})
        conn.execute(text("DELETE FROM etl_audit_log WHERE file_name = 'test_incremental.csv'"))


def test_get_etl_logs(admin_headers):
    """Verify ETL logs endpoint returns history from etl_audit_log."""
    res = client.get("/api/analytics/etl-logs", headers=admin_headers)
    assert res.status_code == 200
    logs = res.json()
    assert isinstance(logs, list)
    assert len(logs) >= 2
    assert "run_id" in logs[0]
    assert "status" in logs[0]


def test_incremental_upload_and_deduplication(admin_headers):
    """Verify uploading incremental CSV and skipping duplicate invoices."""
    sample_csv = (
        f"Invoice,StockCode,Description,Quantity,InvoiceDate,Price,Customer ID,Country\n"
        f"{TEST_INVOICE},{TEST_SKU},Incremental Test Ceramic Mug,10,2026-10-10 12:00:00,12.50,19999,United Kingdom\n"
    )

    # 1. First Upload
    files = {"file": ("test_incremental.csv", sample_csv.encode("utf-8"), "text/csv")}
    upload_res = client.post("/api/etl/upload-incremental", headers=admin_headers, files=files)
    assert upload_res.status_code == 200
    data = upload_res.json()
    assert data["status"] == "SUCCESS"
    assert data["records_inserted"] == 1
    assert data["duplicate_invoices_skipped"] == 0

    # 2. Second Upload with same invoice -> Duplicate Prevention
    dup_files = {"file": ("test_incremental.csv", sample_csv.encode("utf-8"), "text/csv")}
    dup_res = client.post("/api/etl/upload-incremental", headers=admin_headers, files=dup_files)
    assert dup_res.status_code == 200
    dup_data = dup_res.json()
    assert dup_data["records_inserted"] == 0
    assert dup_data["duplicate_invoices_skipped"] == 1
    assert dup_data["status"] == "SKIPPED_DUPLICATES"
