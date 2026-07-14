"""Dish (product) query + serialization helpers.

The v2 SQL-first schema renamed the menu-item entity Dish -> Product and made
several attributes single-FK lookups (category/cuisine/food_type) with a
separate canonical_dishes comparison layer. These helpers map the new
`Product` model onto the existing `DishOut` API contract so the frontend
contract stays stable. "Dish" in the API == a Product row.
"""

from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

import models
import schemas


def _product_query(db: Session):
    return db.query(models.Product).options(
        joinedload(models.Product.food_type),
        joinedload(models.Product.category),
        joinedload(models.Product.cuisine),
        joinedload(models.Product.restaurant),
        joinedload(models.Product.variations),
        joinedload(models.Product.flavor_tag_links).joinedload(models.ProductFlavorTag.flavor_tag),
    )


def _product_review_stats(db: Session, product_ids: list[int]) -> dict[int, tuple]:
    """(avg_rating, review_count) per product, from product_reviews."""
    if not product_ids:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.ProductReview.product_id,
            func.avg(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .filter(models.ProductReview.product_id.in_(product_ids))
        .group_by(models.ProductReview.product_id)
        .all()
    }


def _food_type_out(ft: models.FoodType | None) -> schemas.FoodTypeOut | None:
    if ft is None:
        return None
    return schemas.FoodTypeOut(id=ft.id, name=ft.name)


def _variation_out(v: models.ProductVariation) -> schemas.DishVariationOut:
    return schemas.DishVariationOut(label=v.label, price_bdt=float(v.price_bdt))


def _flavor_tag_out(ft: models.FlavorTag) -> schemas.FlavorTagOut:
    # FlavorTag stores slug+label; the API's FlavorTagOut wants id+name.
    return schemas.FlavorTagOut(id=ft.id, name=ft.label)


def enrich_dishes(db: Session, dishes: list[models.Product]) -> list[schemas.DishOut]:
    if not dishes:
        return []

    review_stats = _product_review_stats(db, [d.id for d in dishes])

    results = []
    for d in dishes:
        avg_raw, review_count = review_stats.get(d.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None

        results.append(
            schemas.DishOut(
                id=d.id,
                name=d.name,
                description=d.description,
                price_bdt=float(d.base_price_bdt) if d.base_price_bdt is not None else None,
                image_url=d.image_url,
                is_sold_out=d.is_sold_out,
                is_active=d.is_active,
                category_raw=d.category.name if d.category else None,
                variations=[_variation_out(v) for v in d.variations],
                food_type=_food_type_out(d.food_type),
                canonical_dish_id=d.canonical_dish_id,
                cuisines=[schemas.CuisineOut.model_validate(d.cuisine)] if d.cuisine else [],
                flavor_tags=[_flavor_tag_out(link.flavor_tag) for link in d.flavor_tag_links],
                restaurant=schemas.RestaurantSummaryOut(
                    id=d.restaurant.id,
                    name=d.restaurant.name,
                    area=d.restaurant.area,
                    address=d.restaurant.address,
                    image_url=d.restaurant.hero_image_url,
                    google_place_id=d.restaurant.google_place_id,
                ),
                average_rating=avg_rating,
                review_count=review_count or 0,
            )
        )
    return results


def _sort_by_rating(dishes: list[schemas.DishOut]) -> list[schemas.DishOut]:
    dishes.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))
    return dishes


def _canonical_match_stats(db: Session, canonical: models.CanonicalDish) -> schemas.CanonicalDishMatch:
    row = (
        db.query(
            func.count(func.distinct(models.Product.restaurant_id)),
            func.count(models.Product.id),
            func.min(models.Product.base_price_bdt),
            func.max(models.Product.base_price_bdt),
        )
        .filter(models.Product.canonical_dish_id == canonical.id, models.Product.is_active.is_(True))
        .first()
    )
    restaurant_count, dish_count, min_price, max_price = row or (0, 0, None, None)

    avg_raw = (
        db.query(func.avg(models.ProductReview.rating))
        .join(models.Product, models.ProductReview.product_id == models.Product.id)
        .filter(models.Product.canonical_dish_id == canonical.id)
        .scalar()
    )

    return schemas.CanonicalDishMatch(
        id=canonical.id,
        name=canonical.name,
        food_type=_food_type_out(canonical.food_type),
        aliases=canonical.aliases,
        image_url=canonical.image_url,
        restaurant_count=restaurant_count or 0,
        dish_count=dish_count or 0,
        average_rating=round(float(avg_raw), 1) if avg_raw else None,
        min_price_bdt=float(min_price) if min_price is not None else None,
        max_price_bdt=float(max_price) if max_price is not None else None,
    )


