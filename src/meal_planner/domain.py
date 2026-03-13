from __future__ import annotations

from dataclasses import dataclass
from datetime import date, timedelta
from enum import Enum


class MealSlot(str, Enum):
    BREAKFAST = "breakfast"
    LUNCH = "lunch"
    SNACK = "snack"
    DINNER = "dinner"


class InventoryLocation(str, Enum):
    FREEZER = "freezer"
    FRIDGE = "fridge"
    PANTRY = "pantry"


class InventoryItemType(str, Enum):
    INGREDIENT = "ingredient"
    LEFTOVER = "leftover"


class InventoryEventType(str, Enum):
    MEAL_COMPLETED = "meal_completed"
    MANUAL_CORRECTION = "manual_correction"
    GROCERY_RESTOCK = "grocery_restock"
    LEFTOVER_CREATED = "leftover_created"
    LEFTOVER_CONSUMED = "leftover_consumed"


MEAL_SLOT_ORDER = [
    MealSlot.BREAKFAST.value,
    MealSlot.LUNCH.value,
    MealSlot.SNACK.value,
    MealSlot.DINNER.value,
]


@dataclass(slots=True)
class NutritionTargets:
    calories: int
    protein_g: int
    carbs_g: int
    fat_g: int


@dataclass(slots=True)
class MealSlotTargets:
    meal_slot: str
    max_prep_minutes: int
    target_calories: int
    target_protein_g: int


def start_of_week(day: date) -> date:
    return day - timedelta(days=day.weekday())


def daterange(start_day: date, days: int) -> list[date]:
    return [start_day + timedelta(days=offset) for offset in range(days)]
