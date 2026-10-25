"""Shared demo catalog. All prices, full-stock counts and consumption rates are synthetic."""

# category, SKU, Chinese name, English singular sales unit, English plural sales unit,
# demo cents, full-fridge units, estimated daily demo units, English aliases
FOODS = [
    ("eggs", "eggs_6", "鸡蛋", "pack of six-count eggs", "packs of six-count eggs", 499, 12, 2, r"eggs?"),
    ("milk", "oat_unsweetened", "无糖燕麦奶", "carton of unsweetened oat milk", "cartons of unsweetened oat milk", 449, 2, .5, r"(?:oat )?milk"),
    ("yogurt", "yogurt_unsweetened", "原味无糖酸奶", "pack of plain unsweetened yogurt", "packs of plain unsweetened yogurt", 199, 4, .25, r"yogurts?"),
    ("cheese", "cheese_pack", "奶酪", "pack of cheese", "packs of cheese", 399, 1, .1, r"cheese"),
    ("butter", "butter_pack", "黄油", "pack of butter", "packs of butter", 349, 1, .05, r"butter"),
    ("apples", "apples_each", "苹果", "apple", "apples", 89, 4, .5, r"apples?"),
    ("onions", "onions_each", "洋葱", "onion", "onions", 99, 3, .25, r"onions?"),
    ("tomatoes", "tomatoes_each", "番茄", "tomato", "tomatoes", 79, 6, 1, r"tomato(?:es)?"),
    ("garlic", "garlic_bulb", "大蒜", "bulb of garlic", "bulbs of garlic", 69, 2, .1, r"garlic"),
    ("avocados", "avocados_each", "牛油果", "avocado", "avocados", 149, 3, .5, r"avocados?"),
    ("carrots", "carrots_each", "胡萝卜", "carrot", "carrots", 29, 6, 1, r"carrots?"),
    ("broccoli", "broccoli_head", "西兰花", "head of broccoli", "heads of broccoli", 199, 2, .25, r"broccoli"),
    ("spinach", "spinach_bag", "菠菜", "bag of spinach", "bags of spinach", 249, 1, .1, r"spinach"),
    ("lettuce", "lettuce_head", "生菜", "head of lettuce", "heads of lettuce", 179, 1, .1, r"lettuce"),
    ("cucumbers", "cucumbers_each", "黄瓜", "cucumber", "cucumbers", 79, 2, .25, r"cucumbers?"),
    ("bell_peppers", "bell_peppers_each", "甜椒", "bell pepper", "bell peppers", 129, 3, .3, r"bell peppers?"),
    ("mushrooms", "mushrooms_box", "蘑菇", "box of mushrooms", "boxes of mushrooms", 249, 1, .1, r"mushrooms?"),
    ("lemons", "lemons_each", "柠檬", "lemon", "lemons", 59, 3, .2, r"lemons?"),
    ("oranges", "oranges_each", "橙子", "orange", "oranges", 89, 5, .5, r"oranges?"),
    ("strawberries", "strawberries_box", "草莓", "box of strawberries", "boxes of strawberries", 349, 1, .1, r"strawberr(?:y|ies)"),
    ("blueberries", "blueberries_box", "蓝莓", "box of blueberries", "boxes of blueberries", 299, 1, .1, r"blueberr(?:y|ies)"),
    ("chicken", "chicken_pack", "鸡肉", "pack of chicken", "packs of chicken", 649, 1, .15, r"chicken"),
    ("fish", "fish_pack", "鱼肉", "pack of fish", "packs of fish", 799, 1, .1, r"fish"),
    ("tofu", "tofu_pack", "豆腐", "pack of tofu", "packs of tofu", 199, 2, .25, r"tofu"),
    ("bread", "bread_loaf", "面包", "loaf of bread", "loaves of bread", 299, 1, .2, r"bread"),
    ("juice", "juice_carton", "果汁", "carton of juice", "cartons of juice", 349, 1, .2, r"(?:orange |apple )?juice"),
    ("bottled_water", "water_bottle", "瓶装水", "bottle of water", "bottles of water", 79, 6, 1, r"(?:bottled |still )?water"),
    ("sparkling_water", "sparkling_water_can", "气泡水", "can of sparkling water", "cans of sparkling water", 99, 6, .5, r"sparkling water"),
    ("jam", "jam_jar", "果酱", "jar of jam", "jars of jam", 299, 1, .05, r"jam"),
    ("mustard", "mustard_jar", "芥末酱", "jar of mustard", "jars of mustard", 199, 1, .02, r"mustard"),
    ("ketchup", "ketchup_bottle", "番茄酱", "bottle of ketchup", "bottles of ketchup", 249, 1, .05, r"ketchup"),
]

