from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from meal_planner.web.app import create_app


def test_main_screens_load(tmp_path):
    app = create_app(tmp_path / "routes.db")
    client = TestClient(app)

    for path in ["/today", "/plans/week", "/inventory", "/recipes", "/groceries", "/groceries/receive", "/profile", "/feedback"]:
        response = client.get(path)
        assert response.status_code == 200
    today_response = client.get("/today")
    assert "/recipes/" in today_response.text
    groceries_response = client.get("/groceries")
    assert "Suggested Wegmans buy" in groceries_response.text


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


def test_grocery_receive_route_redirects_after_submission(tmp_path):
    app = create_app(tmp_path / "receive_route.db")
    client = TestClient(app)

    receive_page = client.get("/groceries/receive")
    assert receive_page.status_code == 200

    import re

    match = re.search(r'name="include_(\d+)"', receive_page.text)
    assert match
    item_id = match.group(1)

    response = client.post(
        "/groceries/receive",
        data={
            "week_start": date.today().isoformat(),
            f"include_{item_id}": "on",
            f"quantity_{item_id}": "1",
            f"location_{item_id}": "pantry",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
