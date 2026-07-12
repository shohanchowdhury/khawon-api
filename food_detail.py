from sqlalchemy.orm import Session
from sqlalchemy import func

import models
import schemas


def enrich_food_type(db: Session, food_type: models.FoodType) -> schemas.FoodTypePopularOut:
    """Food-type stats, derived from dishes: how many restaurants serve a dish
    of this type, and the review stats of those dishes."""
    restaurant_count = (
        db.query(func.count(func.distinct(models.Dish.restaurant_id)))
        .filter(models.Dish.food_type_id == food_type.id, models.Dish.is_active.is_(True))
        .scalar()
        or 0
    )

    review_row = (
        db.query(
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .join(models.Dish, models.Review.dish_id == models.Dish.id)
        .filter(models.Dish.food_type_id == food_type.id)
        .first()
    )
    avg_raw, review_count = review_row if review_row else (None, 0)
    avg_rating = round(float(avg_raw), 1) if avg_raw else None

    return schemas.FoodTypePopularOut(
        id=food_type.id,
        name=food_type.name,
        description=food_type.description,
        image_url=food_type.image_url,
        parent_id=food_type.parent_id,
        restaurant_count=restaurant_count,
        review_count=review_count or 0,
        average_rating=avg_rating,
    )


def get_restaurants_for_food_type(
    db: Session, food_type_id: int
) -> list[schemas.RestaurantOut]:
    """Restaurants serving at least one active dish of this food type, with
    rating stats scoped to their dishes of this type."""
    restaurant_ids = [
        row[0]
        for row in db.query(models.Dish.restaurant_id)
        .filter(models.Dish.food_type_id == food_type_id, models.Dish.is_active.is_(True))
        .distinct()
        .all()
    ]

    if not restaurant_ids:
        return []

    # Per-restaurant rating stats, scoped to this food type's dishes
    rating_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Dish.restaurant_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .join(models.Review, models.Review.dish_id == models.Dish.id)
        .filter(
            models.Dish.food_type_id == food_type_id,
            models.Dish.restaurant_id.in_(restaurant_ids),
        )
        .group_by(models.Dish.restaurant_id)
        .all()
    }

    # Each restaurant's full set of food types (derived from all its dishes)
    food_types_by_restaurant: dict[int, list[models.FoodType]] = {}
    for restaurant_id, food_type in (
        db.query(models.Dish.restaurant_id, models.FoodType)
        .join(models.FoodType, models.Dish.food_type_id == models.FoodType.id)
        .filter(models.Dish.restaurant_id.in_(restaurant_ids), models.Dish.is_active.is_(True))
        .distinct()
        .all()
    ):
        food_types_by_restaurant.setdefault(restaurant_id, []).append(food_type)

    restaurants = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.id.in_(restaurant_ids))
        .all()
    )

    results = []
    for restaurant in restaurants:
        avg_raw, review_count = rating_stats.get(restaurant.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None
        food_types = food_types_by_restaurant.get(restaurant.id, [])

        results.append(
            schemas.RestaurantOut(
                id=restaurant.id,
                name=restaurant.name,
                area=restaurant.area,
                address=restaurant.address,
                phone=restaurant.phone,
                google_maps_url=restaurant.google_maps_url,
                website_url=restaurant.website_url,
                google_place_id=restaurant.google_place_id,
                image_url=restaurant.image_url,
                food_types=[
                    schemas.FoodTypeOut.model_validate(ft) for ft in food_types
                ],
                average_rating=avg_rating,
                review_count=review_count or 0,
            )
        )

    results.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))
    return results


def build_food_detail(db: Session, food_type: models.FoodType) -> schemas.FoodDetailResult:
    return schemas.FoodDetailResult(
        food_type=enrich_food_type(db, food_type),
        restaurants=get_restaurants_for_food_type(db, food_type.id),
    )
