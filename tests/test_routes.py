from pathlib import Path

from fastapi.testclient import TestClient

from meal_planner.web.app import create_app


def test_main_screens_load(tmp_path):
    app = create_app(tmp_path / "routes.db")
    client = TestClient(app)

    for path in ["/today", "/plans/week", "/inventory", "/recipes", "/groceries", "/profile", "/feedback"]:
        response = client.get(path)
        assert response.status_code == 200
    today_response = client.get("/today")
    assert "/recipes/" in today_response.text


def test_inventory_adjust_route_redirects(tmp_path):
    app = create_app(tmp_path / "inventory_route.db")
    client = TestClient(app)

    response = client.post(
        "/inventory/adjust",
        data={
            "name": "Test pantry item",
            "quantity": "2",
            "location": "pantry",
            "unit": "count",
            "mode": "delta",
            "reason": "Manual adjustment",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_today_refresh_route_redirects(tmp_path):
    app = create_app(tmp_path / "refresh.db")
    client = TestClient(app)

    response = client.post("/today/refresh", follow_redirects=False)

    assert response.status_code == 303
