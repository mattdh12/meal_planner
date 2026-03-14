from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
import math
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from meal_planner.ai import AIPlannerAdapter
from meal_planner.domain import (
    MEAL_SLOT_ORDER,
    InventoryEventType,
    InventoryItemType,
    InventoryLocation,
    MealSlot,
    daterange,
    start_of_week,
)
from meal_planner.planning import (
    build_slot_targets,
    choose_best_recipe,
    compute_nutrition_targets,
    current_inventory_by_ingredient,
    inventory_coverage,
    known_appliance_map,
    max_planned_servings,
    recipe_candidates,
    recipe_feedback_score,
    recent_recipe_counts,
    recommended_servings,
    score_recipe,
    slot_calorie_ceiling,
)
from meal_planner.settings import DEFAULT_STORE
from meal_planner.store_catalog import get_wegmans_product_reference
from meal_planner.storage import (
    Appliance,
    GroceryList,
    GroceryListItem,
    Ingredient,
    InventoryEvent,
    InventoryItem,
    MealPlanDay,
    PlannedMeal,
    PrepTask,
    Recipe,
    RecipeAppliance,
    RecipeFeedback,
    RecipeIngredient,
    Supplement,
    SupplementFeedback,
    UserProfile,
)


class RecipeValidationError(ValueError):
    pass


class IngredientValidationError(ValueError):
    pass


class SupplementValidationError(ValueError):
    pass


class ProfileService:
    def __init__(self, session: Session):
        self.session = session

    def get_profile(self) -> UserProfile:
        return self.session.query(UserProfile).first()

    def update_profile(self, payload: dict[str, Any]) -> UserProfile:
        profile = self.get_profile()
        for key, value in payload.items():
            if hasattr(profile, key):
                setattr(profile, key, value)
        self.session.flush()
        return profile


class ApplianceService:
    def __init__(self, session: Session):
        self.session = session

    def list_appliances(self) -> list[Appliance]:
        return self.session.query(Appliance).order_by(Appliance.name.asc()).all()

    def add_appliance(self, name: str, has_it: bool = True) -> Appliance | None:
        cleaned = name.strip()
        if not cleaned:
            return None
        appliance = self.session.query(Appliance).filter(func.lower(Appliance.name) == cleaned.lower()).one_or_none()
        if appliance is None:
            appliance = Appliance(name=cleaned, has_appliance=has_it, is_known=True)
            self.session.add(appliance)
        else:
            appliance.has_appliance = has_it
            appliance.is_known = True
        self.session.flush()
        return appliance

    def set_availability(self, appliance_id: int, has_it: bool) -> Appliance | None:
        appliance = self.session.get(Appliance, appliance_id)
        if appliance is None:
            return None
        appliance.has_appliance = has_it
        self.session.flush()
        return appliance

    def resolve_unknown_appliance(self, name: str, has_it: bool) -> Appliance:
        appliance = self.session.query(Appliance).filter(func.lower(Appliance.name) == name.lower()).one_or_none()
        if appliance is None:
            appliance = Appliance(name=name, has_appliance=has_it, is_known=False)
            self.session.add(appliance)
        else:
            appliance.has_appliance = has_it
        self.session.flush()
        return appliance

    def unresolved(self) -> list[Appliance]:
        return self.session.query(Appliance).filter(Appliance.has_appliance.is_(None)).order_by(Appliance.name.asc()).all()


