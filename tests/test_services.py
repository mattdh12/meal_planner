from datetime import date, timedelta

from meal_planner.domain import MEAL_SLOT_ORDER, start_of_week
from meal_planner.planning import compute_nutrition_targets
from meal_planner.services import ApplianceService, GroceryService, InventoryService, PlannerService, ProfileService, RecipeService
from meal_planner.store_catalog import get_wegmans_product_reference
from meal_planner.storage import Database, InventoryItem, MealPlanDay


def test_generate_week_plan_contains_all_meal_slots(tmp_path):
    database = Database(tmp_path / "planner.db")
    database.initialize()

    with database.session() as session:
        days = PlannerService(session).generate_week_plan(start_of_week(date.today()), regenerate=True)

        assert len(days) == 7
        for day in days:
            assert sorted(meal.meal_slot for meal in day.meals) == sorted(MEAL_SLOT_ORDER)


def test_inventory_updates_when_dinner_is_completed_and_manual_adjustment_is_allowed(tmp_path):
    database = Database(tmp_path / "inventory.db")
    database.initialize()

    with database.session() as session:
        profile = ProfileService(session).get_profile()
        planner = PlannerService(session)
        day = planner.generate_day_plan(date.today())
        dinner = next(meal for meal in day.meals if meal.meal_slot == "dinner" and meal.recipe_id)
        inventory_service = InventoryService(session)

        for recipe_ingredient in dinner.recipe.ingredients:
            inventory_service.adjust_inventory_item(
                recipe_ingredient.ingredient.name,
                recipe_ingredient.quantity * 2,
                "pantry",
                "Test setup",
                mode="delta",
                unit=recipe_ingredient.unit,
            )

        ingredient_names = [row.ingredient.name for row in dinner.recipe.ingredients]
        before = {
            name: sum(
                item.quantity
                for item in session.query(InventoryItem).filter(InventoryItem.name == name).all()
            )
            for name in ingredient_names
        }

        inventory_service.record_meal_completed(dinner.id, profile.leftovers_cap)
        inventory_service.adjust_inventory_item(
            "Greek yogurt",
            2,
            "fridge",
            "Grocery restock",
            mode="delta",
            unit="cup",
        )

        after = {
            name: sum(
                item.quantity
                for item in session.query(InventoryItem).filter(InventoryItem.name == name).all()
            )
            for name in ingredient_names
        }
        leftovers = session.query(InventoryItem).filter(InventoryItem.name == f"Leftover: {dinner.recipe.name}").one_or_none()
        yogurt = session.query(InventoryItem).filter(InventoryItem.name == "Greek yogurt").one()

        assert any(after[name] < before[name] for name in ingredient_names)
        assert leftovers is not None
        assert leftovers.quantity == min(dinner.recipe.leftover_servings, profile.leftovers_cap)
        assert yogurt.quantity == 2


def test_unknown_appliance_can_be_saved_for_future_plans(tmp_path):
    database = Database(tmp_path / "appliances.db")
    database.initialize()

    with database.session() as session:
        appliance = ApplianceService(session).resolve_unknown_appliance("Instant Pot", False)

        assert appliance.name == "Instant Pot"
        assert appliance.has_appliance is False


def test_appliance_can_be_added_and_removed_from_available_list(tmp_path):
    database = Database(tmp_path / "appliance_manage.db")
    database.initialize()

    with database.session() as session:
        service = ApplianceService(session)
        appliance = service.add_appliance("Toaster Oven")
        assert appliance is not None
        assert appliance.has_appliance is True

        updated = service.set_availability(appliance.id, False)
        assert updated is not None
        assert updated.has_appliance is False


def test_prep_tasks_are_filtered_to_the_due_date(tmp_path):
    database = Database(tmp_path / "prep_tasks.db")
    database.initialize()

    with database.session() as session:
        planner = PlannerService(session)
        week_start = start_of_week(date.today())
        days = planner.generate_week_plan(week_start, regenerate=True)
        next_dinner = next(
            meal
            for day in days
            if day.plan_date > date.today()
            for meal in day.meals
            if meal.meal_slot == "dinner" and meal.recipe is not None
        )
        recipe_ingredient = next_dinner.recipe.ingredients[0]
        InventoryService(session).adjust_inventory_item(
            recipe_ingredient.ingredient.name,
            recipe_ingredient.quantity,
            "freezer",
            "Test freezer setup",
            mode="delta",
            unit=recipe_ingredient.unit,
        )
        planner._rebuild_prep_tasks(week_start)
        target_due_date = next_dinner.day.plan_date - timedelta(days=1)
        tasks = planner.prep_tasks_for_date(target_due_date)

        assert tasks
        assert all(task.due_date == target_due_date for task in tasks)
        assert all(target_due_date.strftime("%A") in task.description for task in tasks)


def test_seed_inventory_uses_actual_household_items_and_breakfast_can_upgrade_beyond_cereal(tmp_path):
    database = Database(tmp_path / "seed_inventory.db")
    database.initialize()

    with database.session() as session:
        item_names = {item.name for item in session.query(InventoryItem).all()}
        planner = PlannerService(session)
        day = planner.generate_day_plan(date.today())
        breakfast = next(meal for meal in day.meals if meal.meal_slot == "breakfast")

        assert "Fiber One Honey Clusters Cereal" in item_names
        assert "Greek yogurt" not in item_names
        assert breakfast.recipe is not None
        assert breakfast.recipe.protein_g * breakfast.planned_servings >= 30
        assert planner.planned_meal_calories(breakfast) >= 480


