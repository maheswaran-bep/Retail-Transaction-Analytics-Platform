"""Test suite for analytics endpoints and Product Master CRUD operations."""

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

TEST_STOCK_CODE = "TEST_API_SKU_99"


@pytest.fixture(scope="module")
def admin_headers():
    """Ensure admin account exists and return authorization header."""
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
    """Create approved test analyst and return authorization header."""
    analyst_email = "analyst_perm@retail.com"
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

    # Cleanup analyst
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM users WHERE email = :email"), {"email": analyst_email})


@pytest.fixture(autouse=True)
def cleanup_test_product():
    """Clean up test product before and after test."""
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dim_product WHERE stock_code = :sku"), {"sku": TEST_STOCK_CODE})
    yield
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM dim_product WHERE stock_code = :sku"), {"sku": TEST_STOCK_CODE})


# ==========================================
# 1. Analytics Endpoints Tests
# ==========================================

def test_analytics_kpis(analyst_headers):
    """Verify aggregate KPIs return 200 with valid numeric metrics."""
    resp = client.get("/api/analytics/kpis", headers=analyst_headers)
    assert resp.status_code == 200
    data = resp.json()

    assert isinstance(data["total_revenue"], (int, float))
    assert isinstance(data["total_units_sold"], int)
    assert isinstance(data["total_orders"], int)
    assert isinstance(data["average_order_value"], (int, float))

    assert data["total_revenue"] > 10_000_000
    assert data["total_units_sold"] > 1_000_000
    assert data["total_orders"] > 10_000
    assert data["average_order_value"] > 0


def test_analytics_kpis_date_filter(analyst_headers):
    """Verify KPIs filter by date window properly."""
    resp = client.get(
        "/api/analytics/kpis?start_date=2010-01-01&end_date=2010-06-30",
        headers=analyst_headers,
    )
    assert resp.status_code == 200
    data = resp.json()
    assert data["total_revenue"] > 0
    assert data["total_orders"] > 0


def test_analytics_top_products(analyst_headers):
    """Verify top products ranked by revenue and quantity."""
    # By revenue
    resp_rev = client.get("/api/analytics/top-products?limit=10&sort_by=revenue", headers=analyst_headers)
    assert resp_rev.status_code == 200
    items_rev = resp_rev.json()
    assert len(items_rev) == 10
    assert items_rev[0]["revenue"] >= items_rev[1]["revenue"]
    assert items_rev[0]["stock_code"] != ""

    # By quantity
    resp_qty = client.get("/api/analytics/top-products?limit=5&sort_by=quantity", headers=analyst_headers)
    assert resp_qty.status_code == 200
    items_qty = resp_qty.json()
    assert len(items_qty) == 5
    assert items_qty[0]["units_sold"] >= items_qty[1]["units_sold"]


def test_analytics_regional_sales(analyst_headers):
    """Verify regional sales breakdown grouped by country."""
    resp = client.get("/api/analytics/regional-sales", headers=analyst_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 5
    assert items[0]["revenue"] >= items[1]["revenue"]
    assert items[0]["country_name"] == "United Kingdom"
    assert items[0]["total_orders"] > 0


def test_analytics_regional_sales_date_and_exclude_uk(analyst_headers):
    """Verify regional sales with date filter, exclude_uk, and custom limit."""
    resp = client.get(
        "/api/analytics/regional-sales?start_date=2010-01-01&end_date=2010-12-31&exclude_uk=true&limit=5",
        headers=analyst_headers,
    )
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) == 5
    for item in items:
        assert item["country_name"] != "United Kingdom"
        assert item["revenue"] > 0
        assert item["units_sold"] > 0
        assert item["total_orders"] > 0
    assert items[0]["revenue"] >= items[1]["revenue"]


def test_analytics_monthly_trends(analyst_headers):
    """Verify monthly longitudinal sales performance."""
    resp = client.get("/api/analytics/monthly-trends", headers=analyst_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) >= 24  # spans two years of data
    assert items[0]["month"] < items[-1]["month"]
    assert items[0]["revenue"] > 0
    assert items[0]["order_count"] > 0


