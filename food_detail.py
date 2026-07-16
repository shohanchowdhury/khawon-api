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
) -> list[schemas.BrandListOut]:
    """Brands serving at least one active product of this food type. Brand
    cards, not branches: Bella Italia appears once even if all three branches
    serve pizza."""
    from brand_browse import build_brand_list  # brand_browse has no dependency back on this module

    chain_ids = [
        row[0]
        for row in db.query(models.Restaurant.chain_id)
        .join(models.Product, models.Product.restaurant_id == models.Restaurant.id)
        .filter(
            models.Product.food_type_id == food_type_id,
            models.Product.is_active.is_(True),
            models.Restaurant.is_active.is_(True),
        )
        .distinct()
        .all()
    ]

    brands = build_brand_list(db, chain_ids)
    brands.sort(key=lambda b: (b.display_rating is None, -(b.display_rating or 0), b.name.lower()))
    return brands


def build_food_detail(db: Session, food_type: models.FoodType) -> schemas.FoodDetailResult:
    return schemas.FoodDetailResult(
        food_type=enrich_food_type(db, food_type),
        restaurants=get_restaurants_for_food_type(db, food_type.id),
    )