def test_daily_plan_calories_stay_close_to_target(tmp_path):
    database = Database(tmp_path / "calorie_target.db")
    database.initialize()

    with database.session() as session:
        overview = PlannerService(session).today_overview(date.today())

        assert overview["planned_calories"] >= overview["nutrition"].calories - 250
        assert overview["planned_calories"] <= overview["nutrition"].calories + 450


def test_lunch_and_dinner_do_not_repeat_on_consecutive_days(tmp_path):
    database = Database(tmp_path / "variety.db")
    database.initialize()

    with database.session() as session:
        days = PlannerService(session).generate_week_plan(start_of_week(date.today()), regenerate=True)
        lunch_recipe_ids = [
            next((meal.recipe_id for meal in day.meals if meal.meal_slot == "lunch" and meal.recipe_id), None)
            for day in days
        ]
        dinner_recipe_ids = [
            next((meal.recipe_id for meal in day.meals if meal.meal_slot == "dinner" and meal.recipe_id), None)
            for day in days
        ]

        for previous, current in zip(lunch_recipe_ids, lunch_recipe_ids[1:]):
            assert previous is None or current is None or previous != current
        for previous, current in zip(dinner_recipe_ids, dinner_recipe_ids[1:]):
            assert previous is None or current is None or previous != current


def test_grocery_purchase_flow_updates_inventory(tmp_path):
    database = Database(tmp_path / "purchase_flow.db")
    database.initialize()

    with database.session() as session:
        week_start = start_of_week(date.today())
        planner = PlannerService(session)
        planner.generate_week_plan(week_start, regenerate=True)
        grocery_service = GroceryService(session)
        rows = grocery_service.purchase_rows(week_start)
        assert rows

        first_row = rows[0]
        grocery_service.apply_purchases(
            [
                {
                    "item_name": first_row["item"].ingredient_name,
                    "quantity": first_row["item"].quantity,
                    "unit": first_row["item"].unit,
                    "location": first_row["default_location"],
                }
            ]
        )

        purchased_item = session.query(InventoryItem).filter(InventoryItem.name == first_row["item"].ingredient_name).one()
        assert purchased_item.quantity >= first_row["item"].quantity


def test_marking_grocery_item_on_hand_updates_inventory_and_removes_it_from_list(tmp_path):
    database = Database(tmp_path / "mark_on_hand.db")
    database.initialize()

    with database.session() as session:
        week_start = start_of_week(date.today())
        PlannerService(session).generate_week_plan(week_start, regenerate=True)
        grocery_service = GroceryService(session)
        grocery_list = grocery_service.get_weekly_list(week_start)
        milk_item = next(item for item in grocery_list.items if item.ingredient_name == "Milk")

        grocery_service.mark_item_on_hand(milk_item.id)
        grocery_service.generate_weekly_list(week_start, regenerate=True)

        updated_list = grocery_service.get_weekly_list(week_start)
        updated_item_names = {item.ingredient_name for item in updated_list.items}
        milk_inventory = session.query(InventoryItem).filter(InventoryItem.name == "Milk").all()

        assert "Milk" not in updated_item_names
        assert sum(item.quantity for item in milk_inventory) >= 13


def test_workouts_per_week_adds_average_training_calories(tmp_path):
    database = Database(tmp_path / "workouts.db")
    database.initialize()

    with database.session() as session:
        profile = ProfileService(session).get_profile()

        profile.workouts_per_week = 0
        rest_day_average = compute_nutrition_targets(profile)

        profile.workouts_per_week = 4
        training_week_average = compute_nutrition_targets(profile)

        assert training_week_average.calories > rest_day_average.calories
        assert training_week_average.carbs_g > rest_day_average.carbs_g


def test_wegmans_catalog_returns_package_reference_for_milk():
    reference = get_wegmans_product_reference("Milk")

    assert reference is not None
    assert reference.package_size_label == "1/2 gallon carton"
    assert reference.inventory_quantity == 8.0


def test_grocery_rows_include_package_based_store_suggestions(tmp_path):
    database = Database(tmp_path / "wegmans_rows.db")
    database.initialize()

    with database.session() as session:
        week_start = start_of_week(date.today())
        PlannerService(session).generate_week_plan(week_start, regenerate=True)
        rows = GroceryService(session).shopping_rows(week_start)

        assert rows
        milk_row = next(row for row in rows if row["item"].ingredient_name == "Milk")

        assert milk_row["recommended_packages"] >= 1
        assert "Whole milk" in milk_row["shopping_label"]
        assert milk_row["recommended_inventory_quantity"] >= milk_row["item"].quantity


def test_seed_data_includes_stove_top_and_pans_appliances(tmp_path):
    database = Database(tmp_path / "appliance_seed.db")
    database.initialize()

    with database.session() as session:
        appliance_names = {appliance.name for appliance in ApplianceService(session).list_appliances()}

        assert "Meat Thermometer" in appliance_names
        assert "Stove Top" in appliance_names
        assert "Pans" in appliance_names


def test_leftover_recipes_include_direct_microwave_steps():
    steps = RecipeService.leftover_reheat_steps(type("RecipeStub", (), {"leftover_servings": 1})(), microwave_available=True)

    assert steps
    assert "Microwave for 2 minutes." in steps
    assert all("to" not in step for step in steps if "Microwave for" in step)
