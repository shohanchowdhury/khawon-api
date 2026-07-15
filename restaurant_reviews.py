"""Restaurant-level review helpers (the restaurant_reviews stack).

Distinct from product/dish reviews: these rate the overall restaurant
experience. A restaurant's rating/review_count are derived live from its
APPROVED restaurant_reviews (mirrors the dish-review approach). Post-moderation:
new reviews are inserted status='approved' and shown immediately.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

import models
import schemas


def resolve_display_rating(
    khawon_avg: float | None,
    khawon_count: int,
    fp_rating,
    fp_count,
) -> tuple[float | None, int, str | None]:
    """Pick the rating to show: khawon's own if it has any reviews, else the
    foodpanda scraped rating as a fallback while khawon data fills up.
    Returns (rating, count, source) where source is 'khawon' | 'foodpanda' | None
    so the UI can label a borrowed rating honestly."""
    if khawon_avg is not None:
        return khawon_avg, khawon_count, "khawon"
    if fp_rating is not None:
        return float(fp_rating), fp_count or 0, "foodpanda"
    return None, 0, None


def restaurant_review_to_out(review: models.RestaurantReview) -> schemas.RestaurantReviewOut:
    return schemas.RestaurantReviewOut(
        id=review.id,
        restaurant_id=review.restaurant_id,
        username=review.user.display_name,
        rating=review.rating,
        comment=review.body,
        is_verified=review.is_verified_visit,
        created_at=review.created_at,
    )


def restaurant_review_stats(db: Session, restaurant_ids: list[int]) -> dict[int, tuple]:
    """(avg_rating, review_count) per restaurant, from approved restaurant_reviews."""
    if not restaurant_ids:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.RestaurantReview.restaurant_id,
            func.avg(models.RestaurantReview.rating),
            func.count(models.RestaurantReview.id),
        )
        .filter(
            models.RestaurantReview.restaurant_id.in_(restaurant_ids),
            models.RestaurantReview.status == "approved",
        )
        .group_by(models.RestaurantReview.restaurant_id)
        .all()
    }


def get_reviews_for_restaurant(
    db: Session, restaurant_id: int, *, offset: int = 0, limit: int = 20
) -> tuple[list[schemas.RestaurantReviewOut], int]:
    base = (
        db.query(models.RestaurantReview)
        .filter(
            models.RestaurantReview.restaurant_id == restaurant_id,
            models.RestaurantReview.status == "approved",
        )
    )
    total = base.order_by(None).count()
    reviews = (
        base.options(joinedload(models.RestaurantReview.user))
        .order_by(models.RestaurantReview.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [restaurant_review_to_out(r) for r in reviews], total