class RecipeService:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _display_quantity(quantity: float) -> str:
        if float(quantity).is_integer():
            return str(int(quantity))
        return f"{quantity:g}"

    def list_recipes(self) -> list[Recipe]:
        return (
            self.session.query(Recipe)
            .options(
                joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient),
                joinedload(Recipe.appliances),
                joinedload(Recipe.feedback_entries),
            )
            .order_by(Recipe.meal_slot.asc(), Recipe.name.asc())
            .all()
        )

    def get_recipe(self, recipe_id: int) -> Recipe | None:
        return (
            self.session.query(Recipe)
            .options(
                joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient),
                joinedload(Recipe.appliances),
                joinedload(Recipe.feedback_entries),
            )
            .filter(Recipe.id == recipe_id)
            .one_or_none()
        )

    def list_ingredients(self) -> list[Ingredient]:
        return self.session.query(Ingredient).order_by(Ingredient.name.asc()).all()

    def ingredient_line_text(self, recipe: Recipe | None) -> str:
        if recipe is None:
            return ""
        return "\n".join(
            f"{row.ingredient.name} | {self._display_quantity(row.quantity)} | {row.unit}"
            for row in recipe.ingredients
        )

    def appliance_line_text(self, recipe: Recipe | None) -> str:
        if recipe is None:
            return ""
        return "\n".join(appliance.appliance_name for appliance in recipe.appliances)

    def recipe_options_by_slot(self) -> dict[str, list[Recipe]]:
        grouped: dict[str, list[Recipe]] = defaultdict(list)
        for recipe in self.list_recipes():
            grouped[recipe.meal_slot].append(recipe)
        return grouped

    def upsert_recipe(
        self,
        payload: dict[str, Any],
        ingredient_lines: str,
        appliance_lines: str,
        recipe_id: int | None = None,
    ) -> Recipe:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise RecipeValidationError("Recipe name is required.")

        recipe = self.session.get(Recipe, recipe_id) if recipe_id is not None else None
        duplicate = (
            self.session.query(Recipe)
            .filter(func.lower(Recipe.name) == name.lower())
            .one_or_none()
        )
        if duplicate is not None and (recipe is None or duplicate.id != recipe.id):
            raise RecipeValidationError("A recipe with that name already exists.")

        if recipe is None:
            recipe = Recipe(name=name)
            self.session.add(recipe)
            self.session.flush()

        recipe.name = name
        recipe.meal_slot = str(payload.get("meal_slot") or MealSlot.DINNER.value)
        recipe.prep_minutes = max(0, int(payload.get("prep_minutes") or 0))
        recipe.cook_minutes = max(0, int(payload.get("cook_minutes") or 0))
        recipe.simplicity_score = max(1, min(5, int(payload.get("simplicity_score") or 3)))
        recipe.pots_pans_score = max(1, min(5, int(payload.get("pots_pans_score") or 2)))
        recipe.servings = max(1, int(payload.get("servings") or 1))
        recipe.leftover_servings = max(0, int(payload.get("leftover_servings") or 0))
        recipe.calories = max(0, int(payload.get("calories") or 0))
        recipe.protein_g = max(0, int(payload.get("protein_g") or 0))
        recipe.carbs_g = max(0, int(payload.get("carbs_g") or 0))
        recipe.fat_g = max(0, int(payload.get("fat_g") or 0))
        recipe.has_protein_component = bool(payload.get("has_protein_component"))
        recipe.has_carb_component = bool(payload.get("has_carb_component"))
        recipe.has_healthy_fat_component = bool(payload.get("has_healthy_fat_component"))
        recipe.has_vegetable_component = bool(payload.get("has_vegetable_component"))
        recipe.instructions = str(payload.get("instructions") or "").strip()
        recipe.notes = str(payload.get("notes") or "").strip()

        parsed_ingredients = self._parse_ingredient_lines(ingredient_lines)
        parsed_appliances = self._parse_appliance_lines(appliance_lines)

        self.session.flush()
        self.session.query(RecipeIngredient).filter(RecipeIngredient.recipe_id == recipe.id).delete()
        self.session.query(RecipeAppliance).filter(RecipeAppliance.recipe_id == recipe.id).delete()
        self.session.flush()

        for ingredient_row in parsed_ingredients:
            ingredient = self._get_or_create_ingredient(
                ingredient_row["name"],
                ingredient_row["unit"],
            )
            self.session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    ingredient_id=ingredient.id,
                    quantity=ingredient_row["quantity"],
                    unit=ingredient_row["unit"],
                )
            )

        for appliance_name in parsed_appliances:
            self._ensure_appliance_exists(appliance_name)
            self.session.add(RecipeAppliance(recipe_id=recipe.id, appliance_name=appliance_name))

        self._sync_planned_meal_titles(recipe)
        self.session.flush()
        return recipe

    def _parse_ingredient_lines(self, raw_text: str) -> list[dict[str, Any]]:
        lines = [line.strip() for line in raw_text.splitlines() if line.strip()]
        if not lines:
            raise RecipeValidationError("Add at least one ingredient line.")

        parsed: list[dict[str, Any]] = []
        seen_names: set[str] = set()
        for line in lines:
            parts = [part.strip() for part in line.split("|")]
            if len(parts) != 3:
                raise RecipeValidationError(
                    "Each ingredient line must use: Ingredient name | quantity | unit"
                )

            name, quantity_text, unit = parts
            if not name or not unit:
                raise RecipeValidationError("Ingredient lines need a name and a unit.")
            try:
                quantity = float(quantity_text)
            except ValueError as exc:
                raise RecipeValidationError(f"Could not read quantity in ingredient line: {line}") from exc
            if quantity <= 0:
                raise RecipeValidationError("Ingredient quantities must be greater than zero.")

            ingredient_key = name.lower()
            if ingredient_key in seen_names:
                raise RecipeValidationError(f"Ingredient appears more than once: {name}")
            seen_names.add(ingredient_key)
            parsed.append({"name": name, "quantity": quantity, "unit": unit})
        return parsed

    @staticmethod
    def _parse_appliance_lines(raw_text: str) -> list[str]:
        seen: set[str] = set()
        appliances: list[str] = []
        for line in raw_text.splitlines():
            cleaned = line.strip()
            if not cleaned:
                continue
            key = cleaned.lower()
            if key in seen:
                continue
            seen.add(key)
            appliances.append(cleaned)
        return appliances

    def _get_or_create_ingredient(self, name: str, unit: str) -> Ingredient:
        ingredient = self.session.query(Ingredient).filter(func.lower(Ingredient.name) == name.lower()).one_or_none()
        if ingredient is None:
            ingredient = Ingredient(name=name, default_unit=unit, category="Pantry")
            self.session.add(ingredient)
            self.session.flush()
        elif not ingredient.default_unit:
            ingredient.default_unit = unit
        return ingredient

    def _ensure_appliance_exists(self, appliance_name: str) -> Appliance:
        appliance = self.session.query(Appliance).filter(func.lower(Appliance.name) == appliance_name.lower()).one_or_none()
        if appliance is None:
            appliance = Appliance(name=appliance_name, has_appliance=None, is_known=False)
            self.session.add(appliance)
            self.session.flush()
        return appliance

    def _sync_planned_meal_titles(self, recipe: Recipe) -> None:
        planned_meals = self.session.query(PlannedMeal).filter(PlannedMeal.recipe_id == recipe.id).all()
        for planned_meal in planned_meals:
            if planned_meal.uses_leftovers:
                planned_meal.title = f"{recipe.name} leftovers"
            else:
                planned_meal.title = recipe.name

    def add_feedback(self, recipe_id: int, tasty_rating: int, ease_rating: int, notes: str) -> None:
        self.session.add(
            RecipeFeedback(
                recipe_id=recipe_id,
                tasty_rating=tasty_rating,
                ease_rating=ease_rating,
                notes=notes,
            )
        )
        self.session.flush()

    def list_supplements(self) -> list[Supplement]:
        return (
            self.session.query(Supplement)
            .options(joinedload(Supplement.feedback_entries))
            .order_by(Supplement.recommended.desc(), Supplement.name.asc())
            .all()
        )

    def recommended_supplements(self) -> list[Supplement]:
        return [supplement for supplement in self.list_supplements() if supplement.recommended]

    @staticmethod
    def leftover_reheat_steps(recipe: Recipe | None, microwave_available: bool) -> list[str]:
        if recipe is None or recipe.leftover_servings <= 0 or not microwave_available:
            return []
        return [
            "Move one leftover serving into a microwave-safe bowl or plate.",
            "Cover it loosely so the food does not dry out.",
            "Microwave for 2 minutes.",
            "Stir the center so the heat spreads evenly.",
            "Microwave for 1 more minute.",
            "Let it sit for 1 minute before eating.",
        ]

    def add_supplement_feedback(self, supplement_id: int, rating: int, notes: str) -> None:
        self.session.add(
            SupplementFeedback(
                supplement_id=supplement_id,
                rating=rating,
                notes=notes,
            )
        )
        self.session.flush()


