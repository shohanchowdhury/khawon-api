"""Load a classify_batch.py + match_google_places.py output pair into the DB.

USAGE:
    python load_batch.py products.json restaurants.json [--area Dhanmondi]
    python load_batch.py --sync-links-only

products.json    - classify_batch.py's *_products.json (has source_restaurant_code,
                    full product fields, and food_type_leaf/parent/cuisine/flavor_tags)
restaurants.json - classify_batch.py's *_restaurants.json, after match_google_places.py
                    has filled in google_place_id/match_status

Upserts Restaurant by source_restaurant_code and Dish by
(restaurant_id, source_dish_id) - safe to re-run on the same files.
(The pipeline JSON still calls the foodpanda id "product_id"; it maps to the
dishes.source_dish_id column.)
FoodType/Cuisine/FlavorTag rows are get-or-created by name.
After dish upsert, restaurant_food_types is rebuilt from dishes for batch restaurants
(or all restaurants with dishes when using --sync-links-only).

Written for bulk performance over a high-latency connection (e.g. Railway's
public Postgres proxy, ~220ms/round-trip): new rows go in via a handful of
Core multi-row INSERT ... RETURNING statements rather than one INSERT per row.
Existing rows (only on a re-run) are updated via executemany, which is slower
per-row but rare. A fresh load is therefore all bulk inserts = a few dozen
round-trips total, not tens of thousands.
"""
import argparse
import json
from datetime import datetime, timezone

from sqlalchemy import bindparam, delete, insert, update

from database import SessionLocal
import models


def _restaurant_values(r, area):
    coords = r.get("coordinates") or {}
    images = r.get("images") or {}
    return {
        "source_restaurant_code": r.get("source_restaurant_code"),
        "name": r["name"],
        "address": r.get("address"),
        "area": area,
        "latitude": coords.get("latitude"),
        "longitude": coords.get("longitude"),
        "raw_cuisines": r.get("cuisines") or [],
        "foodpanda_rating": r.get("rating"),
        "foodpanda_review_number": r.get("review_number"),
        "budget": r.get("budget"),
        "image_url": images.get("hero"),
        "logo_url": images.get("logo"),
        "chain_name": r.get("chain_name"),
        "chain_code": r.get("chain_code"),
        "google_place_id": r.get("google_place_id"),
        "match_status": r.get("match_status") or "unmatched",
    }


def _dish_values(p, restaurant_id, food_type_id, seen_at):
    return {
        "restaurant_id": restaurant_id,
        "source_dish_id": p["product_id"],   # foodpanda product id from the pipeline JSON
        "food_type_id": food_type_id,
        "name": p["name"],
        "description": p.get("description"),
        "price_bdt": p.get("price_bdt"),
        "image_url": p.get("image"),
        "is_sold_out": p.get("is_sold_out", False),
        "category_raw": p.get("category"),
        "dietary_attributes_raw": p.get("dietary_attributes") or [],
        "variations": p.get("variations") or [],
        "is_active": True,
        "last_seen_at": seen_at,
    }


