from __future__ import annotations

from collections import defaultdict
from datetime import date, timedelta
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
from meal_planner.planning import build_slot_targets, choose_best_recipe, compute_nutrition_targets
from meal_planner.settings import DEFAULT_STORE
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
    RecipeFeedback,
    RecipeIngredient,
    Supplement,
    SupplementFeedback,
    UserProfile,
)


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

    def recipe_options_by_slot(self) -> dict[str, list[Recipe]]:
        grouped: dict[str, list[Recipe]] = defaultdict(list)
        for recipe in self.list_recipes():
            grouped[recipe.meal_slot].append(recipe)
        return grouped

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

    def add_supplement_feedback(self, supplement_id: int, rating: int, notes: str) -> None:
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
                remaining = recipe_ingredient.quantity
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
                leftover_quantity = min(recipe.leftover_servings, leftovers_cap)
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
        return (
            self.session.query(MealPlanDay)
            .options(joinedload(MealPlanDay.meals).joinedload(PlannedMeal.recipe), joinedload(MealPlanDay.prep_tasks))
            .filter(MealPlanDay.plan_date == target_date)
            .one()
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
            return (
                self.session.query(MealPlanDay)
                .options(joinedload(MealPlanDay.meals).joinedload(PlannedMeal.recipe), joinedload(MealPlanDay.prep_tasks))
                .filter(MealPlanDay.plan_date >= week_start, MealPlanDay.plan_date < week_start + timedelta(days=7))
                .order_by(MealPlanDay.plan_date.asc())
                .all()
            )

        leftovers_queue: list[tuple[int, str]] = []
        for target_date in daterange(week_start, 7):
            day = self._get_or_create_day(target_date)
            self.session.query(PlannedMeal).filter(PlannedMeal.meal_plan_day_id == day.id).delete()
            self.session.query(PrepTask).filter(PrepTask.meal_plan_day_id == day.id).delete()

            for meal_slot in MEAL_SLOT_ORDER:
                if meal_slot == MealSlot.LUNCH.value and leftovers_queue:
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
                    continue

                scored = choose_best_recipe(self.session, profile, meal_slot)
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
                    continue

                self.session.add(
                    PlannedMeal(
                        meal_plan_day_id=day.id,
                        meal_slot=meal_slot,
                        recipe_id=scored.recipe.id,
                        title=scored.recipe.name,
                        planned_servings=1,
                        notes=f"Inventory coverage: {round(scored.inventory_coverage * 100)}%",
                    )
                )
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
        planned_meal.recipe_id = recipe.id
        planned_meal.title = recipe.name
        planned_meal.uses_leftovers = False
        planned_meal.notes = "Manually replaced from weekly plan."
        self.session.flush()
        self._rebuild_prep_tasks(start_of_week(planned_meal.day.plan_date))

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
                    self.session.add(
                        PrepTask(
                            meal_plan_day_id=day.id,
                            due_date=day.plan_date - timedelta(days=1),
                            description=f"Defrost {', '.join(freezer_items)} for {meal.title} tomorrow.",
                        )
                    )
        self.session.flush()

    def today_overview(self, target_date: date) -> dict[str, Any]:
        day = self.generate_day_plan(target_date)
        profile = self.profile_service.get_profile()
        nutrition = compute_nutrition_targets(profile)
        slot_targets = build_slot_targets(profile, nutrition)
        return {"day": day, "profile": profile, "nutrition": nutrition, "slot_targets": slot_targets}


class GroceryService:
    def __init__(self, session: Session):
        self.session = session

    def generate_weekly_list(self, week_start: date, regenerate: bool = False) -> GroceryList:
        if regenerate:
            grocery_list = self.session.query(GroceryList).filter(GroceryList.week_start == week_start).one_or_none()
            if grocery_list:
                self.session.delete(grocery_list)
                self.session.flush()

        grocery_list = self.session.query(GroceryList).filter(GroceryList.week_start == week_start).one_or_none()
        if grocery_list is None:
            grocery_list = GroceryList(week_start=week_start, store_name=DEFAULT_STORE)
            self.session.add(grocery_list)
            self.session.flush()
        else:
            self.session.query(GroceryListItem).filter(GroceryListItem.grocery_list_id == grocery_list.id).delete()

        required: dict[tuple[int, str], float] = defaultdict(float)
        ingredient_lookup: dict[int, Ingredient] = {ingredient.id: ingredient for ingredient in self.session.query(Ingredient).all()}
        planned_meals = (
            self.session.query(PlannedMeal)
            .join(MealPlanDay, PlannedMeal.meal_plan_day_id == MealPlanDay.id)
            .options(joinedload(PlannedMeal.recipe).joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient))
            .filter(MealPlanDay.plan_date >= week_start, MealPlanDay.plan_date < week_start + timedelta(days=7))
            .all()
        )
        for meal in planned_meals:
            if meal.recipe is None or meal.uses_leftovers:
                continue
            for recipe_ingredient in meal.recipe.ingredients:
                required[(recipe_ingredient.ingredient_id, recipe_ingredient.unit)] += recipe_ingredient.quantity

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
