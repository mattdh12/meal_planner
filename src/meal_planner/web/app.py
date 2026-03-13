from __future__ import annotations

from collections import defaultdict
from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from meal_planner.ai import AIPlannerAdapter
from meal_planner.domain import start_of_week
from meal_planner.services import (
    ApplianceService,
    FeedbackService,
    GroceryService,
    InventoryService,
    PlannerService,
    ProfileService,
    RecipeService,
)
from meal_planner.storage import Database, PlannedMeal


WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def _parse_week_start(raw_value: str | None) -> date:
    if raw_value:
        return start_of_week(datetime.fromisoformat(raw_value).date())
    return start_of_week(date.today())


def _base_context(request: Request, profile, unresolved_count: int) -> dict:
    return {
        "request": request,
        "today": date.today(),
        "profile": profile,
        "unresolved_count": unresolved_count,
    }


def create_app(database_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Meal Planner", version="0.1.0")
    app.state.database = Database(database_path=database_path)
    app.state.database.initialize()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.state.ai_adapter = AIPlannerAdapter()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    @app.get("/")
    async def root() -> RedirectResponse:
        return RedirectResponse(url="/today", status_code=303)

    @app.get("/today")
    async def today_dashboard(request: Request) -> object:
        with app.state.database.session() as session:
            planner = PlannerService(session)
            grocery_service = GroceryService(session)
            inventory_service = InventoryService(session)
            feedback_service = FeedbackService(session, app.state.ai_adapter)
            appliance_service = ApplianceService(session)
            overview = planner.today_overview(date.today())
            prep_tasks = planner.prep_tasks_for_date(date.today())
            grocery_list = grocery_service.get_weekly_list(start_of_week(date.today()))
            context = _base_context(request, overview["profile"], len(appliance_service.unresolved()))
            context.update(
                {
                    "overview": overview,
                    "prep_tasks": prep_tasks,
                    "grocery_list": grocery_list,
                    "recent_events": inventory_service.recent_events(),
                    "suggestions": feedback_service.dashboard_context()["suggestions"],
                }
            )
            return app.state.templates.TemplateResponse(request, "today.html", context)

    @app.post("/today/refresh")
    async def refresh_today() -> RedirectResponse:
        with app.state.database.session() as session:
            week_start = start_of_week(date.today())
            PlannerService(session).generate_week_plan(week_start, regenerate=True)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url="/today", status_code=303)

    @app.post("/today/meals/{planned_meal_id}/complete")
    async def complete_meal(planned_meal_id: int) -> RedirectResponse:
        with app.state.database.session() as session:
            profile = ProfileService(session).get_profile()
            InventoryService(session).record_meal_completed(planned_meal_id, profile.leftovers_cap)
            GroceryService(session).generate_weekly_list(start_of_week(date.today()), regenerate=True)
        return RedirectResponse(url="/today", status_code=303)

    @app.get("/plans/week")
    async def weekly_plan(request: Request, start: str | None = None) -> object:
        week_start = _parse_week_start(start)
        with app.state.database.session() as session:
            planner = PlannerService(session)
            recipe_service = RecipeService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            days = planner.generate_week_plan(week_start)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "week_start": week_start,
                    "days": days,
                    "recipe_options": recipe_service.recipe_options_by_slot(),
                }
            )
            return app.state.templates.TemplateResponse(request, "weekly_plan.html", context)

    @app.post("/plans/week/regenerate")
    async def regenerate_week(request: Request) -> RedirectResponse:
        form = await request.form()
        week_start = _parse_week_start(str(form.get("week_start")))
        with app.state.database.session() as session:
            PlannerService(session).generate_week_plan(week_start, regenerate=True)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url=f"/plans/week?start={week_start.isoformat()}", status_code=303)

    @app.post("/plans/meals/{planned_meal_id}/replace")
    async def replace_planned_meal(planned_meal_id: int, request: Request) -> RedirectResponse:
        form = await request.form()
        recipe_id = int(str(form.get("recipe_id")))
        with app.state.database.session() as session:
            planner = PlannerService(session)
            planner.replace_planned_meal(planned_meal_id, recipe_id)
            planned_meal = session.get(PlannedMeal, planned_meal_id)
            week_start = start_of_week(planned_meal.day.plan_date)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url=f"/plans/week?start={week_start.isoformat()}", status_code=303)

    @app.get("/inventory")
    async def inventory(request: Request) -> object:
        with app.state.database.session() as session:
            inventory_service = InventoryService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "inventory_by_location": inventory_service.grouped_items(),
                    "recent_events": inventory_service.recent_events(),
                }
            )
            return app.state.templates.TemplateResponse(request, "inventory.html", context)

    @app.post("/inventory/adjust")
    async def adjust_inventory(request: Request) -> RedirectResponse:
        form = await request.form()
        item_key = str(form.get("item_id") or form.get("name") or "").strip()
        if not item_key:
            return RedirectResponse(url="/inventory", status_code=303)

        quantity = float(str(form.get("quantity") or 0))
        location = str(form.get("location") or "pantry")
        unit = str(form.get("unit") or "count")
        mode = str(form.get("mode") or "delta")
        reason = str(form.get("reason") or "Manual adjustment")
        with app.state.database.session() as session:
            InventoryService(session).adjust_inventory_item(item_key, quantity, location, reason, mode=mode, unit=unit)
            GroceryService(session).generate_weekly_list(start_of_week(date.today()), regenerate=True)
        return RedirectResponse(url="/inventory", status_code=303)

    @app.get("/recipes")
    async def recipes(request: Request) -> object:
        with app.state.database.session() as session:
            recipe_service = RecipeService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update({"recipes": recipe_service.list_recipes()})
            return app.state.templates.TemplateResponse(request, "recipes.html", context)

    @app.get("/recipes/{recipe_id}")
    async def recipe_detail(recipe_id: int, request: Request) -> object:
        with app.state.database.session() as session:
            recipe_service = RecipeService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            recipe = recipe_service.get_recipe(recipe_id)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update({"recipe": recipe})
            return app.state.templates.TemplateResponse(request, "recipe_detail.html", context)

    @app.post("/recipes/{recipe_id}/feedback")
    async def recipe_feedback(recipe_id: int, request: Request) -> RedirectResponse:
        form = await request.form()
        with app.state.database.session() as session:
            RecipeService(session).add_feedback(
                recipe_id=recipe_id,
                tasty_rating=int(str(form.get("tasty_rating") or 3)),
                ease_rating=int(str(form.get("ease_rating") or 3)),
                notes=str(form.get("notes") or ""),
            )
        return RedirectResponse(url=f"/recipes/{recipe_id}", status_code=303)

    @app.get("/groceries")
    async def groceries(request: Request, start: str | None = None) -> object:
        week_start = _parse_week_start(start)
        with app.state.database.session() as session:
            PlannerService(session).generate_week_plan(week_start)
            grocery_service = GroceryService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            grocery_list = grocery_service.get_weekly_list(week_start)
            shopping_rows = grocery_service.shopping_rows(week_start)
            grouped: dict[str, list] = defaultdict(list)
            for row in shopping_rows:
                grouped[row["item"].section].append(row)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "week_start": week_start,
                    "grocery_list": grocery_list,
                    "grouped_rows": dict(grouped),
                    "distinct_item_count": len(grocery_list.items),
                    "store_match_count": sum(1 for row in shopping_rows if row["has_store_reference"]),
                    "linked_product_count": sum(1 for row in shopping_rows if row["product_url"]),
                }
            )
            return app.state.templates.TemplateResponse(request, "groceries.html", context)

    @app.post("/groceries/regenerate")
    async def regenerate_groceries(request: Request) -> RedirectResponse:
        form = await request.form()
        week_start = _parse_week_start(str(form.get("week_start")))
        with app.state.database.session() as session:
            PlannerService(session).generate_week_plan(week_start)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url=f"/groceries?start={week_start.isoformat()}", status_code=303)

    @app.get("/groceries/receive")
    async def receive_groceries(request: Request, start: str | None = None) -> object:
        week_start = _parse_week_start(start)
        with app.state.database.session() as session:
            PlannerService(session).generate_week_plan(week_start)
            grocery_service = GroceryService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "week_start": week_start,
                    "purchase_rows": grocery_service.purchase_rows(week_start),
                }
            )
            return app.state.templates.TemplateResponse(request, "grocery_receive.html", context)

    @app.post("/groceries/receive")
    async def receive_groceries_submit(request: Request) -> RedirectResponse:
        form = await request.form()
        week_start = _parse_week_start(str(form.get("week_start")))
        with app.state.database.session() as session:
            PlannerService(session).generate_week_plan(week_start)
            grocery_service = GroceryService(session)
            purchase_rows = grocery_service.purchase_rows(week_start)
            purchases: list[dict[str, object]] = []
            for row in purchase_rows:
                item = row["item"]
                include_key = f"include_{item.id}"
                if not form.get(include_key):
                    continue
                purchases.append(
                    {
                        "item_name": item.ingredient_name,
                        "quantity": float(str(form.get(f"quantity_{item.id}") or item.quantity)),
                        "unit": item.unit,
                        "location": str(form.get(f"location_{item.id}") or row["default_location"]),
                    }
                )
            grocery_service.apply_purchases(purchases)
            grocery_service.generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url="/inventory", status_code=303)

    @app.get("/profile")
    async def profile(request: Request) -> object:
        with app.state.database.session() as session:
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            profile = profile_service.get_profile()
            context = _base_context(request, profile, len(appliance_service.unresolved()))
            context.update({"appliances": appliance_service.list_appliances()})
            return app.state.templates.TemplateResponse(request, "profile.html", context)

    @app.post("/profile")
    async def update_profile(request: Request) -> RedirectResponse:
        form = await request.form()
        payload = {
            "name": str(form.get("name") or ""),
            "age": int(str(form.get("age") or 0)),
            "sex": str(form.get("sex") or "male"),
            "current_weight_lb": float(str(form.get("current_weight_lb") or 0)),
            "goal_weight_lb": float(str(form.get("goal_weight_lb") or 0)),
            "workouts_per_week": int(str(form.get("workouts_per_week") or 0)),
            "fitness_goal": str(form.get("fitness_goal") or ""),
            "shopping_frequency_days": int(str(form.get("shopping_frequency_days") or 7)),
            "preferred_store": str(form.get("preferred_store") or "Wegmans"),
            "leftovers_cap": int(str(form.get("leftovers_cap") or 1)),
            "breakfast_max_prep_minutes": int(str(form.get("breakfast_max_prep_minutes") or 0)),
            "lunch_max_prep_minutes": int(str(form.get("lunch_max_prep_minutes") or 10)),
            "snack_max_prep_minutes": int(str(form.get("snack_max_prep_minutes") or 0)),
            "dinner_max_prep_minutes": int(str(form.get("dinner_max_prep_minutes") or 30)),
            "notes": str(form.get("notes") or ""),
        }
        with app.state.database.session() as session:
            ProfileService(session).update_profile(payload)
            week_start = start_of_week(date.today())
            PlannerService(session).generate_week_plan(week_start, regenerate=True)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url="/profile", status_code=303)

    @app.post("/profile/appliances/resolve")
    async def resolve_appliance(request: Request) -> RedirectResponse:
        form = await request.form()
        appliance_name = str(form.get("name") or "")
        has_it = str(form.get("has_it") or "false").lower() == "true"
        with app.state.database.session() as session:
            ApplianceService(session).resolve_unknown_appliance(appliance_name, has_it)
            week_start = start_of_week(date.today())
            PlannerService(session).generate_week_plan(week_start, regenerate=True)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url="/profile", status_code=303)

    @app.get("/feedback")
    async def feedback_dashboard(request: Request) -> object:
        with app.state.database.session() as session:
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            recipe_service = RecipeService(session)
            feedback_service = FeedbackService(session, app.state.ai_adapter)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(feedback_service.dashboard_context())
            context.update({"supplements": recipe_service.list_supplements()})
            return app.state.templates.TemplateResponse(request, "feedback.html", context)

    @app.post("/feedback/supplements/{supplement_id}")
    async def supplement_feedback(supplement_id: int, request: Request) -> RedirectResponse:
        form = await request.form()
        with app.state.database.session() as session:
            RecipeService(session).add_supplement_feedback(
                supplement_id=supplement_id,
                rating=int(str(form.get("rating") or 3)),
                notes=str(form.get("notes") or ""),
            )
        return RedirectResponse(url="/feedback", status_code=303)

    return app
