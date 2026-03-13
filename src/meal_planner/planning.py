from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from math import fabs

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

from meal_planner.domain import MealSlot, MealSlotTargets, NutritionTargets, start_of_week
from meal_planner.storage import Appliance, InventoryItem, MealPlanDay, PlannedMeal, Recipe, RecipeFeedback, RecipeIngredient, UserProfile


@dataclass(slots=True)
class ScoredRecipe:
    recipe: Recipe
    score: float
    inventory_coverage: float


def compute_nutrition_targets(profile: UserProfile) -> NutritionTargets:
    current_weight = profile.current_weight_lb
    goal_weight = profile.goal_weight_lb or current_weight
    base_calories = int(current_weight * 15)
    calorie_surplus = 300 if goal_weight > current_weight else 0
    workouts_per_week = max(0, getattr(profile, "workouts_per_week", 0) or 0)
    activity_calories = round((min(workouts_per_week, 14) * 250) / 7)
    calories = base_calories + calorie_surplus + activity_calories
    protein = round(goal_weight * 0.9)
    fat = max(60, round(current_weight * 0.35))
    carbs = max(120, round((calories - (protein * 4 + fat * 9)) / 4))
    return NutritionTargets(calories=calories, protein_g=protein, carbs_g=carbs, fat_g=fat)


def build_slot_targets(profile: UserProfile, targets: NutritionTargets) -> dict[str, MealSlotTargets]:
    calorie_ratios = {
        MealSlot.BREAKFAST.value: 0.25,
        MealSlot.LUNCH.value: 0.25,
        MealSlot.SNACK.value: 0.10,
        MealSlot.DINNER.value: 0.40,
    }
    protein_ratios = {
        MealSlot.BREAKFAST.value: 0.20,
        MealSlot.LUNCH.value: 0.25,
        MealSlot.SNACK.value: 0.10,
        MealSlot.DINNER.value: 0.45,
    }
    prep_limits = {
        MealSlot.BREAKFAST.value: profile.breakfast_max_prep_minutes,
        MealSlot.LUNCH.value: profile.lunch_max_prep_minutes,
        MealSlot.SNACK.value: profile.snack_max_prep_minutes,
        MealSlot.DINNER.value: profile.dinner_max_prep_minutes,
    }

    return {
        slot: MealSlotTargets(
            meal_slot=slot,
            max_prep_minutes=prep_limits[slot],
            target_calories=round(targets.calories * calorie_ratios[slot]),
            target_protein_g=round(targets.protein_g * protein_ratios[slot]),
        )
        for slot in calorie_ratios
    }


def inventory_coverage(recipe: Recipe, inventory_by_ingredient: dict[int, float]) -> float:
    if not recipe.ingredients:
        return 0.0
    covered = 0
    for recipe_ingredient in recipe.ingredients:
        available = inventory_by_ingredient.get(recipe_ingredient.ingredient_id, 0)
        if available >= recipe_ingredient.quantity:
            covered += 1
    return covered / len(recipe.ingredients)


def recipe_feedback_score(session: Session) -> dict[int, float]:
    rows = (
        session.query(
            RecipeFeedback.recipe_id,
            func.avg((RecipeFeedback.tasty_rating + RecipeFeedback.ease_rating) / 2.0),
        )
        .group_by(RecipeFeedback.recipe_id)
        .all()
    )
    return {recipe_id: float(score) for recipe_id, score in rows}


def recent_recipe_counts(session: Session) -> dict[int, int]:
    cutoff = date.today() - timedelta(days=14)
    rows = (
        session.query(PlannedMeal.recipe_id, func.count(PlannedMeal.id))
        .join(MealPlanDay, PlannedMeal.meal_plan_day_id == MealPlanDay.id)
        .filter(PlannedMeal.recipe_id.is_not(None))
        .filter(MealPlanDay.plan_date >= cutoff)
        .group_by(PlannedMeal.recipe_id)
        .all()
    )
    return {recipe_id: count for recipe_id, count in rows if recipe_id is not None}


def known_appliance_map(session: Session) -> dict[str, bool | None]:
    return {appliance.name.lower(): appliance.has_appliance for appliance in session.query(Appliance).all()}


def ensure_appliance_records(session: Session, recipes: list[Recipe]) -> None:
    known = {appliance.name.lower() for appliance in session.query(Appliance).all()}
    created = False
    for recipe in recipes:
        for appliance in recipe.appliances:
            key = appliance.appliance_name.lower()
            if key not in known:
                session.add(Appliance(name=appliance.appliance_name, has_appliance=None, is_known=False))
                known.add(key)
                created = True
    if created:
        session.flush()