def _bulk_insert_returning(db, model, rows, *return_cols):
    """Bulk-insert `rows` (list of dicts) in one Core statement, returning the
    requested columns. Returns the RETURNING result rows (order not assumed -
    callers key off the returned natural-key columns)."""
    if not rows:
        return []
    stmt = insert(model).returning(*return_cols)
    return list(db.execute(stmt, rows))


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

    try:
        # ---- Taxonomy: existing name -> id ----
        ft_id = {name: id_ for name, id_ in db.query(models.FoodType.name, models.FoodType.id)}
        cu_id = {name: id_ for name, id_ in db.query(models.Cuisine.name, models.Cuisine.id)}
        fl_id = {name: id_ for name, id_ in db.query(models.FlavorTag.name, models.FlavorTag.id)}

        # Collect needed taxonomy from the products file.
        ft_parent = {}   # name -> parent_name (None for roots)
        need_cuisines, need_flavors = set(), set()
        for p in products:
            leaf, parent = p.get("food_type_leaf"), p.get("food_type_parent")
            if leaf:
                ft_parent.setdefault(leaf, parent if parent and parent != leaf else None)
            if parent and parent != leaf:
                ft_parent.setdefault(parent, None)
            if p.get("cuisine"):
                need_cuisines.add(p["cuisine"])
            for name in p.get("flavor_tags", []):
                need_flavors.add(name)

        # Insert new roots first (parent None), then new leaves (parent_id resolved).
        new_roots = [n for n, par in ft_parent.items() if par is None and n not in ft_id]
        for row in _bulk_insert_returning(
            db, models.FoodType, [{"name": n} for n in new_roots],
            models.FoodType.name, models.FoodType.id,
        ):
            ft_id[row.name] = row.id
        new_leaves = [n for n, par in ft_parent.items() if par is not None and n not in ft_id]
        for row in _bulk_insert_returning(
            db, models.FoodType,
            [{"name": n, "parent_id": ft_id.get(ft_parent[n])} for n in new_leaves],
            models.FoodType.name, models.FoodType.id,
        ):
            ft_id[row.name] = row.id
        stats["food_types_created"] = len(new_roots) + len(new_leaves)

        new_cuisines = [n for n in need_cuisines if n not in cu_id]
        for row in _bulk_insert_returning(
            db, models.Cuisine, [{"name": n} for n in new_cuisines],
            models.Cuisine.name, models.Cuisine.id,
        ):
            cu_id[row.name] = row.id
        stats["cuisines_created"] = len(new_cuisines)

        new_flavors = [n for n in need_flavors if n not in fl_id]
        for row in _bulk_insert_returning(
            db, models.FlavorTag, [{"name": n} for n in new_flavors],
            models.FlavorTag.name, models.FlavorTag.id,
        ):
            fl_id[row.name] = row.id
        stats["flavor_tags_created"] = len(new_flavors)

        # ---- Restaurants: split new vs existing by source_restaurant_code ----
        codes = [r.get("source_restaurant_code") for r in restaurants]
        code_to_id = {
            code: id_
            for code, id_ in db.query(
                models.Restaurant.source_restaurant_code, models.Restaurant.id
            ).filter(models.Restaurant.source_restaurant_code.in_(codes))
        }
        new_rest_rows, update_rest_rows = [], []
        for r in restaurants:
            vals = _restaurant_values(r, args.area)
            code = vals["source_restaurant_code"]
            if code in code_to_id:
                update_rest_rows.append({**vals, "_id": code_to_id[code]})
                stats["restaurants_updated"] += 1
            else:
                new_rest_rows.append(vals)
                stats["restaurants_created"] += 1

        for row in _bulk_insert_returning(
            db, models.Restaurant, new_rest_rows,
            models.Restaurant.source_restaurant_code, models.Restaurant.id,
        ):
            code_to_id[row.source_restaurant_code] = row.id
        if update_rest_rows:
            cols = [k for k in update_rest_rows[0] if k not in ("_id", "source_restaurant_code")]
            stmt = (
                update(models.Restaurant.__table__)
                .where(models.Restaurant.__table__.c.id == bindparam("_id"))
                .values({c: bindparam(c) for c in cols})
            )
            db.execute(stmt, update_rest_rows)

        # ---- Dishes: split new vs existing by (restaurant_id, source_dish_id) ----
        restaurant_ids = list(code_to_id.values())
        existing_dishes = {}
        if restaurant_ids:
            for did, rid, sdid in db.query(
                models.Dish.id, models.Dish.restaurant_id, models.Dish.source_dish_id
            ).filter(models.Dish.restaurant_id.in_(restaurant_ids)):
                existing_dishes[(rid, sdid)] = did

        seen_at = datetime.now(timezone.utc)
        new_dish_rows, update_dish_rows = [], []
        seen_keys = set()   # (rid, sdid) present in this batch, for deactivation below
        # desired joins keyed by dish natural key, resolved to dish ids after insert
        want_cuisine = {}   # (rid, sdid) -> cuisine_id
        want_flavors = {}   # (rid, sdid) -> [flavor_id, ...]
        for p in products:
            rid = code_to_id.get(p.get("source_restaurant_code"))
            if rid is None:
                print(f"  [skip] {p['name']}: no restaurant for "
                      f"source_restaurant_code={p.get('source_restaurant_code')!r}")
                continue
            leaf = p.get("food_type_leaf")
            food_type_id = ft_id.get(leaf) if leaf else None
            vals = _dish_values(p, rid, food_type_id, seen_at)
            key = (rid, p["product_id"])
            seen_keys.add(key)
            if key in existing_dishes:
                update_dish_rows.append({**vals, "_id": existing_dishes[key]})
                stats["products_updated"] += 1
            else:
                new_dish_rows.append(vals)
                stats["products_created"] += 1
            if p.get("cuisine") and p["cuisine"] in cu_id:
                want_cuisine[key] = cu_id[p["cuisine"]]
            want_flavors[key] = [fl_id[n] for n in p.get("flavor_tags", []) if n in fl_id]

        did_by_key = dict(existing_dishes)
        for row in _bulk_insert_returning(
            db, models.Dish, new_dish_rows,
            models.Dish.id, models.Dish.restaurant_id, models.Dish.source_dish_id,
        ):
            did_by_key[(row.restaurant_id, row.source_dish_id)] = row.id
        if update_dish_rows:
            cols = [k for k in update_dish_rows[0] if k != "_id"]
            stmt = (
                update(models.Dish.__table__)
                .where(models.Dish.__table__.c.id == bindparam("_id"))
                .values({c: bindparam(c) for c in cols})
            )
            db.execute(stmt, update_dish_rows)

        # Menu lifecycle: dishes of the loaded restaurants that were NOT in
        # this batch have vanished from the menu - mark inactive, never delete
        # (their reviews survive; they reactivate if a later scrape has them).
        vanished_ids = [
            dish_id for key, dish_id in existing_dishes.items() if key not in seen_keys
        ]
        if vanished_ids:
            db.execute(
                update(models.Dish.__table__)
                .where(models.Dish.__table__.c.id.in_(vanished_ids))
                .values(is_active=False)
            )
        stats["dishes_deactivated"] = len(vanished_ids)

        # ---- Join tables: clear existing dishes' links, then bulk insert all ----
        updated_ids = [existing_dishes[k] for k in existing_dishes]
        if updated_ids:
            db.execute(delete(models.DishCuisine).where(
                models.DishCuisine.dish_id.in_(updated_ids)))
            db.execute(delete(models.DishFlavorTag).where(
                models.DishFlavorTag.dish_id.in_(updated_ids)))

        cuisine_join_rows, flavor_join_rows = [], []
        for key, cuisine_id in want_cuisine.items():
            did = did_by_key.get(key)
            if did is not None:
                cuisine_join_rows.append({"dish_id": did, "cuisine_id": cuisine_id})
        for key, flavor_ids in want_flavors.items():
            did = did_by_key.get(key)
            if did is None:
                continue
            for flavor_id in flavor_ids:
                flavor_join_rows.append({"dish_id": did, "flavor_tag_id": flavor_id})
        if cuisine_join_rows:
            db.execute(insert(models.DishCuisine), cuisine_join_rows)
        if flavor_join_rows:
            db.execute(insert(models.DishFlavorTag), flavor_join_rows)

        db.commit()
    except Exception:
        db.rollback()
        raise
    finally:
        db.close()

    print(f"Restaurants: {stats['restaurants_created']} created, {stats['restaurants_updated']} updated")
    print(f"Dishes: {stats['products_created']} created, {stats['products_updated']} updated, "
          f"{stats.get('dishes_deactivated', 0)} deactivated (vanished from menu)")
    print(f"New FoodTypes: {stats['food_types_created']}, "
          f"new Cuisines: {stats['cuisines_created']}, "
          f"new FlavorTags: {stats['flavor_tags_created']}")


if __name__ == "__main__":
    main()
