"""Food sub type browse helpers — dish image pools for UI cycling."""

from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas


def _subtype_dish_counts(db: Session, sub_type_ids: list[int]) -> dict[int, int]:
    """Active product count per sub type, in one grouped query (no N+1)."""
    if not sub_type_ids:
        return {}
    return dict(
        db.query(models.Product.food_sub_type_id, func.count(models.Product.id))
        .filter(
            models.Product.food_sub_type_id.in_(sub_type_ids),
            models.Product.is_active.is_(True),
        )
        .group_by(models.Product.food_sub_type_id)
        .all()
    )


def _subtype_image_pools(
    db: Session, sub_type_ids: list[int], *, limit: int, strategy: str
) -> dict[int, list[str]]:
    """Deduped image URLs per sub type, capped at `limit`, in one query.

    Fetches (sub_type_id, image_url) for all requested sub types ordered by the
    chosen strategy, then buckets in Python - one round trip instead of one per
    sub type.
    """
    pools: dict[int, list[str]] = {sid: [] for sid in sub_type_ids}
    if not sub_type_ids:
        return pools

    query = (
        db.query(models.Product.food_sub_type_id, models.Product.image_url)
        .filter(
            models.Product.food_sub_type_id.in_(sub_type_ids),
            models.Product.is_active.is_(True),
            models.Product.image_url.isnot(None),
            models.Product.image_url != "",
        )
    )
    if strategy == "top_reviewed":
        query = query.order_by(models.Product.review_count.desc(), models.Product.id.asc())
    else:
        query = query.order_by(models.Product.id.asc())

    seen: dict[int, set[str]] = {sid: set() for sid in sub_type_ids}
    for sub_type_id, image_url in query.all():
        bucket = pools[sub_type_id]
        if len(bucket) >= limit or image_url in seen[sub_type_id]:
            continue
        seen[sub_type_id].add(image_url)
        bucket.append(image_url)
    return pools


def build_food_sub_type_list(
    db: Session,
    food_type: models.FoodType,
    *,
    image_limit: int = 20,
    strategy: str = "cycle_all",
) -> schemas.FoodSubTypeListResult:
    sub_types = (
        db.query(models.FoodSubType)
        .filter(models.FoodSubType.food_type_id == food_type.id)
        .order_by(models.FoodSubType.name)
        .all()
    )

    ids = [st.id for st in sub_types]
    counts = _subtype_dish_counts(db, ids)
    pools = _subtype_image_pools(db, ids, limit=image_limit, strategy=strategy)

    results = [
        schemas.FoodSubTypeOut(
            id=sub_type.id,
            name=sub_type.name,
            food_type_id=sub_type.food_type_id,
            dish_count=counts.get(sub_type.id, 0),
            image_urls=pools.get(sub_type.id, []),
        )
        for sub_type in sub_types
    ]

    return schemas.FoodSubTypeListResult(
        food_type=schemas.FoodTypeOut(
            id=food_type.id,
            name=food_type.name,
            description=None,
            image_url=None,
            parent_id=None,
        ),
        sub_types=results,
    )
