from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from meal_planner.storage import Recipe
from meal_planner.web.app import create_app


def test_main_screens_load(tmp_path):
    app = create_app(tmp_path / "routes.db")
    client = TestClient(app)

    for path in ["/today", "/plans/week", "/inventory", "/recipes", "/groceries", "/groceries/receive", "/profile", "/feedback"]:
        response = client.get(path)
        assert response.status_code == 200
    today_response = client.get("/today")
    assert "/recipes/" in today_response.text
    assert "Supplements today" in today_response.text
    weekly_response = client.get("/plans/week")
    assert "/recipes/" in weekly_response.text
    assert weekly_response.text.index("Breakfast") < weekly_response.text.index("Lunch") < weekly_response.text.index("Snack") < weekly_response.text.index("Dinner")
    groceries_response = client.get("/groceries")
    assert "One clean shopping view" in groceries_response.text
    assert "Check off" in groceries_response.text
    assert "<p class=\"eyebrow\">Dairy</p>" not in groceries_response.text
    inventory_response = client.get("/inventory")
    assert "Quick delta" not in inventory_response.text
    with app.state.database.session() as session:
        leftover_recipe = session.query(Recipe).filter(Recipe.leftover_servings > 0).order_by(Recipe.id.asc()).first()
    recipe_response = client.get(f"/recipes/{leftover_recipe.id}")
    assert "Leftover microwave guide" in recipe_response.text


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


def test_mark_grocery_item_on_hand_redirects_after_submission(tmp_path):
    app = create_app(tmp_path / "mark_on_hand_route.db")
    client = TestClient(app)

    groceries_page = client.get("/groceries")
    assert groceries_page.status_code == 200

    import re

    match = re.search(r'action="/groceries/items/(\d+)/mark-on-hand"', groceries_page.text)
    assert match
    item_id = match.group(1)

    response = client.post(
        f"/groceries/items/{item_id}/mark-on-hand",
        data={"week_start": date.today().isoformat()},
        follow_redirects=False,
    )

    assert response.status_code == 303


def test_profile_appliance_add_and_remove_routes_redirect(tmp_path):
    app = create_app(tmp_path / "appliance_route.db")
    client = TestClient(app)

    add_response = client.post(
        "/profile/appliances/add",
        data={"name": "Toaster Oven"},
        follow_redirects=False,
    )
    assert add_response.status_code == 303

    profile_page = client.get("/profile")
    assert "Toaster Oven" in profile_page.text

    import re

    match = re.search(r'action="/profile/appliances/(\d+)/availability"', profile_page.text)
    assert match
    appliance_id = match.group(1)

    remove_response = client.post(
        f"/profile/appliances/{appliance_id}/availability",
        data={"has_it": "false"},
        follow_redirects=False,
    )
    assert remove_response.status_code == 303
