"""Food sub type browse helpers — dish image pools for UI cycling."""

from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas
from product_image_pools import product_image_pools


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
    pools = product_image_pools(
        db,
        group_column=models.Product.food_sub_type_id,
        entity_ids=ids,
        limit=image_limit,
        strategy=strategy,
    )

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
