"""Brand browse helpers -- assemble BrandListOut cards for a set of chains.

Shared by the restaurants router (browse/catalogue) and food_detail (brands
serving a food type). 'Restaurant' in the API means brand; a standalone
restaurant is a brand of one.
"""

import collections

from sqlalchemy import or_
from sqlalchemy.orm import Session

import models
import schemas
from restaurant_reviews import brand_display_rating


def build_brand_list(db: Session, chain_ids: list[int]) -> list[schemas.BrandListOut]:
    """BrandListOut for the given chains, in the given order, via a fixed
    number of grouped queries (no per-brand N+1)."""
    if not chain_ids:
        return []

    chains = {
        c.id: c for c in db.query(models.RestaurantChain)
        .filter(models.RestaurantChain.id.in_(chain_ids)).all()
    }
    branches_by_chain: dict[int, list[models.Restaurant]] = collections.defaultdict(list)
    for r in (
        db.query(models.Restaurant)
        .filter(models.Restaurant.chain_id.in_(chain_ids), models.Restaurant.is_active.is_(True))
        .all()
    ):
        branches_by_chain[r.chain_id].append(r)

    food_types_by_chain: dict[int, list] = collections.defaultdict(list)
    for chain_id, ft_id, ft_name in (
        db.query(models.Restaurant.chain_id, models.FoodType.id, models.FoodType.name)
        .join(models.Product, models.Product.restaurant_id == models.Restaurant.id)
        .join(models.FoodType, models.FoodType.id == models.Product.food_type_id)
        .filter(models.Restaurant.chain_id.in_(chain_ids), models.Product.is_active.is_(True))
        .distinct()
        .all()
    ):
        food_types_by_chain[chain_id].append(schemas.FoodTypeOut(id=ft_id, name=ft_name))

    cuisines_by_chain: dict[int, set] = collections.defaultdict(set)
    for chain_id, cuisine_name in (
        db.query(models.Restaurant.chain_id, models.Cuisine.name)
        .join(models.RestaurantCuisine, models.RestaurantCuisine.restaurant_id == models.Restaurant.id)
        .join(models.Cuisine, models.Cuisine.id == models.RestaurantCuisine.cuisine_id)
        .filter(models.Restaurant.chain_id.in_(chain_ids))
        .distinct()
        .all()
    ):
        cuisines_by_chain[chain_id].add(cuisine_name)

    out = []
    for chain_id in chain_ids:
        branches = branches_by_chain.get(chain_id, [])
        if not branches:
            continue
        rating, count, source = brand_display_rating(db, branches)
        chain = chains.get(chain_id)
        out.append(schemas.BrandListOut(
            id=chain_id,
            slug=chain.chain_code if chain else str(chain_id),
            name=chain.name if chain else branches[0].name,
            branch_count=len(branches),
            areas=sorted({b.area for b in branches if b.area}),
            image_url=next((b.hero_image_url for b in branches if b.hero_image_url), None),
            food_types=sorted(food_types_by_chain.get(chain_id, []), key=lambda f: f.name),
            cuisines=sorted(cuisines_by_chain.get(chain_id, set())),
            display_rating=rating,
            display_rating_source=source,
            display_review_count=count,
        ))
    return out


def matching_chain_ids(db: Session, q: str | None):
    """Chains with at least one active branch, optionally filtered by brand
    name / branch name / area / address. Ordered by brand name."""
    query = (
        db.query(models.RestaurantChain.id, models.RestaurantChain.name)
        .join(models.Restaurant, models.Restaurant.chain_id == models.RestaurantChain.id)
        .filter(models.Restaurant.is_active.is_(True))
    )
    if q:
        pattern = f"%{q}%"
        query = query.filter(or_(
            models.RestaurantChain.name.ilike(pattern),
            models.Restaurant.name.ilike(pattern),
            models.Restaurant.area.ilike(pattern),
            models.Restaurant.address.ilike(pattern),
        ))
    return query.distinct().order_by(models.RestaurantChain.name)
