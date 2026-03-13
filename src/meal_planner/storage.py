from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime
from pathlib import Path

from sqlalchemy import Boolean, Date, DateTime, Float, ForeignKey, Integer, String, Text, UniqueConstraint, create_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from meal_planner.domain import InventoryEventType, InventoryItemType, InventoryLocation, MealSlot
from meal_planner.settings import DATABASE_PATH, DATA_DIR, SEED_DATA_PATH


class Base(DeclarativeBase):
    pass


def utcnow_naive() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class UserProfile(Base):
    __tablename__ = "user_profiles"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), default="Matt")
    age: Mapped[int] = mapped_column(Integer, default=32)
    sex: Mapped[str] = mapped_column(String(20), default="male")
    current_weight_lb: Mapped[float] = mapped_column(Float, default=175.0)
    goal_weight_lb: Mapped[float] = mapped_column(Float, default=188.0)
    fitness_goal: Mapped[str] = mapped_column(String(200), default="Gain 10 to 15 pounds of muscle.")
    shopping_frequency_days: Mapped[int] = mapped_column(Integer, default=7)
    preferred_store: Mapped[str] = mapped_column(String(80), default="Wegmans")
    leftovers_cap: Mapped[int] = mapped_column(Integer, default=1)
    breakfast_max_prep_minutes: Mapped[int] = mapped_column(Integer, default=0)
    lunch_max_prep_minutes: Mapped[int] = mapped_column(Integer, default=10)
    snack_max_prep_minutes: Mapped[int] = mapped_column(Integer, default=0)
    dinner_max_prep_minutes: Mapped[int] = mapped_column(Integer, default=30)
    notes: Mapped[str] = mapped_column(Text, default="")


class Appliance(Base):
    __tablename__ = "appliances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    has_appliance: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    is_known: Mapped[bool] = mapped_column(Boolean, default=True)


class Ingredient(Base):
    __tablename__ = "ingredients"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(100), unique=True)
    default_unit: Mapped[str] = mapped_column(String(30), default="count")
    category: Mapped[str] = mapped_column(String(50), default="Pantry")


class InventoryItem(Base):
    __tablename__ = "inventory_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    item_type: Mapped[str] = mapped_column(String(20), default=InventoryItemType.INGREDIENT.value)
    ingredient_id: Mapped[int | None] = mapped_column(ForeignKey("ingredients.id"), nullable=True)
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), nullable=True)
    location: Mapped[str] = mapped_column(String(30), default=InventoryLocation.PANTRY.value)
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(30), default="count")
    notes: Mapped[str] = mapped_column(Text, default="")

    ingredient: Mapped[Ingredient | None] = relationship()
    recipe: Mapped["Recipe | None"] = relationship()


class Recipe(Base):
    __tablename__ = "recipes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    meal_slot: Mapped[str] = mapped_column(String(30), default=MealSlot.DINNER.value)
    prep_minutes: Mapped[int] = mapped_column(Integer, default=10)
    cook_minutes: Mapped[int] = mapped_column(Integer, default=0)
    simplicity_score: Mapped[int] = mapped_column(Integer, default=3)
    pots_pans_score: Mapped[int] = mapped_column(Integer, default=2)
    servings: Mapped[int] = mapped_column(Integer, default=1)
    leftover_servings: Mapped[int] = mapped_column(Integer, default=0)
    calories: Mapped[int] = mapped_column(Integer, default=400)
    protein_g: Mapped[int] = mapped_column(Integer, default=20)
    carbs_g: Mapped[int] = mapped_column(Integer, default=40)
    fat_g: Mapped[int] = mapped_column(Integer, default=15)
    has_protein_component: Mapped[bool] = mapped_column(Boolean, default=True)
    has_carb_component: Mapped[bool] = mapped_column(Boolean, default=True)
    has_healthy_fat_component: Mapped[bool] = mapped_column(Boolean, default=False)
    has_vegetable_component: Mapped[bool] = mapped_column(Boolean, default=False)
    instructions: Mapped[str] = mapped_column(Text, default="")
    notes: Mapped[str] = mapped_column(Text, default="")

    ingredients: Mapped[list["RecipeIngredient"]] = relationship(back_populates="recipe", cascade="all, delete-orphan")
    appliances: Mapped[list["RecipeAppliance"]] = relationship(back_populates="recipe", cascade="all, delete-orphan")
    feedback_entries: Mapped[list["RecipeFeedback"]] = relationship(back_populates="recipe", cascade="all, delete-orphan")


