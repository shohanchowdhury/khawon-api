"""Shared helpers for pooling product image URLs by a grouping column."""

import random

from sqlalchemy.orm import Session

import models


def product_image_pools(
    db: Session,
    *,
    group_column,
    entity_ids: list[int],
    limit: int = 20,
    strategy: str = "cycle_all",
) -> dict[int, list[str]]:
    """Deduped image URLs per entity, capped at `limit`, in one query.

    Fetches (group_id, image_url) for all requested entities ordered by the
    chosen strategy, then buckets in Python - one round trip instead of one per
    entity.
    """
    pools: dict[int, list[str]] = {eid: [] for eid in entity_ids}
    if not entity_ids:
        return pools

    query = (
        db.query(group_column, models.Product.image_url)
        .filter(
            group_column.in_(entity_ids),
            models.Product.is_active.is_(True),
            models.Product.image_url.isnot(None),
            models.Product.image_url != "",
        )
    )
    if strategy == "top_reviewed":
        query = query.order_by(models.Product.review_count.desc(), models.Product.id.asc())
    else:
        query = query.order_by(models.Product.id.asc())

    seen: dict[int, set[str]] = {eid: set() for eid in entity_ids}
    for entity_id, image_url in query.all():
        bucket = pools[entity_id]
        if len(bucket) >= limit or image_url in seen[entity_id]:
            continue
        seen[entity_id].add(image_url)
        bucket.append(image_url)
    return pools


def pick_random_url(urls: list[str]) -> str | None:
    """Return one random URL from a non-empty pool, else None."""
    filtered = [url for url in urls if url]
    if not filtered:
        return None
    return random.choice(filtered)
