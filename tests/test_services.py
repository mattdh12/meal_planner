from datetime import date

from meal_planner.domain import MEAL_SLOT_ORDER, start_of_week
from meal_planner.services import ApplianceService, InventoryService, PlannerService, ProfileService
from meal_planner.storage import Database, InventoryItem


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

        ingredient_names = [row.ingredient.name for row in dinner.recipe.ingredients]
        before = {
            name: sum(
                item.quantity
                for item in session.query(InventoryItem).filter(InventoryItem.name == name).all()
            )
            for name in ingredient_names
        }

        InventoryService(session).record_meal_completed(dinner.id, profile.leftovers_cap)
        InventoryService(session).adjust_inventory_item(
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
        assert yogurt.quantity >= 6


def test_unknown_appliance_can_be_saved_for_future_plans(tmp_path):
    database = Database(tmp_path / "appliances.db")
    database.initialize()

    with database.session() as session:
        appliance = ApplianceService(session).resolve_unknown_appliance("Instant Pot", False)

        assert appliance.name == "Instant Pot"
        assert appliance.has_appliance is False


def test_prep_tasks_are_filtered_to_the_due_date(tmp_path):
    database = Database(tmp_path / "prep_tasks.db")
    database.initialize()

    with database.session() as session:
        planner = PlannerService(session)
        planner.generate_week_plan(start_of_week(date.today()), regenerate=True)
        tasks = planner.prep_tasks_for_date(date.today())

        assert tasks
        assert all(task.due_date == date.today() for task in tasks)
        assert all(date.today().strftime("%A") in task.description for task in tasks)