class IngredientService:
    def __init__(self, session: Session):
        self.session = session

    def list_ingredients(self) -> list[Ingredient]:
        return self.session.query(Ingredient).order_by(Ingredient.name.asc()).all()

    def get_ingredient(self, ingredient_id: int) -> Ingredient | None:
        return self.session.get(Ingredient, ingredient_id)

    def categories(self) -> list[str]:
        categories = [
            category
            for category, in self.session.query(Ingredient.category).distinct().order_by(Ingredient.category.asc()).all()
            if category
        ]
        if "Pantry" not in categories:
            categories.append("Pantry")
        return categories

    def upsert_ingredient(self, payload: dict[str, Any], ingredient_id: int | None = None) -> Ingredient:
        name = str(payload.get("name") or "").strip()
        default_unit = str(payload.get("default_unit") or "").strip()
        category = str(payload.get("category") or "").strip()

        if not name:
            raise IngredientValidationError("Ingredient name is required.")
        if not default_unit:
            raise IngredientValidationError("Default unit is required.")
        if not category:
            raise IngredientValidationError("Category is required.")

        ingredient = self.session.get(Ingredient, ingredient_id) if ingredient_id is not None else None
        duplicate = (
            self.session.query(Ingredient)
            .filter(func.lower(Ingredient.name) == name.lower())
            .one_or_none()
        )
        if duplicate is not None and (ingredient is None or duplicate.id != ingredient.id):
            raise IngredientValidationError("An ingredient with that name already exists.")

        if ingredient is None:
            ingredient = Ingredient(name=name)
            self.session.add(ingredient)
            self.session.flush()

        previous_name = ingredient.name
        ingredient.name = name
        ingredient.default_unit = default_unit
        ingredient.category = category
        self.session.flush()

        if previous_name != ingredient.name:
            for inventory_item in self.session.query(InventoryItem).filter(InventoryItem.ingredient_id == ingredient.id).all():
                inventory_item.name = ingredient.name
            for grocery_item in self.session.query(GroceryListItem).filter(GroceryListItem.ingredient_name == previous_name).all():
                grocery_item.ingredient_name = ingredient.name
        self.session.flush()
        return ingredient


class SupplementService:
    def __init__(self, session: Session):
        self.session = session

    def list_supplements(self) -> list[Supplement]:
        return (
            self.session.query(Supplement)
            .options(joinedload(Supplement.feedback_entries))
            .order_by(Supplement.recommended.desc(), Supplement.name.asc())
            .all()
        )

    def get_supplement(self, supplement_id: int) -> Supplement | None:
        return (
            self.session.query(Supplement)
            .options(joinedload(Supplement.feedback_entries))
            .filter(Supplement.id == supplement_id)
            .one_or_none()
        )

    def recommended_supplements(self) -> list[Supplement]:
        return [supplement for supplement in self.list_supplements() if supplement.recommended]

    def upsert_supplement(self, payload: dict[str, Any], supplement_id: int | None = None) -> Supplement:
        name = str(payload.get("name") or "").strip()
        if not name:
            raise SupplementValidationError("Supplement name is required.")

        supplement = self.session.get(Supplement, supplement_id) if supplement_id is not None else None
        duplicate = (
            self.session.query(Supplement)
            .filter(func.lower(Supplement.name) == name.lower())
            .one_or_none()
        )
        if duplicate is not None and (supplement is None or duplicate.id != supplement.id):
            raise SupplementValidationError("A supplement with that name already exists.")

        if supplement is None:
            supplement = Supplement(name=name)
            self.session.add(supplement)
            self.session.flush()

        supplement.name = name
        supplement.category = str(payload.get("category") or "Supplement").strip() or "Supplement"
        supplement.recommended = bool(payload.get("recommended"))
        supplement.dosage = str(payload.get("dosage") or "").strip()
        supplement.notes = str(payload.get("notes") or "").strip()
        self.session.flush()
        return supplement

    def add_feedback(self, supplement_id: int, rating: int, notes: str) -> None:
        self.session.add(
            SupplementFeedback(
                supplement_id=supplement_id,
                rating=rating,
                notes=notes,
            )
        )
        self.session.flush()


