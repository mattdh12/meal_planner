from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class StoreProductReference:
    product_name: str
    package_size_label: str
    inventory_quantity: float
    inventory_unit: str
    product_url: str | None = None
    notes: str = ""


WEGMANS_PRODUCT_REFERENCES: dict[str, StoreProductReference] = {
    "greek yogurt": StoreProductReference(
        product_name="Wegmans Plain Nonfat Greek Yogurt Value 4 Pack",
        package_size_label="value 4 pack",
        inventory_quantity=2.5,
        inventory_unit="cup",
        product_url="https://www.wegmans.com/shop/product/687869-Yogurt-Plain-Nonfat-Greek-Value-4-Pack",
        notes="Package-to-cup conversion is approximate so inventory stays easy to manage.",
    ),
    "frozen berries": StoreProductReference(
        product_name="Frozen mixed berries",
        package_size_label="12 oz bag",
        inventory_quantity=3.0,
        inventory_unit="cup",
    ),
    "granola": StoreProductReference(
        product_name="Granola",
        package_size_label="11 oz bag",
        inventory_quantity=4.0,
        inventory_unit="cup",
    ),
    "honey": StoreProductReference(
        product_name="Honey",
        package_size_label='12 oz squeeze bottle',
        inventory_quantity=16.0,
        inventory_unit="tbsp",
    ),
    "rolled oats": StoreProductReference(
        product_name="Old fashioned rolled oats",
        package_size_label="18 oz canister",
        inventory_quantity=5.0,
        inventory_unit="cup",
    ),
    "milk": StoreProductReference(
        product_name="Whole milk",
        package_size_label="1/2 gallon carton",
        inventory_quantity=8.0,
        inventory_unit="cup",
    ),
    "chia seeds": StoreProductReference(
        product_name="Chia seeds",
        package_size_label="12 oz bag",
        inventory_quantity=28.0,
        inventory_unit="tbsp",
    ),
    "banana": StoreProductReference(
        product_name="Bananas",
        package_size_label="single banana",
        inventory_quantity=1.0,
        inventory_unit="count",
    ),
    "peanut butter": StoreProductReference(
        product_name="Peanut butter",
        package_size_label="16 oz jar",
        inventory_quantity=32.0,
        inventory_unit="tbsp",
    ),
    "protein powder": StoreProductReference(
        product_name="Protein powder",
        package_size_label="30 scoop tub",
        inventory_quantity=30.0,
        inventory_unit="scoop",
    ),
    "turkey slices": StoreProductReference(
        product_name="Turkey breast deli slices",
        package_size_label="8 oz pack",
        inventory_quantity=8.0,
        inventory_unit="oz",
    ),
    "whole wheat wrap": StoreProductReference(
        product_name="Whole wheat wraps",
        package_size_label="8 count package",
        inventory_quantity=8.0,
        inventory_unit="count",
    ),
    "hummus": StoreProductReference(
        product_name="Hummus",
        package_size_label="10 oz tub",
        inventory_quantity=10.0,
        inventory_unit="tbsp",
    ),
    "spinach": StoreProductReference(
        product_name="Baby spinach",
        package_size_label="5 oz clamshell",
        inventory_quantity=5.0,
        inventory_unit="cup",
    ),
    "cooked rice": StoreProductReference(
        product_name="Microwave jasmine or brown rice",
        package_size_label="2 cup pouch",
        inventory_quantity=2.0,
        inventory_unit="cup",
    ),
    "chicken breast": StoreProductReference(
        product_name="Boneless skinless chicken breast family pack",
        package_size_label="about 1.5 lb pack",
        inventory_quantity=1.5,
        inventory_unit="lb",
    ),
    "broccoli florets": StoreProductReference(
        product_name="Broccoli florets",
        package_size_label="12 oz bag",
        inventory_quantity=4.0,
        inventory_unit="cup",
    ),
    "olive oil": StoreProductReference(
        product_name="Wegmans Extra Virgin Olive Oil",
        package_size_label="17 fl oz bottle",
        inventory_quantity=34.0,
        inventory_unit="tbsp",
        product_url="https://www.wegmans.com/shop/product/18452-Olive-Oil-Extra-Virgin",
    ),
    "salsa": StoreProductReference(
        product_name="Salsa",
        package_size_label="16 oz jar",
        inventory_quantity=2.0,
        inventory_unit="cup",
    ),
    "black beans": StoreProductReference(
        product_name="Black beans",
        package_size_label="15.5 oz can",
        inventory_quantity=1.5,
        inventory_unit="cup",
        notes="Cup conversion is approximate for drained beans.",
    ),
    "sweet potato": StoreProductReference(
        product_name="Sweet potato",
        package_size_label="single potato",
        inventory_quantity=1.0,
        inventory_unit="count",
    ),
    "turkey meatballs": StoreProductReference(
        product_name="Frozen turkey meatballs",
        package_size_label="20 count bag",
        inventory_quantity=20.0,
        inventory_unit="count",
    ),
    "mixed vegetables": StoreProductReference(
        product_name="Frozen mixed vegetables",
        package_size_label="12 oz bag",
        inventory_quantity=3.0,
        inventory_unit="cup",
    ),
    "tuna packet": StoreProductReference(
        product_name="Tuna packet",
        package_size_label="single pouch",
        inventory_quantity=1.0,
        inventory_unit="count",
    ),
    "crackers": StoreProductReference(
        product_name="Whole grain crackers",
        package_size_label="8 serving box",
        inventory_quantity=8.0,
        inventory_unit="serving",
    ),
    "cottage cheese": StoreProductReference(
        product_name="Wegmans Cottage Cheese Organic 4% Milkfat Whole Milk Classic",
        package_size_label="16 oz tub",
        inventory_quantity=2.0,
        inventory_unit="cup",
        product_url="https://www.wegmans.com/shop/product/665635-Cottage-Cheese-Organic-4-Milkfat-Whole-Milk-Classic",
    ),
    "apple": StoreProductReference(
        product_name="Apple",
        package_size_label="single apple",
        inventory_quantity=1.0,
        inventory_unit="count",
    ),
    "trail mix": StoreProductReference(
        product_name="Trail mix",
        package_size_label="10 serving bag",
        inventory_quantity=10.0,
        inventory_unit="serving",
    ),
    "avocado": StoreProductReference(
        product_name="Avocado",
        package_size_label="single avocado",
        inventory_quantity=1.0,
        inventory_unit="count",
    ),
    "rice cakes": StoreProductReference(
        product_name="Rice cakes",
        package_size_label="14 count sleeve",
        inventory_quantity=14.0,
        inventory_unit="count",
    ),
    "egg whites": StoreProductReference(
        product_name="Liquid egg whites",
        package_size_label="16 oz carton",
        inventory_quantity=2.0,
        inventory_unit="cup",
    ),
    "microwave potato": StoreProductReference(
        product_name="Microwave baking potato",
        package_size_label="single potato",
        inventory_quantity=1.0,
        inventory_unit="count",
    ),
    "shredded cheese": StoreProductReference(
        product_name="Shredded cheese",
        package_size_label="8 oz bag",
        inventory_quantity=2.0,
        inventory_unit="cup",
    ),
    "seasoning blend": StoreProductReference(
        product_name="All-purpose seasoning blend",
        package_size_label="16 tbsp jar",
        inventory_quantity=16.0,
        inventory_unit="tbsp",
    ),
}


def get_wegmans_product_reference(ingredient_name: str) -> StoreProductReference | None:
    return WEGMANS_PRODUCT_REFERENCES.get(ingredient_name.strip().lower())
