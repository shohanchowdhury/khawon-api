"""Brand dish cards: collapse a brand's branches into one card.

The card is a GROUPING, not an entity -- there is no chain_dishes table. Name,
price range, availability and pooled rating are all derived from the per-branch
product rows, which must stay for per-branch reviews, availability and future
map pins.

Key = (chain_id, food_type_id, normalized_name). food_type_id is required, not
decoration: without it a brand's "Chicken" curry fuses with its "Chicken"
pizza (the canonical bootstrap learned this the hard way).
"""

import collections
import re

from sqlalchemy import func
from sqlalchemy.orm import Session

import models
import schemas


def brand_key(p: models.Product) -> tuple:
    return (p.restaurant.chain_id, p.food_type_id, p.normalized_name)


def dish_slug(normalized_name: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (normalized_name or "").lower()).strip("-")


def _branch_totals(db: Session, chain_ids: list[int]) -> dict[int, int]:
    """How many branches each brand has overall (the '3' in 'at 2 of 3')."""
    if not chain_ids:
        return {}
    return dict(
        db.query(models.Restaurant.chain_id, func.count(models.Restaurant.id))
        .filter(models.Restaurant.chain_id.in_(chain_ids),
                models.Restaurant.is_active.is_(True))
        .group_by(models.Restaurant.chain_id)
        .all()
    )


def _review_stats(db: Session, product_ids: list[int]) -> dict[int, tuple]:
    """(sum_rating, count) per product, APPROVED only -- summed so the caller
    can pool across a brand's branches without re-querying."""
    if not product_ids:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.ProductReview.product_id,
            func.sum(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .filter(models.ProductReview.product_id.in_(product_ids),
                models.ProductReview.status == "approved")
        .group_by(models.ProductReview.product_id)
        .all()
    }


def build_brand_dishes(db: Session, products: list[models.Product]) -> list[schemas.BrandDishOut]:
    if not products:
        return []

    groups: dict[tuple, list[models.Product]] = collections.defaultdict(list)
    for p in products:
        groups[brand_key(p)].append(p)

    totals = _branch_totals(db, [p.restaurant.chain_id for p in products])
    stats = _review_stats(db, [p.id for p in products])

    cards: list[schemas.BrandDishOut] = []
    for (chain_id, food_type_id, normalized_name), members in groups.items():
        prices = [float(m.base_price_bdt) for m in members]
        rating_sum = sum(stats.get(m.id, (0, 0))[0] or 0 for m in members)
        rating_n = sum(stats.get(m.id, (0, 0))[1] or 0 for m in members)
        # display name = most common raw spelling among the branches
        display = collections.Counter(m.name.strip() for m in members).most_common(1)[0][0]
        first = members[0]
        cards.append(schemas.BrandDishOut(
            brand=schemas.BrandOut(id=chain_id, name=first.restaurant.chain.name),
            food_type_id=food_type_id,
            slug=dish_slug(normalized_name),
            name=display,
            description=first.description,
            image_url=next((m.image_url for m in members if m.image_url), None),
            category_raw=first.category.name if first.category else None,
            food_type=schemas.FoodTypeOut(id=first.food_type.id, name=first.food_type.name)
                      if first.food_type else None,
            cuisines=[schemas.CuisineOut.model_validate(first.cuisine)] if first.cuisine else [],
            flavor_tags=[schemas.FlavorTagOut(id=l.flavor_tag.id, name=l.flavor_tag.label)
                         for l in first.flavor_tag_links],
            canonical_dish_id=first.canonical_dish_id,
            price_min_bdt=min(prices),
            price_max_bdt=max(prices),
            price_varies=min(prices) != max(prices),
            branch_count=len({m.restaurant_id for m in members}),
            brand_branch_total=totals.get(chain_id, len({m.restaurant_id for m in members})),
            is_sold_out_everywhere=all(m.is_sold_out for m in members),
            average_rating=round(rating_sum / rating_n, 1) if rating_n else None,
            review_count=rating_n,
        ))
    return cards