class InventoryService:
    def __init__(self, session: Session):
        self.session = session

    def grouped_items(self) -> dict[str, list[InventoryItem]]:
        items = self.session.query(InventoryItem).order_by(InventoryItem.location.asc(), InventoryItem.name.asc()).all()
        grouped: dict[str, list[InventoryItem]] = defaultdict(list)
        for item in items:
            grouped[item.location].append(item)
        return grouped

    def adjust_inventory_item(
        self,
        item_id_or_name: str,
        quantity_delta_or_set: float,
        location: str,
        reason: str,
        mode: str = "delta",
        unit: str = "count",
        item_type: str = InventoryItemType.INGREDIENT.value,
    ) -> InventoryItem:
        item = None
        if item_id_or_name.isdigit():
            item = self.session.get(InventoryItem, int(item_id_or_name))
        if item is None:
            item = self.session.query(InventoryItem).filter(
                func.lower(InventoryItem.name) == item_id_or_name.lower(),
                InventoryItem.location == location,
            ).one_or_none()

        ingredient = self.session.query(Ingredient).filter(func.lower(Ingredient.name) == item_id_or_name.lower()).one_or_none()
        if item is None:
            if ingredient is None:
                ingredient = Ingredient(name=item_id_or_name, default_unit=unit, category="Pantry")
                self.session.add(ingredient)
                self.session.flush()
            item = InventoryItem(
                name=ingredient.name,
                item_type=item_type,
                ingredient_id=ingredient.id,
                location=location,
                quantity=0,
                unit=unit,
            )
            self.session.add(item)
            self.session.flush()

        before = item.quantity
        if mode == "set":
            item.quantity = max(0, quantity_delta_or_set)
        else:
            item.quantity = max(0, item.quantity + quantity_delta_or_set)
        item.location = location
        item.unit = unit

        event_type = InventoryEventType.MANUAL_CORRECTION.value if reason != "Grocery restock" else InventoryEventType.GROCERY_RESTOCK.value
        self.session.add(
            InventoryEvent(
                inventory_item_name=item.name,
                ingredient_name=item.name,
                event_type=event_type,
                quantity_delta=item.quantity - before,
                unit=item.unit,
                location=item.location,
                reason=reason,
            )
        )
        self.session.flush()
        return item

    def record_meal_completed(self, planned_meal_id: int, leftovers_cap: int) -> None:
        planned_meal = (
            self.session.query(PlannedMeal)
            .options(joinedload(PlannedMeal.recipe).joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient))
            .filter(PlannedMeal.id == planned_meal_id)
            .one_or_none()
        )
        if not planned_meal or planned_meal.completed:
            return

        planned_meal.completed = True
        recipe = planned_meal.recipe
        if recipe is None:
            return

        if planned_meal.uses_leftovers:
            leftover_name = f"Leftover: {recipe.name}"
            leftover_item = (
                self.session.query(InventoryItem)
                .filter(InventoryItem.name == leftover_name, InventoryItem.item_type == InventoryItemType.LEFTOVER.value)
                .one_or_none()
            )
            if leftover_item:
                leftover_item.quantity = max(0, leftover_item.quantity - 1)
                self.session.add(
                    InventoryEvent(
                        inventory_item_name=leftover_item.name,
                        ingredient_name=leftover_item.name,
                        event_type=InventoryEventType.LEFTOVER_CONSUMED.value,
                        quantity_delta=-1,
                        unit=leftover_item.unit,
                        location=leftover_item.location,
                        reason="Used for planned leftovers lunch.",
                        related_planned_meal_id=planned_meal.id,
                    )
                )
        else:
            for recipe_ingredient in recipe.ingredients:
                remaining = recipe_ingredient.quantity * max(planned_meal.planned_servings, 1)
                items = (
                    self.session.query(InventoryItem)
                    .filter(
                        InventoryItem.ingredient_id == recipe_ingredient.ingredient_id,
                        InventoryItem.item_type == InventoryItemType.INGREDIENT.value,
                        InventoryItem.quantity > 0,
                    )
                    .order_by(InventoryItem.location.asc())
                    .all()
                )
                for item in items:
                    if remaining <= 0:
                        break
                    deduction = min(item.quantity, remaining)
                    item.quantity -= deduction
                    remaining -= deduction
                    self.session.add(
                        InventoryEvent(
                            inventory_item_name=item.name,
                            ingredient_name=item.name,
                            event_type=InventoryEventType.MEAL_COMPLETED.value,
                            quantity_delta=-deduction,
                            unit=item.unit,
                            location=item.location,
                            reason=f"Meal completed: {recipe.name}",
                            related_planned_meal_id=planned_meal.id,
                        )
                    )

            if recipe.leftover_servings and planned_meal.meal_slot == MealSlot.DINNER.value:
                leftover_quantity = min(recipe.leftover_servings * max(planned_meal.planned_servings, 1), leftovers_cap)
                leftover_name = f"Leftover: {recipe.name}"
                leftover = (
                    self.session.query(InventoryItem)
                    .filter(InventoryItem.name == leftover_name, InventoryItem.item_type == InventoryItemType.LEFTOVER.value)
                    .one_or_none()
                )
                if leftover is None:
                    leftover = InventoryItem(
                        name=leftover_name,
                        item_type=InventoryItemType.LEFTOVER.value,
                        recipe_id=recipe.id,
                        location=InventoryLocation.FRIDGE.value,
                        quantity=0,
                        unit="servings",
                    )
                    self.session.add(leftover)
                leftover.quantity += leftover_quantity
                self.session.add(
                    InventoryEvent(
                        inventory_item_name=leftover.name,
                        ingredient_name=leftover.name,
                        event_type=InventoryEventType.LEFTOVER_CREATED.value,
                        quantity_delta=leftover_quantity,
                        unit=leftover.unit,
                        location=leftover.location,
                        reason=f"Created after making {recipe.name}",
                        related_planned_meal_id=planned_meal.id,
                    )
                )
        self.session.flush()

    def recent_events(self, limit: int = 12) -> list[InventoryEvent]:
        return self.session.query(InventoryEvent).order_by(InventoryEvent.created_at.desc()).limit(limit).all()

    def record_grocery_purchase(self, item_name: str, quantity: float, unit: str, location: str) -> InventoryItem:
        return self.adjust_inventory_item(
            item_name,
            quantity,
            location,
            "Grocery restock",
            mode="delta",
            unit=unit,
        )


