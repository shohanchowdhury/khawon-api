"""Restaurant-level review helpers (the restaurant_reviews stack).

Distinct from product/dish reviews: these rate the overall restaurant
experience. A restaurant's rating/review_count are derived live from its
APPROVED restaurant_reviews (mirrors the dish-review approach). Post-moderation:
new reviews are inserted status='approved' and shown immediately.
"""

from sqlalchemy import func
from sqlalchemy.orm import Session, joinedload

import models
import review_edits
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


def restaurant_review_to_out(
    review: models.RestaurantReview, edits: dict | None = None
) -> schemas.RestaurantReviewOut:
    """`edits` is this review's entry from review_edits.edit_stats(), or None
    when it was never edited. Batch that lookup when listing many reviews."""
    edits = edits or {}
    return schemas.RestaurantReviewOut(
        id=review.id,
        restaurant_id=review.restaurant_id,
        branch_name=review.restaurant.name if review.restaurant else None,
        branch_area=review.restaurant.area if review.restaurant else None,
        username=review.user.display_name,
        rating=review.rating,
        comment=review.body,
        is_verified=review.is_verified_visit,
        created_at=review.created_at,
        is_edited=bool(edits),
        edit_count=edits.get("edit_count", 0),
        original_rating=edits.get("original_rating"),
        last_edited_at=edits.get("last_edited_at"),
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
        base.options(joinedload(models.RestaurantReview.user),
                     joinedload(models.RestaurantReview.restaurant))
        .order_by(models.RestaurantReview.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    edits = review_edits.edit_stats(db, models.RestaurantReviewEdit, [r.id for r in reviews])
    return [restaurant_review_to_out(r, edits.get(r.id)) for r in reviews], total


def get_reviews_for_brand(
    db: Session, chain_id: int, *, offset: int = 0, limit: int = 20
) -> tuple[list[schemas.RestaurantReviewOut], int]:
    """All approved location reviews across a brand's branches, newest first.
    Each review carries its branch name/area so the UI can tag the location."""
    base = (
        db.query(models.RestaurantReview)
        .join(models.Restaurant, models.Restaurant.id == models.RestaurantReview.restaurant_id)
        .filter(
            models.Restaurant.chain_id == chain_id,
            models.RestaurantReview.status == "approved",
        )
    )
    total = base.order_by(None).count()
    reviews = (
        base.options(joinedload(models.RestaurantReview.user),
                     joinedload(models.RestaurantReview.restaurant))
        .order_by(models.RestaurantReview.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [restaurant_review_to_out(r) for r in reviews], total


def brand_display_rating(db: Session, branches: list[models.Restaurant]) -> tuple:
    """(rating, count, source) for a brand: pooled khawon location reviews if
    any exist, else the review-count-weighted foodpanda average across
    branches. Swaps to nearest-branch when geo lands."""
    stats = restaurant_review_stats(db, [b.id for b in branches])
    total_n = sum(stats.get(b.id, (None, 0))[1] or 0 for b in branches)
    khawon_avg = (
        round(sum(float(stats[b.id][0]) * stats[b.id][1] for b in branches if b.id in stats) / total_n, 1)
        if total_n else None
    )
    fp = [(float(b.old_rating), b.old_review_count or 0) for b in branches if b.old_rating is not None]
    fp_n = sum(n for _, n in fp)
    fp_avg = round(sum(r * n for r, n in fp) / fp_n, 1) if fp_n else (fp[0][0] if fp else None)
    return resolve_display_rating(khawon_avg, total_n, fp_avg, fp_n)