class RecipeIngredient(Base):
    __tablename__ = "recipe_ingredients"
    __table_args__ = (UniqueConstraint("recipe_id", "ingredient_id"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    ingredient_id: Mapped[int] = mapped_column(ForeignKey("ingredients.id"))
    quantity: Mapped[float] = mapped_column(Float, default=1)
    unit: Mapped[str] = mapped_column(String(30), default="count")

    recipe: Mapped[Recipe] = relationship(back_populates="ingredients")
    ingredient: Mapped[Ingredient] = relationship()


class RecipeAppliance(Base):
    __tablename__ = "recipe_appliances"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    appliance_name: Mapped[str] = mapped_column(String(100))

    recipe: Mapped[Recipe] = relationship(back_populates="appliances")


class RecipeFeedback(Base):
    __tablename__ = "recipe_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    recipe_id: Mapped[int] = mapped_column(ForeignKey("recipes.id"))
    tasty_rating: Mapped[int] = mapped_column(Integer, default=3)
    ease_rating: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    recipe: Mapped[Recipe] = relationship(back_populates="feedback_entries")


class Supplement(Base):
    __tablename__ = "supplements"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    name: Mapped[str] = mapped_column(String(120), unique=True)
    category: Mapped[str] = mapped_column(String(50), default="Supplement")
    recommended: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")
    dosage: Mapped[str] = mapped_column(String(80), default="")

    feedback_entries: Mapped[list["SupplementFeedback"]] = relationship(back_populates="supplement", cascade="all, delete-orphan")


class SupplementFeedback(Base):
    __tablename__ = "supplement_feedback"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    supplement_id: Mapped[int] = mapped_column(ForeignKey("supplements.id"))
    rating: Mapped[int] = mapped_column(Integer, default=3)
    notes: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)

    supplement: Mapped[Supplement] = relationship(back_populates="feedback_entries")


class MealPlanDay(Base):
    __tablename__ = "meal_plan_days"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    plan_date: Mapped[date] = mapped_column(Date, unique=True)
    notes: Mapped[str] = mapped_column(Text, default="")

    meals: Mapped[list["PlannedMeal"]] = relationship(back_populates="day", cascade="all, delete-orphan")
    prep_tasks: Mapped[list["PrepTask"]] = relationship(back_populates="day", cascade="all, delete-orphan")


class PlannedMeal(Base):
    __tablename__ = "planned_meals"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_plan_day_id: Mapped[int] = mapped_column(ForeignKey("meal_plan_days.id"))
    meal_slot: Mapped[str] = mapped_column(String(30))
    recipe_id: Mapped[int | None] = mapped_column(ForeignKey("recipes.id"), nullable=True)
    title: Mapped[str] = mapped_column(String(140))
    planned_servings: Mapped[int] = mapped_column(Integer, default=1)
    uses_leftovers: Mapped[bool] = mapped_column(Boolean, default=False)
    source_meal_id: Mapped[int | None] = mapped_column(ForeignKey("planned_meals.id"), nullable=True)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)
    notes: Mapped[str] = mapped_column(Text, default="")

    day: Mapped[MealPlanDay] = relationship(back_populates="meals")
    recipe: Mapped[Recipe | None] = relationship(foreign_keys=[recipe_id])


class PrepTask(Base):
    __tablename__ = "prep_tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    meal_plan_day_id: Mapped[int] = mapped_column(ForeignKey("meal_plan_days.id"))
    due_date: Mapped[date] = mapped_column(Date)
    description: Mapped[str] = mapped_column(Text)
    completed: Mapped[bool] = mapped_column(Boolean, default=False)

    day: Mapped[MealPlanDay] = relationship(back_populates="prep_tasks")


class GroceryList(Base):
    __tablename__ = "grocery_lists"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    week_start: Mapped[date] = mapped_column(Date, unique=True)
    store_name: Mapped[str] = mapped_column(String(80), default="Wegmans")
    notes: Mapped[str] = mapped_column(Text, default="")

    items: Mapped[list["GroceryListItem"]] = relationship(back_populates="grocery_list", cascade="all, delete-orphan")


class GroceryListItem(Base):
    __tablename__ = "grocery_list_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    grocery_list_id: Mapped[int] = mapped_column(ForeignKey("grocery_lists.id"))
    ingredient_name: Mapped[str] = mapped_column(String(120))
    quantity: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(30), default="count")
    section: Mapped[str] = mapped_column(String(50), default="Pantry")

    grocery_list: Mapped[GroceryList] = relationship(back_populates="items")