class PlannerService:
    def __init__(self, session: Session):
        self.session = session
        self.profile_service = ProfileService(session)

    def _get_or_create_day(self, target_date: date) -> MealPlanDay:
        day = self.session.query(MealPlanDay).filter(MealPlanDay.plan_date == target_date).one_or_none()
        if day is None:
            day = MealPlanDay(plan_date=target_date)
            self.session.add(day)
            self.session.flush()
        return day

    def generate_day_plan(self, target_date: date) -> MealPlanDay:
        week_start = start_of_week(target_date)
        self.generate_week_plan(week_start)
        day = (
            self.session.query(MealPlanDay)
            .options(joinedload(MealPlanDay.meals).joinedload(PlannedMeal.recipe), joinedload(MealPlanDay.prep_tasks))
            .filter(MealPlanDay.plan_date == target_date)
            .one()
        )
        self._rebalance_day_calories(day)
        return (
            self.session.query(MealPlanDay)
            .options(joinedload(MealPlanDay.meals).joinedload(PlannedMeal.recipe), joinedload(MealPlanDay.prep_tasks))
            .filter(MealPlanDay.plan_date == target_date)
            .one()
        )

    def prep_tasks_for_date(self, target_date: date) -> list[PrepTask]:
        return (
            self.session.query(PrepTask)
            .join(MealPlanDay, PrepTask.meal_plan_day_id == MealPlanDay.id)
            .options(joinedload(PrepTask.day))
            .filter(PrepTask.due_date == target_date)
            .order_by(MealPlanDay.plan_date.asc(), PrepTask.id.asc())
            .all()
        )

    def generate_week_plan(self, week_start: date, regenerate: bool = False) -> list[MealPlanDay]:
        profile = self.profile_service.get_profile()
        if regenerate:
            days = self.session.query(MealPlanDay).filter(
                MealPlanDay.plan_date >= week_start,
                MealPlanDay.plan_date < week_start + timedelta(days=7),
            ).all()
            for day in days:
                self.session.delete(day)
            self.session.flush()

        existing = self.session.query(MealPlanDay).filter(
            MealPlanDay.plan_date >= week_start,
            MealPlanDay.plan_date < week_start + timedelta(days=7),
        ).count()
        if existing == 7 and not regenerate:
            self._rebuild_prep_tasks(week_start)
            return (
                self.session.query(MealPlanDay)
                .options(joinedload(MealPlanDay.meals).joinedload(PlannedMeal.recipe), joinedload(MealPlanDay.prep_tasks))
                .filter(MealPlanDay.plan_date >= week_start, MealPlanDay.plan_date < week_start + timedelta(days=7))
                .order_by(MealPlanDay.plan_date.asc())
                .all()
            )

        leftovers_queue: list[tuple[int, str]] = []
        week_ingredient_counts: dict[int, int] = defaultdict(int)
        week_recipe_counts: dict[int, int] = defaultdict(int)
        previous_slot_recipe_ids: dict[str, int | None] = {
            MealSlot.LUNCH.value: None,
            MealSlot.DINNER.value: None,
        }
        for target_date in daterange(week_start, 7):
            day = self._get_or_create_day(target_date)
            self.session.query(PlannedMeal).filter(PlannedMeal.meal_plan_day_id == day.id).delete()
            self.session.query(PrepTask).filter(PrepTask.meal_plan_day_id == day.id).delete()

            for meal_slot in MEAL_SLOT_ORDER:
                if (
                    meal_slot == MealSlot.LUNCH.value
                    and leftovers_queue
                    and leftovers_queue[0][0] != previous_slot_recipe_ids[MealSlot.LUNCH.value]
                ):
                    recipe_id, title = leftovers_queue.pop(0)
                    self.session.add(
                        PlannedMeal(
                            meal_plan_day_id=day.id,
                            meal_slot=meal_slot,
                            recipe_id=recipe_id,
                            title=title,
                            planned_servings=1,
                            uses_leftovers=True,
                        )
                    )
                    previous_slot_recipe_ids[MealSlot.LUNCH.value] = recipe_id
                    continue

                scored = choose_best_recipe(
                    self.session,
                    profile,
                    meal_slot,
                    weekly_ingredient_counts=week_ingredient_counts,
                    weekly_recipe_counts=week_recipe_counts,
                    previous_slot_recipe_id=previous_slot_recipe_ids.get(meal_slot),
                )
                if scored is None:
                    self.session.add(
                        PlannedMeal(
                            meal_plan_day_id=day.id,
                            meal_slot=meal_slot,
                            recipe_id=None,
                            title=f"Manual {meal_slot.title()} plan",
                            planned_servings=1,
                            notes="No seeded recipe matched the current rules.",
                        )
                    )
                    if meal_slot in previous_slot_recipe_ids:
                        previous_slot_recipe_ids[meal_slot] = None
                    continue
                self.session.add(
                    PlannedMeal(
                        meal_plan_day_id=day.id,
                        meal_slot=meal_slot,
                        recipe_id=scored.recipe.id,
                        title=scored.recipe.name,
                        planned_servings=scored.planned_servings,
                        notes=(
                            f"Inventory coverage: {round(scored.inventory_coverage * 100)}% | "
                            f"{scored.planned_servings} serving{'s' if scored.planned_servings != 1 else ''} planned"
                        ),
                    )
                )
                if meal_slot in previous_slot_recipe_ids:
                    previous_slot_recipe_ids[meal_slot] = scored.recipe.id
                week_recipe_counts[scored.recipe.id] += 1
                for recipe_ingredient in scored.recipe.ingredients:
                    week_ingredient_counts[recipe_ingredient.ingredient_id] += 1
                if meal_slot == MealSlot.DINNER.value and scored.recipe.leftover_servings and profile.leftovers_cap > 0:
                    leftovers_queue.append((scored.recipe.id, f"{scored.recipe.name} leftovers"))
            self.session.flush()

        self._rebuild_prep_tasks(week_start)
        return (
            self.session.query(MealPlanDay)
            .options(joinedload(MealPlanDay.meals).joinedload(PlannedMeal.recipe), joinedload(MealPlanDay.prep_tasks))
            .filter(MealPlanDay.plan_date >= week_start, MealPlanDay.plan_date < week_start + timedelta(days=7))
            .order_by(MealPlanDay.plan_date.asc())
            .all()
        )

    def replace_planned_meal(self, planned_meal_id: int, recipe_id: int) -> None:
        planned_meal = self.session.get(PlannedMeal, planned_meal_id)
        recipe = self.session.get(Recipe, recipe_id)
        if planned_meal is None or recipe is None:
            return
        profile = self.profile_service.get_profile()
        slot_targets = build_slot_targets(profile, compute_nutrition_targets(profile))
        planned_servings = recommended_servings(recipe, slot_targets[planned_meal.meal_slot])
        planned_meal.recipe_id = recipe.id
        planned_meal.recipe = recipe
        planned_meal.title = recipe.name
        planned_meal.uses_leftovers = False
        planned_meal.planned_servings = planned_servings
        planned_meal.notes = (
            "Manually replaced from weekly plan. "
            f"{planned_servings} serving{'s' if planned_servings != 1 else ''} planned."
        )
        self.session.flush()
        self._rebuild_prep_tasks(start_of_week(planned_meal.day.plan_date))

    @staticmethod
    def planned_meal_calories(planned_meal: PlannedMeal) -> int | None:
        if planned_meal.recipe is None:
            return None
        return int(planned_meal.recipe.calories * max(planned_meal.planned_servings, 1))

    def planned_day_calories(self, day: MealPlanDay) -> int:
        return sum(self.planned_meal_calories(meal) or 0 for meal in day.meals)

    def _rebalance_day_calories(self, day: MealPlanDay) -> None:
        profile = self.profile_service.get_profile()
        nutrition = compute_nutrition_targets(profile)
        minimum_daily_calories = nutrition.calories - 250
        if self.planned_day_calories(day) >= minimum_daily_calories:
            return

        inventory = current_inventory_by_ingredient(self.session)
        feedback = recipe_feedback_score(self.session)
        recent_counts = recent_recipe_counts(self.session)
        appliance_state = known_appliance_map(self.session)
        slot_targets = build_slot_targets(profile, nutrition)
        slot_meals = {meal.meal_slot: meal for meal in day.meals}

        while self.planned_day_calories(day) < minimum_daily_calories:
            deficit = minimum_daily_calories - self.planned_day_calories(day)
            best_upgrade: dict[str, Any] | None = None

            for meal_slot in (MealSlot.BREAKFAST.value, MealSlot.SNACK.value):
                meal = slot_meals.get(meal_slot)
                if meal is None or meal.recipe is None or meal.uses_leftovers:
                    continue

                current_calories = self.planned_meal_calories(meal) or 0
                for recipe in recipe_candidates(self.session, meal_slot):
                    coverage = inventory_coverage(recipe, inventory)
                    for servings in range(1, max_planned_servings(meal_slot) + 1):
                        candidate_calories = recipe.calories * servings
                        if candidate_calories <= current_calories:
                            continue
                        if candidate_calories > slot_calorie_ceiling(meal_slot):
                            continue

                        candidate_score = score_recipe(
                            recipe=recipe,
                            slot_target=slot_targets[meal_slot],
                            coverage=coverage,
                            feedback_score=feedback.get(recipe.id, 3.0),
                            recent_count=recent_counts.get(recipe.id, 0),
                            appliance_state=appliance_state,
                            inventory_by_ingredient=inventory,
                            planned_servings_override=servings,
                        )
                        utility = candidate_score + min(candidate_calories - current_calories, deficit) * 0.2
                        if best_upgrade is None or utility > best_upgrade["utility"]:
                            best_upgrade = {
                                "meal": meal,
                                "recipe": recipe,
                                "servings": servings,
                                "utility": utility,
                                "calories": candidate_calories,
                            }

            if best_upgrade is None:
                break

            meal = best_upgrade["meal"]
            recipe = best_upgrade["recipe"]
            servings = best_upgrade["servings"]
            meal.recipe_id = recipe.id
            meal.recipe = recipe
            meal.title = recipe.name
            meal.uses_leftovers = False
            meal.planned_servings = servings
            meal.notes = (
                "Adjusted to better match your daily calorie goal. "
                f"{servings} serving{'s' if servings != 1 else ''} planned."
            )
            self.session.flush()

    def _rebuild_prep_tasks(self, week_start: date) -> None:
        days = (
            self.session.query(MealPlanDay)
            .options(
                joinedload(MealPlanDay.meals).joinedload(PlannedMeal.recipe).joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient)
            )
            .filter(MealPlanDay.plan_date >= week_start, MealPlanDay.plan_date < week_start + timedelta(days=7))
            .order_by(MealPlanDay.plan_date.asc())
            .all()
        )
        for day in days:
            self.session.query(PrepTask).filter(PrepTask.meal_plan_day_id == day.id).delete()
            for meal in day.meals:
                if meal.recipe is None or meal.uses_leftovers:
                    continue
                freezer_items = [
                    recipe_ingredient.ingredient.name
                    for recipe_ingredient in meal.recipe.ingredients
                    if self.session.query(InventoryItem)
                    .filter(
                        InventoryItem.ingredient_id == recipe_ingredient.ingredient_id,
                        InventoryItem.location == InventoryLocation.FREEZER.value,
                        InventoryItem.quantity > 0,
                    )
                    .count()
                ]
                if freezer_items:
                    due_date = day.plan_date - timedelta(days=1)
                    due_day_text = due_date.strftime("%A")
                    meal_day_text = day.plan_date.strftime("%A")
                    slot_text = meal.meal_slot.title()
                    self.session.add(
                        PrepTask(
                            meal_plan_day_id=day.id,
                            due_date=due_date,
                            description=(
                                f"On {due_day_text} evening, move {', '.join(freezer_items)} from the freezer to the fridge "
                                f"for tomorrow's {slot_text}: {meal.title} ({meal_day_text})."
                            ),
                        )
                    )
        self.session.flush()

    def today_overview(self, target_date: date) -> dict[str, Any]:
        day = self.generate_day_plan(target_date)
        profile = self.profile_service.get_profile()
        nutrition = compute_nutrition_targets(profile)
        slot_targets = build_slot_targets(profile, nutrition)
        planned_calories = self.planned_day_calories(day)
        return {
            "day": day,
            "profile": profile,
            "nutrition": nutrition,
            "slot_targets": slot_targets,
            "planned_calories": planned_calories,
            "calorie_gap": planned_calories - nutrition.calories,
        }