def search_canonical_dishes(db: Session, q: str, limit: int = 10) -> list[schemas.CanonicalDishMatch]:
    """Canonical dishes whose name, aliases, or food type match the query."""
    pattern = f"%{q}%"
    q_lower = q.lower().strip()

    candidates = (
        db.query(models.CanonicalDish)
        .options(joinedload(models.CanonicalDish.food_type))
        .outerjoin(models.FoodType, models.CanonicalDish.food_type_id == models.FoodType.id)
        .filter(or_(
            models.CanonicalDish.name.ilike(pattern),
            models.FoodType.name.ilike(pattern),
            models.CanonicalDish.aliases.isnot(None),
        ))
        .all()
    )

    def matches(c: models.CanonicalDish) -> bool:
        if q_lower in c.name.lower():
            return True
        if c.food_type and q_lower in c.food_type.name.lower():
            return True
        return any(q_lower in (alias or "").lower() for alias in (c.aliases or []))

    matched = [c for c in candidates if matches(c)]
    results = [_canonical_match_stats(db, c) for c in matched]
    # Most widely available first - comparison is the point
    results.sort(key=lambda m: (-m.restaurant_count, -m.dish_count))
    return results[:limit]


def search_dishes(db: Session, q: str) -> list[schemas.DishOut]:
    pattern = f"%{q}%"
    dishes = (
        _product_query(db)
        .outerjoin(models.FoodType, models.Product.food_type_id == models.FoodType.id)
        .outerjoin(models.CanonicalDish, models.Product.canonical_dish_id == models.CanonicalDish.id)
        .filter(
            models.Product.is_active.is_(True),
            or_(
                models.Product.name.ilike(pattern),
                models.FoodType.name.ilike(pattern),
                models.CanonicalDish.name.ilike(pattern),
            ),
        )
        .all()
    )
    return _sort_by_rating(enrich_dishes(db, dishes))


def get_canonical_dish_comparison(db: Session, canonical_dish_id: int) -> schemas.DishCompareResult | None:
    """THE compare view: one canonical dish across every restaurant serving it."""
    canonical = (
        db.query(models.CanonicalDish)
        .options(joinedload(models.CanonicalDish.food_type))
        .filter(models.CanonicalDish.id == canonical_dish_id)
        .first()
    )
    if canonical is None:
        return None

    dishes = (
        _product_query(db)
        .filter(
            models.Product.canonical_dish_id == canonical_dish_id,
            models.Product.is_active.is_(True),
        )
        .all()
    )
    return schemas.DishCompareResult(
        canonical_dish=schemas.CanonicalDishOut(
            id=canonical.id,
            name=canonical.name,
            food_type=_food_type_out(canonical.food_type),
            aliases=canonical.aliases,
            image_url=canonical.image_url,
        ),
        dishes=_sort_by_rating(enrich_dishes(db, dishes)),
    )


def get_dishes_for_restaurant(db: Session, restaurant_id: int) -> list[schemas.DishOut]:
    dishes = (
        _product_query(db)
        .outerjoin(models.FoodCategory, models.Product.category_id == models.FoodCategory.id)
        .filter(
            models.Product.restaurant_id == restaurant_id,
            models.Product.is_active.is_(True),
        )
        .order_by(models.FoodCategory.name, models.Product.name)
        .all()
    )
    return enrich_dishes(db, dishes)


def get_dish(db: Session, dish_id: int) -> schemas.DishOut | None:
    dish = _product_query(db).filter(models.Product.id == dish_id).first()
    if dish is None:
        return None
    return enrich_dishes(db, [dish])[0]


def review_to_out(review: models.ProductReview) -> schemas.ReviewOut:
    """Serialize a product review with its product/restaurant/user context."""
    return schemas.ReviewOut(
        id=review.id,
        dish_id=review.product_id,
        restaurant_id=review.product.restaurant_id,
        dish_name=review.product.name,
        username=review.user.display_name,
        rating=review.rating,
        comment=review.body,
        is_verified=review.is_verified_order,
        created_at=review.created_at,
    )


def get_reviews_for_dish(db: Session, dish_id: int) -> list[schemas.ReviewOut]:
    reviews = (
        db.query(models.ProductReview)
        .options(joinedload(models.ProductReview.product), joinedload(models.ProductReview.user))
        .filter(models.ProductReview.product_id == dish_id)
        .order_by(models.ProductReview.created_at.desc())
        .all()
    )
    return [review_to_out(r) for r in reviews]


def get_reviews_for_restaurant(db: Session, restaurant_id: int) -> list[schemas.ReviewOut]:
    """A restaurant's dish reviews = product reviews across its products."""
    reviews = (
        db.query(models.ProductReview)
        .join(models.Product, models.ProductReview.product_id == models.Product.id)
        .options(joinedload(models.ProductReview.product), joinedload(models.ProductReview.user))
        .filter(models.Product.restaurant_id == restaurant_id)
        .order_by(models.ProductReview.created_at.desc())
        .all()
    )
    return [review_to_out(r) for r in reviews]
