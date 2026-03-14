from datetime import date
from pathlib import Path

from fastapi.testclient import TestClient

from meal_planner.storage import MealPlanDay, PlannedMeal, Recipe
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


def test_recipe_create_route_saves_recipe_with_ingredients_and_appliances(tmp_path):
    app = create_app(tmp_path / "recipe_create.db")
    client = TestClient(app)

    response = client.post(
        "/recipes/new",
        data={
            "name": "Test Protein Oats",
            "meal_slot": "breakfast",
            "prep_minutes": "4",
            "cook_minutes": "0",
            "simplicity_score": "5",
            "pots_pans_score": "1",
            "servings": "1",
            "leftover_servings": "0",
            "calories": "510",
            "protein_g": "35",
            "carbs_g": "58",
            "fat_g": "12",
            "has_protein_component": "on",
            "has_carb_component": "on",
            "instructions": "Add oats.\nAdd protein.\nEat.",
            "notes": "Simple breakfast.",
            "ingredient_lines": "Rolled oats | 1 | cup\nProtein powder | 1 | scoop\nMilk | 1 | cup",
            "appliance_lines": "Microwave",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with app.state.database.session() as session:
        recipe = session.query(Recipe).filter(Recipe.name == "Test Protein Oats").one()
        assert recipe.meal_slot == "breakfast"
        assert len(recipe.ingredients) == 3
        assert [appliance.appliance_name for appliance in recipe.appliances] == ["Microwave"]


def test_recipe_edit_route_updates_recipe_and_renames_planned_meals(tmp_path):
    app = create_app(tmp_path / "recipe_edit.db")
    client = TestClient(app)

    with app.state.database.session() as session:
        recipe = session.query(Recipe).filter(Recipe.name == "Fiber One Cereal Bowl").one()
        breakfast_recipe_id = recipe.id
        day = MealPlanDay(plan_date=date.today())
        session.add(day)
        session.flush()
        session.add(
            PlannedMeal(
                meal_plan_day_id=day.id,
                meal_slot="breakfast",
                recipe_id=breakfast_recipe_id,
                title=recipe.name,
                planned_servings=1,
            )
        )

    response = client.post(
        f"/recipes/{breakfast_recipe_id}/edit",
        data={
            "name": "Fiber One Breakfast Bowl",
            "meal_slot": "breakfast",
            "prep_minutes": "1",
            "cook_minutes": "0",
            "simplicity_score": "5",
            "pots_pans_score": "1",
            "servings": "1",
            "leftover_servings": "0",
            "calories": "300",
            "protein_g": "12",
            "carbs_g": "48",
            "fat_g": "6",
            "has_carb_component": "on",
            "instructions": "Pour cereal.\nAdd milk.\nEat.",
            "notes": "Updated name.",
            "ingredient_lines": "Fiber One Honey Clusters Cereal | 1.5 | cup\nMilk | 1 | cup",
            "appliance_lines": "",
        },
        follow_redirects=False,
    )

    assert response.status_code == 303
    with app.state.database.session() as session:
        recipe = session.get(Recipe, breakfast_recipe_id)
        assert recipe is not None
        assert recipe.name == "Fiber One Breakfast Bowl"
        planned_titles = {meal.title for meal in session.query(PlannedMeal).filter(PlannedMeal.recipe_id == breakfast_recipe_id).all()}
        assert "Fiber One Breakfast Bowl" in planned_titles