class GroceryService:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _display_quantity(quantity: float) -> str:
        rounded = round(quantity, 2)
        if float(rounded).is_integer():
            return str(int(rounded))
        return f"{rounded:g}"

    @staticmethod
    def suggested_location(section: str, ingredient_name: str) -> str:
        section_key = section.lower()
        ingredient_key = ingredient_name.lower()
        if section_key == "frozen":
            return InventoryLocation.FREEZER.value
        if section_key in {"dairy", "deli", "meat", "condiments"}:
            return InventoryLocation.FRIDGE.value
        if section_key == "produce":
            if any(keyword in ingredient_key for keyword in ("banana", "potato", "sweet potato", "avocado")):
                return InventoryLocation.PANTRY.value
            return InventoryLocation.FRIDGE.value
        return InventoryLocation.PANTRY.value

    def grocery_horizon_days(self) -> int:
        profile = ProfileService(self.session).get_profile()
        raw_value = getattr(profile, "shopping_frequency_days", 7) if profile is not None else 7
        return max(1, min(int(raw_value or 7), 7))

    def generate_weekly_list(self, week_start: date, regenerate: bool = False) -> GroceryList:
        profile = ProfileService(self.session).get_profile()
        coverage_end = week_start + timedelta(days=self.grocery_horizon_days())
        if regenerate:
            grocery_list = self.session.query(GroceryList).filter(GroceryList.week_start == week_start).one_or_none()
            if grocery_list:
                self.session.delete(grocery_list)
                self.session.flush()

        grocery_list = self.session.query(GroceryList).filter(GroceryList.week_start == week_start).one_or_none()
        if grocery_list is None:
            grocery_list = GroceryList(
                week_start=week_start,
                store_name=(profile.preferred_store.strip() if profile and profile.preferred_store else DEFAULT_STORE),
            )
            self.session.add(grocery_list)
            self.session.flush()
        else:
            grocery_list.store_name = profile.preferred_store.strip() if profile and profile.preferred_store else DEFAULT_STORE
            self.session.query(GroceryListItem).filter(GroceryListItem.grocery_list_id == grocery_list.id).delete()

        required: dict[tuple[int, str], float] = defaultdict(float)
        ingredient_lookup: dict[int, Ingredient] = {ingredient.id: ingredient for ingredient in self.session.query(Ingredient).all()}
        planned_meals = (
            self.session.query(PlannedMeal)
            .join(MealPlanDay, PlannedMeal.meal_plan_day_id == MealPlanDay.id)
            .options(joinedload(PlannedMeal.recipe).joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient))
            .filter(MealPlanDay.plan_date >= week_start, MealPlanDay.plan_date < coverage_end)
            .all()
        )
        for meal in planned_meals:
            if meal.recipe is None or meal.uses_leftovers:
                continue
            for recipe_ingredient in meal.recipe.ingredients:
                required[(recipe_ingredient.ingredient_id, recipe_ingredient.unit)] += (
                    recipe_ingredient.quantity * max(meal.planned_servings, 1)
                )

        on_hand: dict[int, float] = defaultdict(float)
        inventory_items = self.session.query(InventoryItem).filter(InventoryItem.item_type == InventoryItemType.INGREDIENT.value).all()
        for item in inventory_items:
            if item.ingredient_id is not None:
                on_hand[item.ingredient_id] += item.quantity

        for (ingredient_id, unit), required_quantity in required.items():
            missing = max(0, required_quantity - on_hand.get(ingredient_id, 0))
            if missing <= 0:
                continue
            ingredient = ingredient_lookup[ingredient_id]
            self.session.add(
                GroceryListItem(
                    grocery_list_id=grocery_list.id,
                    ingredient_name=ingredient.name,
                    quantity=missing,
                    unit=unit,
                    section=ingredient.category,
                )
            )
        self.session.flush()
        return grocery_list

    def get_weekly_list(self, week_start: date) -> GroceryList:
        grocery_list = self.generate_weekly_list(week_start)
        return self.session.query(GroceryList).options(joinedload(GroceryList.items)).filter(GroceryList.id == grocery_list.id).one()

    def shopping_rows(self, week_start: date) -> list[dict[str, Any]]:
        grocery_list = self.get_weekly_list(week_start)
        rows: list[dict[str, Any]] = []
        for item in sorted(grocery_list.items, key=lambda row: (row.section, row.ingredient_name)):
            reference = get_wegmans_product_reference(item.ingredient_name)
            if reference and reference.inventory_unit == item.unit:
                package_inventory_quantity = reference.inventory_quantity
                product_name = reference.product_name
                package_size_label = reference.package_size_label
                product_url = reference.product_url
                notes = reference.notes
                has_store_reference = True
            else:
                package_inventory_quantity = item.quantity
                product_name = item.ingredient_name
                package_size_label = f"enough for {self._display_quantity(item.quantity)} {item.unit}"
                product_url = None
                notes = "No curated Wegmans package is saved for this ingredient yet."
                has_store_reference = False

            recommended_packages = max(1, math.ceil(item.quantity / max(package_inventory_quantity, 0.01)))
            recommended_inventory_quantity = round(recommended_packages * package_inventory_quantity, 2)
            rows.append(
                {
                    "item": item,
                    "product_name": product_name,
                    "package_size_label": package_size_label,
                    "recommended_packages": recommended_packages,
                    "recommended_inventory_quantity": recommended_inventory_quantity,
                    "recommended_inventory_label": f"{self._display_quantity(recommended_inventory_quantity)} {item.unit}",
                    "shopping_label": f"{recommended_packages} x {product_name} ({package_size_label})",
                    "product_url": product_url,
                    "notes": notes,
                    "has_store_reference": has_store_reference,
                }
            )
        return rows

    def purchase_rows(self, week_start: date) -> list[dict[str, Any]]:
        rows: list[dict[str, Any]] = []
        for row in self.shopping_rows(week_start):
            item = row["item"]
            rows.append(
                {
                    "item": item,
                    "product_name": row["product_name"],
                    "package_size_label": row["package_size_label"],
                    "recommended_packages": row["recommended_packages"],
                    "recommended_inventory_quantity": row["recommended_inventory_quantity"],
                    "recommended_inventory_label": row["recommended_inventory_label"],
                    "shopping_label": row["shopping_label"],
                    "product_url": row["product_url"],
                    "notes": row["notes"],
                    "has_store_reference": row["has_store_reference"],
                    "default_location": self.suggested_location(item.section, item.ingredient_name),
                }
            )
        return rows

    def mark_item_on_hand(self, grocery_item_id: int) -> GroceryListItem | None:
        grocery_item = self.session.get(GroceryListItem, grocery_item_id)
        if grocery_item is None or grocery_item.quantity <= 0:
            return grocery_item

        inventory_service = InventoryService(self.session)
        inventory_service.adjust_inventory_item(
            grocery_item.ingredient_name,
            grocery_item.quantity,
            self.suggested_location(grocery_item.section, grocery_item.ingredient_name),
            "Marked on hand from grocery list",
            mode="delta",
            unit=grocery_item.unit,
        )
        self.session.flush()
        return grocery_item

    def apply_purchases(self, purchases: list[dict[str, Any]]) -> None:
        inventory_service = InventoryService(self.session)
        for purchase in purchases:
            quantity = float(purchase["quantity"])
            if quantity <= 0:
                continue
            inventory_service.record_grocery_purchase(
                item_name=purchase["item_name"],
                quantity=quantity,
                unit=purchase["unit"],
                location=purchase["location"],
            )


