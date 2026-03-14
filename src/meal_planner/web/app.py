from __future__ import annotations

from datetime import date, datetime
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import RedirectResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from meal_planner.ai import AIPlannerAdapter
from meal_planner.domain import MEAL_SLOT_ORDER, start_of_week
from meal_planner.services import (
    ApplianceService,
    FeedbackService,
    GroceryService,
    IngredientService,
    IngredientValidationError,
    InventoryService,
    PlannerService,
    ProfileService,
    RecipeService,
    RecipeValidationError,
    SupplementService,
    SupplementValidationError,
)
from meal_planner.storage import Database, PlannedMeal


WEB_DIR = Path(__file__).resolve().parent
TEMPLATE_DIR = WEB_DIR / "templates"
STATIC_DIR = WEB_DIR / "static"


def _parse_week_start(raw_value: str | None) -> date:
    if raw_value:
        return start_of_week(datetime.fromisoformat(raw_value).date())
    return start_of_week(date.today())


def _clamp_int(value: int, minimum: int, maximum: int) -> int:
    return max(minimum, min(maximum, value))


def _base_context(request: Request, profile, unresolved_count: int) -> dict:
    return {
        "request": request,
        "today": date.today(),
        "profile": profile,
        "unresolved_count": unresolved_count,
    }


def _display_name(raw_value: object) -> object:
    if not isinstance(raw_value, str):
        return raw_value
    stripped = raw_value.strip()
    if stripped and stripped == stripped.lower():
        return stripped.title()
    return raw_value


def _ordered_meals(meals: object) -> object:
    if not isinstance(meals, list):
        return meals
    slot_order = {slot: index for index, slot in enumerate(MEAL_SLOT_ORDER)}
    return sorted(meals, key=lambda meal: (slot_order.get(getattr(meal, "meal_slot", ""), 999), getattr(meal, "id", 0)))


def _recipe_form_values(recipe=None, ingredient_lines: str = "", appliance_lines: str = "", form_data: dict | None = None) -> dict:
    if form_data is not None:
        return form_data
    if recipe is None:
        return {
            "name": "",
            "meal_slot": "dinner",
            "prep_minutes": 10,
            "cook_minutes": 0,
            "simplicity_score": 3,
            "pots_pans_score": 2,
            "servings": 1,
            "leftover_servings": 0,
            "calories": 400,
            "protein_g": 20,
            "carbs_g": 40,
            "fat_g": 15,
            "has_protein_component": True,
            "has_carb_component": True,
            "has_healthy_fat_component": False,
            "has_vegetable_component": False,
            "instructions": "",
            "notes": "",
            "ingredient_lines": ingredient_lines,
            "appliance_lines": appliance_lines,
        }
    return {
        "name": recipe.name,
        "meal_slot": recipe.meal_slot,
        "prep_minutes": recipe.prep_minutes,
        "cook_minutes": recipe.cook_minutes,
        "simplicity_score": recipe.simplicity_score,
        "pots_pans_score": recipe.pots_pans_score,
        "servings": recipe.servings,
        "leftover_servings": recipe.leftover_servings,
        "calories": recipe.calories,
        "protein_g": recipe.protein_g,
        "carbs_g": recipe.carbs_g,
        "fat_g": recipe.fat_g,
        "has_protein_component": recipe.has_protein_component,
        "has_carb_component": recipe.has_carb_component,
        "has_healthy_fat_component": recipe.has_healthy_fat_component,
        "has_vegetable_component": recipe.has_vegetable_component,
        "instructions": recipe.instructions,
        "notes": recipe.notes,
        "ingredient_lines": ingredient_lines,
        "appliance_lines": appliance_lines,
    }


def _ingredient_form_values(ingredient=None, form_data: dict | None = None) -> dict:
    if form_data is not None:
        return form_data
    if ingredient is None:
        return {"name": "", "default_unit": "count", "category": "Pantry"}
    return {
        "name": ingredient.name,
        "default_unit": ingredient.default_unit,
        "category": ingredient.category,
    }


def _supplement_form_values(supplement=None, form_data: dict | None = None) -> dict:
    if form_data is not None:
        return form_data
    if supplement is None:
        return {
            "name": "",
            "category": "Supplement",
            "recommended": False,
            "dosage": "",
            "notes": "",
        }
    return {
        "name": supplement.name,
        "category": supplement.category,
        "recommended": supplement.recommended,
        "dosage": supplement.dosage,
        "notes": supplement.notes,
    }