def test_analytics_declining_products(analyst_headers):
    """Verify detection of products declining >40% from Year 1 to Year 2."""
    resp = client.get("/api/analytics/declining-products", headers=analyst_headers)
    assert resp.status_code == 200
    items = resp.json()
    assert len(items) > 0
    for item in items:
        assert item["y1_units"] >= 50
        assert item["decline_pct"] > 40.0
        assert item["y1_revenue"] > 0


def test_analytics_unauthorized():
    """Verify analytics endpoints reject unauthenticated access with 401."""
    resp = client.get("/api/analytics/kpis")
    assert resp.status_code == 401


# ==========================================
# 2. Product Master CRUD & RBAC Tests
# ==========================================

def test_products_list_and_search(analyst_headers):
    """Verify analysts can list and search products with pagination."""
    # List paginated
    resp = client.get("/api/products?page=1&page_size=15", headers=analyst_headers)
    assert resp.status_code == 200
    data = resp.json()
    assert data["total"] > 4000
    assert data["page"] == 1
    assert data["page_size"] == 15
    assert len(data["items"]) == 15

    # Keyword search
    search_resp = client.get("/api/products?search=CAKESTAND", headers=analyst_headers)
    assert search_resp.status_code == 200
    search_data = search_resp.json()
    assert search_data["total"] > 0
    assert any("CAKESTAND" in item["description"].upper() for item in search_data["items"])


def test_products_crud_rbac_lifecycle(admin_headers, analyst_headers):
    """Verify Product CRUD lifecycle and RBAC enforcement between Admin and Analyst."""
    new_product_payload = {
        "stock_code": TEST_STOCK_CODE,
        "description": "Test Stainless Steel Cake Server",
        "unit_price": 14.50,
        "category": "Bakeware",
    }

    # 1. Analyst attempts CREATE -> 403 Forbidden
    analyst_create = client.post("/api/products", headers=analyst_headers, json=new_product_payload)
    assert analyst_create.status_code == 403

    # 2. Admin performs CREATE -> 201 Created
    admin_create = client.post("/api/products", headers=admin_headers, json=new_product_payload)
    assert admin_create.status_code == 201
    created_prod = admin_create.json()
    assert created_prod["stock_code"] == TEST_STOCK_CODE
    assert created_prod["unit_price"] == 14.50
    assert created_prod["is_active"] is True

    # 3. Analyst attempts UPDATE -> 403 Forbidden
    analyst_update = client.put(
        f"/api/products/{TEST_STOCK_CODE}",
        headers=analyst_headers,
        json={"unit_price": 18.00, "description": "Updated Cake Server"},
    )
    assert analyst_update.status_code == 403

    # 4. Admin performs UPDATE -> 200 OK
    admin_update = client.put(
        f"/api/products/{TEST_STOCK_CODE}",
        headers=admin_headers,
        json={"unit_price": 18.00, "description": "Updated Cake Server"},
    )
    assert admin_update.status_code == 200
    updated_prod = admin_update.json()
    assert updated_prod["unit_price"] == 18.00
    assert updated_prod["description"] == "Updated Cake Server"

    # 5. Analyst attempts DELETE -> 403 Forbidden
    analyst_delete = client.delete(f"/api/products/{TEST_STOCK_CODE}", headers=analyst_headers)
    assert analyst_delete.status_code == 403

    # 6. Admin performs SOFT DELETE -> 200 OK
    admin_delete = client.delete(f"/api/products/{TEST_STOCK_CODE}", headers=admin_headers)
    assert admin_delete.status_code == 200
    assert admin_delete.json()["is_active"] is False

    # 7. Verify is_active is now False in product list
    verify_resp = client.get(f"/api/products?search={TEST_STOCK_CODE}", headers=analyst_headers)
    assert verify_resp.status_code == 200
    assert verify_resp.json()["items"][0]["is_active"] is False
