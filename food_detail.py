"""Food-type detail + stats.

v2 note: food_types is now a bare lookup (id, name) - the description / image /
parent hierarchy columns the v1 UI used were dropped when the schema was
slimmed and the rich browsable entity became canonical_dishes. Those fields are
surfaced as None here so the existing contract still validates; restore them by
re-adding columns to schema.sql if the food-type detail pages need them again.
"""

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

import models
import schemas


def enrich_food_type(db: Session, food_type: models.FoodType) -> schemas.FoodTypePopularOut:
    """Food-type stats, derived from products: how many restaurants serve a
    product of this type, and the review stats of those products."""
    restaurant_count = (
        db.query(func.count(func.distinct(models.Product.restaurant_id)))
        .filter(models.Product.food_type_id == food_type.id, models.Product.is_active.is_(True))
        .scalar()
        or 0
    )

    review_row = (
        db.query(
            func.avg(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .join(models.Product, models.ProductReview.product_id == models.Product.id)
        .filter(
            models.Product.food_type_id == food_type.id,
            models.ProductReview.status == "approved",
        )
        .first()
    )
    avg_raw, review_count = review_row if review_row else (None, 0)
    avg_rating = round(float(avg_raw), 1) if avg_raw else None

    return schemas.FoodTypePopularOut(
        id=food_type.id,
        name=food_type.name,
        description=None,
        image_url=None,
        parent_id=None,
        restaurant_count=restaurant_count,
        review_count=review_count or 0,
        average_rating=avg_rating,
    )


def get_restaurants_for_food_type(
    db: Session, food_type_id: int
) -> list[schemas.RestaurantOut]:
    """Restaurants serving at least one active product of this food type, with
    rating stats scoped to their products of this type."""
    restaurant_ids = [
        row[0]
        for row in db.query(models.Product.restaurant_id)
        .filter(models.Product.food_type_id == food_type_id, models.Product.is_active.is_(True))
        .distinct()
        .all()
    ]

    if not restaurant_ids:
        return []

    # Per-restaurant rating stats, scoped to this food type's products
    rating_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Product.restaurant_id,
            func.avg(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .join(models.ProductReview, models.ProductReview.product_id == models.Product.id)
        .filter(
            models.Product.food_type_id == food_type_id,
            models.Product.restaurant_id.in_(restaurant_ids),
            models.ProductReview.status == "approved",
        )
        .group_by(models.Product.restaurant_id)
        .all()
    }

    restaurants = (
        db.query(models.Restaurant)
        .options(
            joinedload(models.Restaurant.chain),
            joinedload(models.Restaurant.cuisine_links).joinedload(
                models.RestaurantCuisine.cuisine
            ),
        )
        .filter(models.Restaurant.id.in_(restaurant_ids))
        .all()
    )

    # local import avoids a circular import (restaurants router imports dish_detail)
    from routers.restaurants import _food_types_for_restaurants, _restaurant_out

    food_types_by_restaurant = _food_types_for_restaurants(db, restaurant_ids)

    results = []
    for restaurant in restaurants:
        avg_raw, review_count = rating_stats.get(restaurant.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None
        results.append(
            _restaurant_out(
                restaurant,
                food_types_by_restaurant.get(restaurant.id, []),
                avg_rating,
                review_count or 0,
            )
        )

    results.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))
    return results


def build_food_detail(db: Session, food_type: models.FoodType) -> schemas.FoodDetailResult:
    return schemas.FoodDetailResult(
        food_type=enrich_food_type(db, food_type),
        restaurants=get_restaurants_for_food_type(db, food_type.id),
    )
