from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func

import models
import schemas


def enrich_food_type(db: Session, food_type: models.FoodType) -> schemas.FoodTypePopularOut:
    restaurant_count = (
        db.query(func.count(models.RestaurantFoodType.restaurant_id))
        .filter(models.RestaurantFoodType.food_type_id == food_type.id)
        .scalar()
        or 0
    )

    review_row = (
        db.query(
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(models.Review.food_type_id == food_type.id)
        .first()
    )
    avg_raw, review_count = review_row if review_row else (None, 0)
    avg_rating = round(float(avg_raw), 1) if avg_raw else None

    return schemas.FoodTypePopularOut(
        id=food_type.id,
        name=food_type.name,
        description=food_type.description,
        image_url=food_type.image_url,
        taste_tags=food_type.taste_tags,
        restaurant_count=restaurant_count,
        review_count=review_count or 0,
        average_rating=avg_rating,
    )


def get_restaurants_for_food_type(
    db: Session, food_type_id: int
) -> list[schemas.RestaurantOut]:
    restaurant_ids = [
        row[0]
        for row in db.query(models.RestaurantFoodType.restaurant_id)
        .filter(models.RestaurantFoodType.food_type_id == food_type_id)
        .all()
    ]

    if not restaurant_ids:
        return []

    rating_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Review.restaurant_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(
            models.Review.food_type_id == food_type_id,
            models.Review.restaurant_id.in_(restaurant_ids),
        )
        .group_by(models.Review.restaurant_id)
        .all()
    }

    restaurants = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.id.in_(restaurant_ids))
        .options(
            joinedload(models.Restaurant.food_type_links).joinedload(
                models.RestaurantFoodType.food_type
            )
        )
        .all()
    )

    results = []
    for restaurant in restaurants:
        avg_raw, review_count = rating_stats.get(restaurant.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None
        food_types = [link.food_type for link in restaurant.food_type_links]

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