class InventoryEvent(Base):
    __tablename__ = "inventory_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True)
    inventory_item_name: Mapped[str] = mapped_column(String(120))
    ingredient_name: Mapped[str] = mapped_column(String(120), default="")
    event_type: Mapped[str] = mapped_column(String(40), default=InventoryEventType.MANUAL_CORRECTION.value)
    quantity_delta: Mapped[float] = mapped_column(Float, default=0)
    unit: Mapped[str] = mapped_column(String(30), default="count")
    location: Mapped[str] = mapped_column(String(30), default=InventoryLocation.PANTRY.value)
    reason: Mapped[str] = mapped_column(Text, default="")
    related_planned_meal_id: Mapped[int | None] = mapped_column(ForeignKey("planned_meals.id"), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=utcnow_naive)


class Database:
    def __init__(self, database_path: Path | None = None):
        self.database_path = database_path or DATABASE_PATH
        DATA_DIR.mkdir(parents=True, exist_ok=True)
        self.engine = create_engine(f"sqlite:///{self.database_path}", future=True)
        self.session_factory = sessionmaker(bind=self.engine, autoflush=False, expire_on_commit=False)

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    def initialize(self) -> None:
        Base.metadata.create_all(self.engine)
        with self.session() as session:
            if session.query(UserProfile).count() == 0:
                seed_database(session)
            sync_seed_reference_data(session)


def load_seed_data(path: Path = SEED_DATA_PATH) -> dict:
    with path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def seed_database(session: Session) -> None:
    seed_data = load_seed_data()

    profile = UserProfile(**seed_data["profile"])
    session.add(profile)

    for appliance_name in seed_data["appliances"]:
        session.add(Appliance(name=appliance_name["name"], has_appliance=appliance_name["has_appliance"], is_known=True))

    ingredient_lookup: dict[str, Ingredient] = {}
    for ingredient_data in seed_data["ingredients"]:
        ingredient = Ingredient(**ingredient_data)
        session.add(ingredient)
        session.flush()
        ingredient_lookup[ingredient.name] = ingredient

    for recipe_data in seed_data["recipes"]:
        recipe = Recipe(
            name=recipe_data["name"],
            meal_slot=recipe_data["meal_slot"],
            prep_minutes=recipe_data["prep_minutes"],
            cook_minutes=recipe_data.get("cook_minutes", 0),
            simplicity_score=recipe_data.get("simplicity_score", 3),
            pots_pans_score=recipe_data.get("pots_pans_score", 2),
            servings=recipe_data.get("servings", 1),
            leftover_servings=recipe_data.get("leftover_servings", 0),
            calories=recipe_data["calories"],
            protein_g=recipe_data["protein_g"],
            carbs_g=recipe_data["carbs_g"],
            fat_g=recipe_data["fat_g"],
            has_protein_component=recipe_data.get("has_protein_component", True),
            has_carb_component=recipe_data.get("has_carb_component", True),
            has_healthy_fat_component=recipe_data.get("has_healthy_fat_component", False),
            has_vegetable_component=recipe_data.get("has_vegetable_component", False),
            instructions=recipe_data.get("instructions", ""),
            notes=recipe_data.get("notes", ""),
        )
        session.add(recipe)
        session.flush()
        for ingredient_row in recipe_data["ingredients"]:
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    ingredient_id=ingredient_lookup[ingredient_row["name"]].id,
                    quantity=ingredient_row["quantity"],
                    unit=ingredient_row["unit"],
                )
            )
        for appliance_name in recipe_data.get("appliances", []):
            session.add(RecipeAppliance(recipe_id=recipe.id, appliance_name=appliance_name))

    for inventory_row in seed_data["inventory"]:
        ingredient = ingredient_lookup.get(inventory_row["name"])
        session.add(
            InventoryItem(
                name=inventory_row["name"],
                item_type=InventoryItemType.INGREDIENT.value,
                ingredient_id=ingredient.id if ingredient else None,
                location=inventory_row["location"],
                quantity=inventory_row["quantity"],
                unit=inventory_row["unit"],
                notes=inventory_row.get("notes", ""),
            )
        )

    for supplement_data in seed_data["supplements"]:
        session.add(Supplement(**supplement_data))

    session.flush()