def score_recipe(
    recipe: Recipe,
    slot_target: MealSlotTargets,
    coverage: float,
    feedback_score: float,
    recent_count: int,
    appliance_state: dict[str, bool | None],
    inventory_by_ingredient: dict[int, float],
    weekly_ingredient_counts: dict[int, int] | None = None,
    weekly_recipe_count: int = 0,
) -> float:
    for recipe_appliance in recipe.appliances:
        state = appliance_state.get(recipe_appliance.appliance_name.lower())
        if state is False or state is None:
            return -10_000

    total_minutes = recipe.prep_minutes + recipe.cook_minutes
    if slot_target.max_prep_minutes == 0 and total_minutes > 5:
        return -1_000
    if total_minutes > max(slot_target.max_prep_minutes, 5) + 20:
        return -1_000

    score = 100.0
    score += coverage * 25
    score += feedback_score * 4
    score -= recent_count * 8
    score -= fabs(recipe.calories - slot_target.target_calories) / max(slot_target.target_calories, 1) * 20
    score -= fabs(recipe.protein_g - slot_target.target_protein_g) * 0.3
    score += max(0, 5 - recipe.prep_minutes)
    score += max(0, 4 - recipe.pots_pans_score) * 4
    score += recipe.simplicity_score * 3
    weekly_ingredient_counts = weekly_ingredient_counts or {}
    ingredient_ids = [row.ingredient_id for row in recipe.ingredients]
    if ingredient_ids:
        overlap_count = sum(1 for ingredient_id in ingredient_ids if weekly_ingredient_counts.get(ingredient_id, 0) > 0)
        new_shopping_count = sum(
            1
            for ingredient_id in ingredient_ids
            if inventory_by_ingredient.get(ingredient_id, 0) <= 0 and weekly_ingredient_counts.get(ingredient_id, 0) == 0
        )
        overlap_ratio = overlap_count / len(ingredient_ids)
        score += overlap_ratio * 28
        score -= new_shopping_count * 10
        score -= (1 - coverage) * 18
        if coverage == 1.0:
            score += 16
            if slot_target.max_prep_minutes <= 10:
                score += 10
        elif coverage >= 0.75:
            score += 8

    if recipe.meal_slot in {MealSlot.BREAKFAST.value, MealSlot.SNACK.value}:
        score += coverage * 12

    repeat_penalty = 2 if recipe.meal_slot in {MealSlot.BREAKFAST.value, MealSlot.SNACK.value} else 6
    score -= weekly_recipe_count * repeat_penalty
    if recipe.meal_slot == MealSlot.DINNER.value:
        score += 5 if recipe.has_protein_component else 0
        score += 3 if recipe.has_carb_component else 0
        score += 2 if recipe.has_healthy_fat_component else 0
        score += 4 if recipe.has_vegetable_component else 0
    return score


def recipe_candidates(session: Session, meal_slot: str) -> list[Recipe]:
    return (
        session.query(Recipe)
        .options(joinedload(Recipe.ingredients).joinedload(RecipeIngredient.ingredient), joinedload(Recipe.appliances))
        .filter(Recipe.meal_slot == meal_slot)
        .order_by(Recipe.name.asc())
        .all()
    )


def current_inventory_by_ingredient(session: Session) -> dict[int, float]:
    rows = (
        session.query(InventoryItem.ingredient_id, func.sum(InventoryItem.quantity))
        .filter(InventoryItem.ingredient_id.is_not(None))
        .filter(InventoryItem.item_type == "ingredient")
        .group_by(InventoryItem.ingredient_id)
        .all()
    )
    return {ingredient_id: float(quantity) for ingredient_id, quantity in rows if ingredient_id is not None}


def choose_best_recipe(
    session: Session,
    profile: UserProfile,
    meal_slot: str,
    weekly_ingredient_counts: dict[int, int] | None = None,
    weekly_recipe_counts: dict[int, int] | None = None,
) -> ScoredRecipe | None:
    targets = build_slot_targets(profile, compute_nutrition_targets(profile))
    candidates = recipe_candidates(session, meal_slot)
    if not candidates:
        return None
    ensure_appliance_records(session, candidates)
    inventory = current_inventory_by_ingredient(session)
    feedback = recipe_feedback_score(session)
    recent_counts = recent_recipe_counts(session)
    appliance_state = known_appliance_map(session)
    weekly_ingredient_counts = weekly_ingredient_counts or {}
    weekly_recipe_counts = weekly_recipe_counts or {}

    scored: list[ScoredRecipe] = []
    for recipe in candidates:
        coverage = inventory_coverage(recipe, inventory)
        score = score_recipe(
            recipe=recipe,
            slot_target=targets[meal_slot],
            coverage=coverage,
            feedback_score=feedback.get(recipe.id, 3.0),
            recent_count=recent_counts.get(recipe.id, 0),
            appliance_state=appliance_state,
            inventory_by_ingredient=inventory,
            weekly_ingredient_counts=weekly_ingredient_counts,
            weekly_recipe_count=weekly_recipe_counts.get(recipe.id, 0),
        )
        scored.append(ScoredRecipe(recipe=recipe, score=score, inventory_coverage=coverage))

    scored.sort(key=lambda row: row.score, reverse=True)
    return scored[0] if scored and scored[0].score > -1_000 else None


def plan_context_summary(session: Session) -> dict:
    profile = session.query(UserProfile).first()
    inventory = current_inventory_by_ingredient(session)
    return {
        "profile": profile,
        "inventory_items": inventory,
        "week_start": start_of_week(date.today()),
    }
