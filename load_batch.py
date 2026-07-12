"""Load a classify_batch.py + match_google_places.py output pair into the DB.

USAGE:
    python load_batch.py products.json restaurants.json [--area Dhanmondi]

products.json    - classify_batch.py's *_products.json (has source_restaurant_code,
                    full product fields, and food_type_leaf/parent/cuisine/flavor_tags)
restaurants.json - classify_batch.py's *_restaurants.json, after match_google_places.py
                    has filled in google_place_id/match_status

Upserts Restaurant by source_restaurant_code and Product by
(restaurant_id, source_product_id) - safe to re-run on the same files.
FoodType/Cuisine/FlavorTag rows are get-or-created by name.
"""
import argparse
import json

from database import SessionLocal
import models


def get_or_create_food_type(db, cache, created_counter, leaf_name, parent_name):
    if leaf_name is None:
        return None
    if leaf_name in cache:
        return cache[leaf_name]

    parent = None
    if parent_name and parent_name != leaf_name:
        parent = get_or_create_food_type(db, cache, created_counter, parent_name, None)

    food_type = db.query(models.FoodType).filter(models.FoodType.name == leaf_name).first()
    if food_type is None:
        food_type = models.FoodType(name=leaf_name, parent_id=parent.id if parent else None)
        db.add(food_type)
        db.flush()
        created_counter.append(1)
    elif parent and food_type.parent_id is None:
        food_type.parent_id = parent.id

    cache[leaf_name] = food_type
    return food_type


def get_or_create_cuisine(db, cache, created_counter, name):
    if name is None:
        return None
    if name in cache:
        return cache[name]
    cuisine = db.query(models.Cuisine).filter(models.Cuisine.name == name).first()
    if cuisine is None:
        cuisine = models.Cuisine(name=name)
        db.add(cuisine)
        db.flush()
        created_counter.append(1)
    cache[name] = cuisine
    return cuisine


def get_or_create_flavor_tag(db, cache, created_counter, name):
    if name in cache:
        return cache[name]
    tag = db.query(models.FlavorTag).filter(models.FlavorTag.name == name).first()
    if tag is None:
        tag = models.FlavorTag(name=name)
        db.add(tag)
        db.flush()
        created_counter.append(1)
    cache[name] = tag
    return tag


def upsert_restaurant(db, r, area):
    code = r.get("source_restaurant_code")
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.source_restaurant_code == code
    ).first()
    created = restaurant is None
    if created:
        restaurant = models.Restaurant(source_restaurant_code=code)
        db.add(restaurant)

    coords = r.get("coordinates") or {}
    images = r.get("images") or {}

    restaurant.name = r["name"]
    restaurant.address = r.get("address")
    restaurant.area = area
    restaurant.latitude = coords.get("latitude")
    restaurant.longitude = coords.get("longitude")
    restaurant.raw_cuisines = r.get("cuisines") or []
    restaurant.foodpanda_rating = r.get("rating")
    restaurant.foodpanda_review_number = r.get("review_number")
    restaurant.budget = r.get("budget")
    restaurant.image_url = images.get("hero")
    restaurant.logo_url = images.get("logo")
    restaurant.chain_name = r.get("chain_name")
    restaurant.chain_code = r.get("chain_code")
    restaurant.google_place_id = r.get("google_place_id")
    restaurant.match_status = r.get("match_status") or "unmatched"

    db.flush()
    return restaurant, created


def upsert_product(db, p, restaurant_id, food_type_id):
    source_product_id = p["product_id"]
    product = db.query(models.Product).filter(
        models.Product.restaurant_id == restaurant_id,
        models.Product.source_product_id == source_product_id,
    ).first()
    created = product is None
    if created:
        product = models.Product(restaurant_id=restaurant_id, source_product_id=source_product_id)
        db.add(product)

    product.name = p["name"]
    product.description = p.get("description")
    product.price_bdt = p.get("price_bdt")
    product.image_url = p.get("image")
    product.is_sold_out = p.get("is_sold_out", False)
    product.category_raw = p.get("category")
    product.dietary_attributes_raw = p.get("dietary_attributes") or []
    product.variations = p.get("variations") or []
    product.food_type_id = food_type_id

    db.flush()
    return product, created


def sync_product_links(db, product, cuisine, flavor_tags):
    db.query(models.ProductCuisine).filter(models.ProductCuisine.product_id == product.id).delete()
    db.query(models.ProductFlavorTag).filter(models.ProductFlavorTag.product_id == product.id).delete()

    if cuisine is not None:
        db.add(models.ProductCuisine(product_id=product.id, cuisine_id=cuisine.id))
    for tag in flavor_tags:
        db.add(models.ProductFlavorTag(product_id=product.id, flavor_tag_id=tag.id))


def main():
    parser = argparse.ArgumentParser(description="Load a classified/matched batch into the DB.")
    parser.add_argument("products_path")
    parser.add_argument("restaurants_path")
    parser.add_argument("--area", default="Dhanmondi")
    args = parser.parse_args()

    with open(args.products_path, encoding="utf-8") as f:
        products = json.load(f)
    with open(args.restaurants_path, encoding="utf-8") as f:
        restaurants = json.load(f)

    db = SessionLocal()
    stats = {
        "restaurants_created": 0, "restaurants_updated": 0,
        "products_created": 0, "products_updated": 0,
        "food_types_created": 0, "cuisines_created": 0, "flavor_tags_created": 0,
    }
    food_type_cache, cuisine_cache, flavor_tag_cache = {}, {}, {}
    ft_created, cu_created, fl_created = [], [], []

    try:
        restaurant_by_code = {}
        for r in restaurants:
            restaurant, created = upsert_restaurant(db, r, args.area)
            restaurant_by_code[r.get("source_restaurant_code")] = restaurant
            stats["restaurants_created" if created else "restaurants_updated"] += 1

        for p in products:
            restaurant = restaurant_by_code.get(p.get("source_restaurant_code"))
            if restaurant is None:
                print(f"  [skip] {p['name']}: no matching restaurant for "
                      f"source_restaurant_code={p.get('source_restaurant_code')!r}")
                continue

            food_type = get_or_create_food_type(db, food_type_cache, ft_created, p.get("food_type_leaf"), p.get("food_type_parent"))
            cuisine = get_or_create_cuisine(db, cuisine_cache, cu_created, p.get("cuisine"))
            flavor_tags = [get_or_create_flavor_tag(db, flavor_tag_cache, fl_created, name) for name in p.get("flavor_tags", [])]

            product, created = upsert_product(db, p, restaurant.id, food_type.id if food_type else None)
            sync_product_links(db, product, cuisine, flavor_tags)
            stats["products_created" if created else "products_updated"] += 1

        stats["food_types_created"] = len(ft_created)
        stats["cuisines_created"] = len(cu_created)
        stats["flavor_tags_created"] = len(fl_created)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Restaurants: {stats['restaurants_created']} created, {stats['restaurants_updated']} updated")
    print(f"Products: {stats['products_created']} created, {stats['products_updated']} updated")
    print(f"New FoodTypes: {stats['food_types_created']}, "
          f"new Cuisines: {stats['cuisines_created']}, "
          f"new FlavorTags: {stats['flavor_tags_created']}")


if __name__ == "__main__":
    main()