def create_app(database_path: Path | None = None) -> FastAPI:
    app = FastAPI(title="Meal Planner", version="0.1.0")
    app.state.database = Database(database_path=database_path)
    app.state.database.initialize()
    app.state.templates = Jinja2Templates(directory=str(TEMPLATE_DIR))
    app.state.templates.env.filters["display_name"] = _display_name
    app.state.templates.env.filters["ordered_meals"] = _ordered_meals
    app.state.ai_adapter = AIPlannerAdapter()
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")

    def _render_recipe_form(
        request: Request,
        recipe=None,
        form_values: dict | None = None,
        error_message: str | None = None,
        status_code: int = 200,
    ) -> object:
        with app.state.database.session() as session:
            recipe_service = RecipeService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            ingredient_lines = recipe_service.ingredient_line_text(recipe) if recipe is not None else ""
            appliance_lines = recipe_service.appliance_line_text(recipe) if recipe is not None else ""
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "recipe": recipe,
                    "form_values": _recipe_form_values(
                        recipe=recipe,
                        ingredient_lines=ingredient_lines,
                        appliance_lines=appliance_lines,
                        form_data=form_values,
                    ),
                    "error_message": error_message,
                    "available_ingredients": recipe_service.list_ingredients(),
                    "available_appliances": appliance_service.list_appliances(),
                }
            )
            return app.state.templates.TemplateResponse(request, "recipe_form.html", context, status_code=status_code)

    def _render_ingredient_form(
        request: Request,
        ingredient=None,
        form_values: dict | None = None,
        error_message: str | None = None,
        status_code: int = 200,
    ) -> object:
        with app.state.database.session() as session:
            ingredient_service = IngredientService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "ingredient": ingredient,
                    "form_values": _ingredient_form_values(ingredient=ingredient, form_data=form_values),
                    "error_message": error_message,
                    "categories": ingredient_service.categories(),
                }
            )
            return app.state.templates.TemplateResponse(request, "ingredient_form.html", context, status_code=status_code)

    def _render_supplement_form(
        request: Request,
        supplement=None,
        form_values: dict | None = None,
        error_message: str | None = None,
        status_code: int = 200,
    ) -> object:
        with app.state.database.session() as session:
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "supplement": supplement,
                    "form_values": _supplement_form_values(supplement=supplement, form_data=form_values),
                    "error_message": error_message,
                }
            )
            return app.state.templates.TemplateResponse(request, "supplement_form.html", context, status_code=status_code)

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
            recipe_service = RecipeService(session)
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
                    "recommended_supplements": SupplementService(session).recommended_supplements(),
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

    @app.get("/ingredients")
    async def ingredients(request: Request) -> object:
        with app.state.database.session() as session:
            ingredient_service = IngredientService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update({"ingredients": ingredient_service.list_ingredients()})
            return app.state.templates.TemplateResponse(request, "ingredients.html", context)

    @app.get("/ingredients/new")
    async def new_ingredient(request: Request) -> object:
        return _render_ingredient_form(request)

    @app.post("/ingredients/new")
    async def create_ingredient(request: Request) -> object:
        form = await request.form()
        form_values = {
            "name": str(form.get("name") or "").strip(),
            "default_unit": str(form.get("default_unit") or "").strip(),
            "category": str(form.get("category") or "").strip(),
        }
        try:
            with app.state.database.session() as session:
                ingredient = IngredientService(session).upsert_ingredient(form_values)
                GroceryService(session).generate_weekly_list(start_of_week(date.today()), regenerate=True)
            return RedirectResponse(url=f"/ingredients/{ingredient.id}/edit", status_code=303)
        except IngredientValidationError as exc:
            return _render_ingredient_form(request, form_values=form_values, error_message=str(exc), status_code=400)

    @app.get("/ingredients/{ingredient_id}/edit")
    async def edit_ingredient(ingredient_id: int, request: Request) -> object:
        with app.state.database.session() as session:
            ingredient = IngredientService(session).get_ingredient(ingredient_id)
        if ingredient is None:
            raise HTTPException(status_code=404)
        return _render_ingredient_form(request, ingredient=ingredient)

    @app.post("/ingredients/{ingredient_id}/edit")
    async def update_ingredient(ingredient_id: int, request: Request) -> object:
        form = await request.form()
        form_values = {
            "name": str(form.get("name") or "").strip(),
            "default_unit": str(form.get("default_unit") or "").strip(),
            "category": str(form.get("category") or "").strip(),
        }
        try:
            with app.state.database.session() as session:
                IngredientService(session).upsert_ingredient(form_values, ingredient_id=ingredient_id)
                GroceryService(session).generate_weekly_list(start_of_week(date.today()), regenerate=True)
            return RedirectResponse(url="/ingredients", status_code=303)
        except IngredientValidationError as exc:
            with app.state.database.session() as session:
                ingredient = IngredientService(session).get_ingredient(ingredient_id)
            if ingredient is None:
                raise HTTPException(status_code=404)
            return _render_ingredient_form(
                request,
                ingredient=ingredient,
                form_values=form_values,
                error_message=str(exc),
                status_code=400,
            )

    @app.get("/recipes")
    async def recipes(request: Request) -> object:
        with app.state.database.session() as session:
            recipe_service = RecipeService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update({"recipes": recipe_service.list_recipes()})
            return app.state.templates.TemplateResponse(request, "recipes.html", context)

    @app.get("/recipes/new")
    async def new_recipe(request: Request) -> object:
        return _render_recipe_form(request)

    @app.post("/recipes/new")
    async def create_recipe(request: Request) -> object:
        form = await request.form()
        form_values = {
            "name": str(form.get("name") or "").strip(),
            "meal_slot": str(form.get("meal_slot") or "dinner"),
            "prep_minutes": int(str(form.get("prep_minutes") or 0)),
            "cook_minutes": int(str(form.get("cook_minutes") or 0)),
            "simplicity_score": int(str(form.get("simplicity_score") or 3)),
            "pots_pans_score": int(str(form.get("pots_pans_score") or 2)),
            "servings": int(str(form.get("servings") or 1)),
            "leftover_servings": int(str(form.get("leftover_servings") or 0)),
            "calories": int(str(form.get("calories") or 0)),
            "protein_g": int(str(form.get("protein_g") or 0)),
            "carbs_g": int(str(form.get("carbs_g") or 0)),
            "fat_g": int(str(form.get("fat_g") or 0)),
            "has_protein_component": form.get("has_protein_component") is not None,
            "has_carb_component": form.get("has_carb_component") is not None,
            "has_healthy_fat_component": form.get("has_healthy_fat_component") is not None,
            "has_vegetable_component": form.get("has_vegetable_component") is not None,
            "instructions": str(form.get("instructions") or ""),
            "notes": str(form.get("notes") or ""),
            "ingredient_lines": str(form.get("ingredient_lines") or ""),
            "appliance_lines": str(form.get("appliance_lines") or ""),
        }
        try:
            with app.state.database.session() as session:
                recipe = RecipeService(session).upsert_recipe(
                    payload=form_values,
                    ingredient_lines=form_values["ingredient_lines"],
                    appliance_lines=form_values["appliance_lines"],
                )
            return RedirectResponse(url=f"/recipes/{recipe.id}", status_code=303)
        except RecipeValidationError as exc:
            return _render_recipe_form(request, form_values=form_values, error_message=str(exc), status_code=400)

    @app.get("/recipes/{recipe_id}")
    async def recipe_detail(recipe_id: int, request: Request) -> object:
        with app.state.database.session() as session:
            recipe_service = RecipeService(session)
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            recipe = recipe_service.get_recipe(recipe_id)
            if recipe is None:
                raise HTTPException(status_code=404)
            microwave_available = any(
                appliance.name.lower() == "microwave" and appliance.has_appliance
                for appliance in appliance_service.list_appliances()
            )
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(
                {
                    "recipe": recipe,
                    "leftover_reheat_steps": recipe_service.leftover_reheat_steps(recipe, microwave_available),
                }
            )
            return app.state.templates.TemplateResponse(request, "recipe_detail.html", context)

    @app.get("/recipes/{recipe_id}/edit")
    async def edit_recipe(recipe_id: int, request: Request) -> object:
        with app.state.database.session() as session:
            recipe = RecipeService(session).get_recipe(recipe_id)
        if recipe is None:
            raise HTTPException(status_code=404)
        return _render_recipe_form(request, recipe=recipe)

    @app.post("/recipes/{recipe_id}/edit")
    async def update_recipe(recipe_id: int, request: Request) -> object:
        form = await request.form()
        form_values = {
            "name": str(form.get("name") or "").strip(),
            "meal_slot": str(form.get("meal_slot") or "dinner"),
            "prep_minutes": int(str(form.get("prep_minutes") or 0)),
            "cook_minutes": int(str(form.get("cook_minutes") or 0)),
            "simplicity_score": int(str(form.get("simplicity_score") or 3)),
            "pots_pans_score": int(str(form.get("pots_pans_score") or 2)),
            "servings": int(str(form.get("servings") or 1)),
            "leftover_servings": int(str(form.get("leftover_servings") or 0)),
            "calories": int(str(form.get("calories") or 0)),
            "protein_g": int(str(form.get("protein_g") or 0)),
            "carbs_g": int(str(form.get("carbs_g") or 0)),
            "fat_g": int(str(form.get("fat_g") or 0)),
            "has_protein_component": form.get("has_protein_component") is not None,
            "has_carb_component": form.get("has_carb_component") is not None,
            "has_healthy_fat_component": form.get("has_healthy_fat_component") is not None,
            "has_vegetable_component": form.get("has_vegetable_component") is not None,
            "instructions": str(form.get("instructions") or ""),
            "notes": str(form.get("notes") or ""),
            "ingredient_lines": str(form.get("ingredient_lines") or ""),
            "appliance_lines": str(form.get("appliance_lines") or ""),
        }
        try:
            with app.state.database.session() as session:
                recipe_service = RecipeService(session)
                recipe = recipe_service.upsert_recipe(
                    payload=form_values,
                    ingredient_lines=form_values["ingredient_lines"],
                    appliance_lines=form_values["appliance_lines"],
                    recipe_id=recipe_id,
                )
                GroceryService(session).generate_weekly_list(start_of_week(date.today()), regenerate=True)
            return RedirectResponse(url=f"/recipes/{recipe.id}", status_code=303)
        except RecipeValidationError as exc:
            with app.state.database.session() as session:
                recipe = RecipeService(session).get_recipe(recipe_id)
            if recipe is None:
                raise HTTPException(status_code=404)
            return _render_recipe_form(
                request,
                recipe=recipe,
                form_values=form_values,
                error_message=str(exc),
                status_code=400,
            )

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
            profile = profile_service.get_profile()
            grocery_list = grocery_service.get_weekly_list(week_start)
            shopping_rows = grocery_service.shopping_rows(week_start)
            context = _base_context(request, profile, len(appliance_service.unresolved()))
            context.update(
                {
                    "week_start": week_start,
                    "grocery_horizon_days": grocery_service.grocery_horizon_days(),
                    "grocery_list": grocery_list,
                    "shopping_rows": shopping_rows,
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

    @app.post("/groceries/items/{grocery_item_id}/mark-on-hand")
    async def mark_grocery_item_on_hand(grocery_item_id: int, request: Request) -> RedirectResponse:
        form = await request.form()
        week_start = _parse_week_start(str(form.get("week_start")))
        with app.state.database.session() as session:
            grocery_service = GroceryService(session)
            marked_item = grocery_service.mark_item_on_hand(grocery_item_id)
            target_week = week_start
            if marked_item is not None and marked_item.grocery_list is not None:
                target_week = marked_item.grocery_list.week_start
            grocery_service.generate_weekly_list(target_week, regenerate=True)
        return RedirectResponse(url=f"/groceries?start={target_week.isoformat()}", status_code=303)

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
                    "grocery_horizon_days": grocery_service.grocery_horizon_days(),
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
            "shopping_frequency_days": _clamp_int(int(str(form.get("shopping_frequency_days") or 7)), 1, 7),
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

    @app.post("/profile/appliances/add")
    async def add_appliance(request: Request) -> RedirectResponse:
        form = await request.form()
        appliance_name = str(form.get("name") or "")
        with app.state.database.session() as session:
            ApplianceService(session).add_appliance(appliance_name, has_it=True)
            week_start = start_of_week(date.today())
            PlannerService(session).generate_week_plan(week_start, regenerate=True)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url="/profile", status_code=303)

    @app.post("/profile/appliances/{appliance_id}/availability")
    async def update_appliance_availability(appliance_id: int, request: Request) -> RedirectResponse:
        form = await request.form()
        has_it = str(form.get("has_it") or "false").lower() == "true"
        with app.state.database.session() as session:
            ApplianceService(session).set_availability(appliance_id, has_it)
            week_start = start_of_week(date.today())
            PlannerService(session).generate_week_plan(week_start, regenerate=True)
            GroceryService(session).generate_weekly_list(week_start, regenerate=True)
        return RedirectResponse(url="/profile", status_code=303)

    @app.get("/feedback")
    async def feedback_dashboard(request: Request) -> object:
        with app.state.database.session() as session:
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            supplement_service = SupplementService(session)
            feedback_service = FeedbackService(session, app.state.ai_adapter)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update(feedback_service.dashboard_context())
            context.update({"supplements": supplement_service.list_supplements()})
            return app.state.templates.TemplateResponse(request, "feedback.html", context)

    @app.get("/supplements")
    async def supplements(request: Request) -> object:
        with app.state.database.session() as session:
            profile_service = ProfileService(session)
            appliance_service = ApplianceService(session)
            supplement_service = SupplementService(session)
            context = _base_context(request, profile_service.get_profile(), len(appliance_service.unresolved()))
            context.update({"supplements": supplement_service.list_supplements()})
            return app.state.templates.TemplateResponse(request, "supplements.html", context)

    @app.get("/supplements/new")
    async def new_supplement(request: Request) -> object:
        return _render_supplement_form(request)

    @app.post("/supplements/new")
    async def create_supplement(request: Request) -> object:
        form = await request.form()
        form_values = {
            "name": str(form.get("name") or "").strip(),
            "category": str(form.get("category") or "").strip(),
            "recommended": form.get("recommended") is not None,
            "dosage": str(form.get("dosage") or "").strip(),
            "notes": str(form.get("notes") or "").strip(),
        }
        try:
            with app.state.database.session() as session:
                supplement = SupplementService(session).upsert_supplement(form_values)
            return RedirectResponse(url=f"/supplements/{supplement.id}/edit", status_code=303)
        except SupplementValidationError as exc:
            return _render_supplement_form(request, form_values=form_values, error_message=str(exc), status_code=400)

    @app.get("/supplements/{supplement_id}/edit")
    async def edit_supplement(supplement_id: int, request: Request) -> object:
        with app.state.database.session() as session:
            supplement = SupplementService(session).get_supplement(supplement_id)
        if supplement is None:
            raise HTTPException(status_code=404)
        return _render_supplement_form(request, supplement=supplement)

    @app.post("/supplements/{supplement_id}/edit")
    async def update_supplement(supplement_id: int, request: Request) -> object:
        form = await request.form()
        form_values = {
            "name": str(form.get("name") or "").strip(),
            "category": str(form.get("category") or "").strip(),
            "recommended": form.get("recommended") is not None,
            "dosage": str(form.get("dosage") or "").strip(),
            "notes": str(form.get("notes") or "").strip(),
        }
        try:
            with app.state.database.session() as session:
                SupplementService(session).upsert_supplement(form_values, supplement_id=supplement_id)
            return RedirectResponse(url="/supplements", status_code=303)
        except SupplementValidationError as exc:
            with app.state.database.session() as session:
                supplement = SupplementService(session).get_supplement(supplement_id)
            if supplement is None:
                raise HTTPException(status_code=404)
            return _render_supplement_form(
                request,
                supplement=supplement,
                form_values=form_values,
                error_message=str(exc),
                status_code=400,
            )

    @app.post("/feedback/supplements/{supplement_id}")
    async def supplement_feedback(supplement_id: int, request: Request) -> RedirectResponse:
        form = await request.form()
        with app.state.database.session() as session:
            SupplementService(session).add_feedback(
                supplement_id=supplement_id,
                rating=int(str(form.get("rating") or 3)),
                notes=str(form.get("notes") or ""),
            )
        return RedirectResponse(url="/feedback", status_code=303)

    return app