CATEGORIES = tuple(row[0] for row in FOODS)
VISION_CATEGORIES = CATEGORIES
LABELS = {row[0]: row[2] for row in FOODS}
LABELS.update(milk="牛奶", yogurt="酸奶")
ENGLISH_ALIASES = {row[0]: row[8] for row in FOODS}
DEMO_FULL_STOCK = {row[0]: row[6] for row in FOODS}
DEMO_DAILY_USAGE = {row[0]: row[7] for row in FOODS}
CAPACITY_LEVELS = ("nearly_empty", "low", "medium", "high", "full", "unknown")
CATALOG = {}

for category, sku, chinese, singular, plural, cents, _, _, _ in FOODS:
    CATALOG[sku] = {"category": category, "name": chinese, "pack_size": 6 if category == "eggs" else 1,
                    "cents": cents, "singular_en": singular, "plural_en": plural,
                    "observation_singular_en": singular, "observation_plural_en": plural,
                    "observation_unit_zh": "包装"}

# Observation counts and purchase units intentionally differ for egg cartons.
CATALOG["eggs_6"].update(observation_singular_en="egg", observation_plural_en="eggs", observation_unit_zh="枚")
CATALOG["oat_unsweetened"].update(observation_singular_en="carton of milk", observation_plural_en="cartons of milk", observation_unit_zh="盒")
CATALOG["yogurt_unsweetened"].update(observation_singular_en="pack of yogurt", observation_plural_en="packs of yogurt", observation_unit_zh="份")
CATALOG["dairy_milk"] = {**CATALOG["oat_unsweetened"], "name": "普通牛奶", "cents": 399,
                          "singular_en": "carton of regular milk", "plural_en": "cartons of regular milk"}
for category in ("apples", "onions", "tomatoes", "avocados", "carrots", "cucumbers", "bell_peppers", "lemons", "oranges"):
    product = next(p for p in CATALOG.values() if p["category"] == category)
    product.update(unit="each", unit_zh="个", observation_unit_zh="个")
CATALOG["garlic_bulb"].update(unit="each", unit_zh="头", observation_unit_zh="头")
CATALOG["broccoli_head"].update(unit="each", unit_zh="颗", observation_unit_zh="颗")
CATALOG["lettuce_head"].update(unit="each", unit_zh="颗", observation_unit_zh="颗")


def default_product(category):
    return next(p for p in CATALOG.values() if p["category"] == category)


def format_product_count(sku, count, locale="en-US"):
    p = CATALOG[sku]
    number = f"{count:g}"
    if locale.lower().startswith("en"):
        return f"{number} {p['singular_en'] if count == 1 else p['plural_en']}"
    return f"{number}{p.get('unit_zh', '份')}{p['name']}"


def format_observed_count(category, count, locale="en-US"):
    p = default_product(category)
    number = f"{count:g}"
    if locale.lower().startswith("en"):
        return f"{number} {p['observation_singular_en'] if count == 1 else p['observation_plural_en']}"
    return f"{number}{p['observation_unit_zh']}{LABELS[category]}"


def vision_categories():
    return [{"category": category, "name": category.replace("_", " "),
             "count_unit": default_product(category)["observation_singular_en"]} for category in VISION_CATEGORIES]