class FeedbackService:
    def __init__(self, session: Session, ai_adapter: AIPlannerAdapter):
        self.session = session
        self.ai_adapter = ai_adapter

    def dashboard_context(self) -> dict[str, Any]:
        recipe_feedback = (
            self.session.query(RecipeFeedback)
            .options(joinedload(RecipeFeedback.recipe))
            .order_by(RecipeFeedback.created_at.desc())
            .limit(8)
            .all()
        )
        supplement_feedback = (
            self.session.query(SupplementFeedback)
            .options(joinedload(SupplementFeedback.supplement))
            .order_by(SupplementFeedback.created_at.desc())
            .limit(8)
            .all()
        )
        unresolved_appliances = self.session.query(Appliance).filter(Appliance.has_appliance.is_(None)).all()
        low_feedback = [entry for entry in recipe_feedback if entry.tasty_rating <= 2 or entry.ease_rating <= 2]
        low_stock_items = [
            item.name
            for item in self.session.query(InventoryItem)
            .filter(InventoryItem.quantity < 2, InventoryItem.item_type == InventoryItemType.INGREDIENT.value)
            .all()
        ]
        suggestions = self.ai_adapter.suggest_plan_changes(
            {
                "low_feedback": low_feedback,
                "unresolved_appliances": unresolved_appliances,
                "shopping_notes": low_stock_items,
            }
        )
        return {
            "recipe_feedback": recipe_feedback,
            "supplement_feedback": supplement_feedback,
            "suggestions": suggestions,
        }