def sync_seed_reference_data(session: Session) -> None:
    seed_data = load_seed_data()
    ingredient_lookup: dict[str, Ingredient] = {ingredient.name: ingredient for ingredient in session.query(Ingredient).all()}

    for appliance_row in seed_data.get("appliances", []):
        appliance = session.query(Appliance).filter(Appliance.name == appliance_row["name"]).one_or_none()
        if appliance is None:
            session.add(
                Appliance(
                    name=appliance_row["name"],
                    has_appliance=appliance_row["has_appliance"],
                    is_known=True,
                )
            )

    for ingredient_row in seed_data.get("ingredients", []):
        ingredient = ingredient_lookup.get(ingredient_row["name"])
        if ingredient is None:
            ingredient = Ingredient(**ingredient_row)
            session.add(ingredient)
            session.flush()
            ingredient_lookup[ingredient.name] = ingredient
        else:
            ingredient.default_unit = ingredient_row.get("default_unit", ingredient.default_unit)
            ingredient.category = ingredient_row.get("category", ingredient.category)

    for recipe_row in seed_data.get("recipes", []):
        recipe = session.query(Recipe).filter(Recipe.name == recipe_row["name"]).one_or_none()
        if recipe is None:
            recipe = Recipe(name=recipe_row["name"])
            session.add(recipe)
            session.flush()

        recipe.meal_slot = recipe_row["meal_slot"]
        recipe.prep_minutes = recipe_row["prep_minutes"]
        recipe.cook_minutes = recipe_row.get("cook_minutes", 0)
        recipe.simplicity_score = recipe_row.get("simplicity_score", 3)
        recipe.pots_pans_score = recipe_row.get("pots_pans_score", 2)
        recipe.servings = recipe_row.get("servings", 1)
        recipe.leftover_servings = recipe_row.get("leftover_servings", 0)
        recipe.calories = recipe_row["calories"]
        recipe.protein_g = recipe_row["protein_g"]
        recipe.carbs_g = recipe_row["carbs_g"]
        recipe.fat_g = recipe_row["fat_g"]
        recipe.has_protein_component = recipe_row.get("has_protein_component", True)
        recipe.has_carb_component = recipe_row.get("has_carb_component", True)
        recipe.has_healthy_fat_component = recipe_row.get("has_healthy_fat_component", False)
        recipe.has_vegetable_component = recipe_row.get("has_vegetable_component", False)
        recipe.instructions = recipe_row.get("instructions", recipe.instructions)
        recipe.notes = recipe_row.get("notes", recipe.notes)
        session.flush()

        session.query(RecipeIngredient).filter(RecipeIngredient.recipe_id == recipe.id).delete()
        session.query(RecipeAppliance).filter(RecipeAppliance.recipe_id == recipe.id).delete()
        session.flush()

        for ingredient_data in recipe_row.get("ingredients", []):
            ingredient = ingredient_lookup[ingredient_data["name"]]
            session.add(
                RecipeIngredient(
                    recipe_id=recipe.id,
                    ingredient_id=ingredient.id,
                    quantity=ingredient_data["quantity"],
                    unit=ingredient_data["unit"],
                )
            )
        for appliance_name in recipe_row.get("appliances", []):
            session.add(RecipeAppliance(recipe_id=recipe.id, appliance_name=appliance_name))

    for supplement_row in seed_data.get("supplements", []):
        supplement = session.query(Supplement).filter(Supplement.name == supplement_row["name"]).one_or_none()
        if supplement is None:
            supplement = Supplement(name=supplement_row["name"])
            session.add(supplement)
        supplement.category = supplement_row.get("category", supplement.category)
        supplement.recommended = supplement_row.get("recommended", supplement.recommended)
        supplement.notes = supplement_row.get("notes", supplement.notes)
        supplement.dosage = supplement_row.get("dosage", supplement.dosage)

    session.flush()


def replace_inventory_from_seed(session: Session) -> None:
    seed_data = load_seed_data()
    ingredient_lookup: dict[str, Ingredient] = {ingredient.name: ingredient for ingredient in session.query(Ingredient).all()}
    session.query(InventoryEvent).delete()
    session.query(InventoryItem).delete()
    session.flush()

    for inventory_row in seed_data.get("inventory", []):
        ingredient = ingredient_lookup.get(inventory_row["name"])
        session.add(
            InventoryItem(
                name=inventory_row["name"],
                item_type=InventoryItemType.INGREDIENT.value,
                ingredient_id=ingredient.id if ingredient else None,
                location=inventory_row["location"],
                quantity=inventory_row["quantity"],
                unit=inventory_row["unit"],
                notes=inventory_row.get("notes", ""),
            )
        )
    session.flush()
