from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

import models
import schemas


def _dish_query(db: Session):
    return db.query(models.Dish).options(
        joinedload(models.Dish.food_type),
        joinedload(models.Dish.restaurant),
        joinedload(models.Dish.cuisine_links).joinedload(models.DishCuisine.cuisine),
        joinedload(models.Dish.flavor_tag_links).joinedload(models.DishFlavorTag.flavor_tag),
    )


def _dish_review_stats(db: Session, dish_ids: list[int]) -> dict[int, tuple]:
    if not dish_ids:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Review.dish_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(models.Review.dish_id.in_(dish_ids))
        .group_by(models.Review.dish_id)
        .all()
    }


def enrich_dishes(db: Session, dishes: list[models.Dish]) -> list[schemas.DishOut]:
    if not dishes:
        return []

    review_stats = _dish_review_stats(db, [d.id for d in dishes])

    results = []
    for d in dishes:
        avg_raw, review_count = review_stats.get(d.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None

        results.append(
            schemas.DishOut(
                id=d.id,
                name=d.name,
                description=d.description,
                price_bdt=d.price_bdt,
                image_url=d.image_url,
                is_sold_out=d.is_sold_out,
                is_active=d.is_active,
                category_raw=d.category_raw,
                variations=d.variations,
                food_type=schemas.FoodTypeOut.model_validate(d.food_type) if d.food_type else None,
                canonical_dish_id=d.canonical_dish_id,
                cuisines=[schemas.CuisineOut.model_validate(link.cuisine) for link in d.cuisine_links],
                flavor_tags=[schemas.FlavorTagOut.model_validate(link.flavor_tag) for link in d.flavor_tag_links],
                restaurant=schemas.RestaurantSummaryOut.model_validate(d.restaurant),
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
            func.count(func.distinct(models.Dish.restaurant_id)),
            func.count(models.Dish.id),
            func.min(models.Dish.price_bdt),
            func.max(models.Dish.price_bdt),
        )
        .filter(models.Dish.canonical_dish_id == canonical.id, models.Dish.is_active.is_(True))
        .first()
    )
    restaurant_count, dish_count, min_price, max_price = row or (0, 0, None, None)

    avg_raw = (
        db.query(func.avg(models.Review.rating))
        .join(models.Dish, models.Review.dish_id == models.Dish.id)
        .filter(models.Dish.canonical_dish_id == canonical.id)
        .scalar()
    )

    return schemas.CanonicalDishMatch(
        id=canonical.id,
        name=canonical.name,
        food_type=schemas.FoodTypeOut.model_validate(canonical.food_type) if canonical.food_type else None,
        aliases=canonical.aliases,
        image_url=canonical.image_url,
        restaurant_count=restaurant_count or 0,
        dish_count=dish_count or 0,
        average_rating=round(float(avg_raw), 1) if avg_raw else None,
        min_price_bdt=min_price,
        max_price_bdt=max_price,
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
            # aliases is JSON; do the substring check in Python below for portability
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
        _dish_query(db)
        .outerjoin(models.FoodType, models.Dish.food_type_id == models.FoodType.id)
        .outerjoin(models.CanonicalDish, models.Dish.canonical_dish_id == models.CanonicalDish.id)
        .filter(
            models.Dish.is_active.is_(True),
            or_(
                models.Dish.name.ilike(pattern),
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
        _dish_query(db)
        .filter(
            models.Dish.canonical_dish_id == canonical_dish_id,
            models.Dish.is_active.is_(True),
        )
        .all()
    )
    return schemas.DishCompareResult(
        canonical_dish=schemas.CanonicalDishOut(
            id=canonical.id,
            name=canonical.name,
            food_type=schemas.FoodTypeOut.model_validate(canonical.food_type) if canonical.food_type else None,
            aliases=canonical.aliases,
            image_url=canonical.image_url,
        ),
        dishes=_sort_by_rating(enrich_dishes(db, dishes)),
    )


def get_dishes_for_restaurant(db: Session, restaurant_id: int) -> list[schemas.DishOut]:
    dishes = (
        _dish_query(db)
        .filter(
            models.Dish.restaurant_id == restaurant_id,
            models.Dish.is_active.is_(True),
        )
        .order_by(models.Dish.category_raw, models.Dish.name)
        .all()
    )
    return enrich_dishes(db, dishes)


def get_dish(db: Session, dish_id: int) -> schemas.DishOut | None:
    dish = _dish_query(db).filter(models.Dish.id == dish_id).first()
    if dish is None:
        return None
    return enrich_dishes(db, [dish])[0]


def review_to_out(review: models.Review) -> schemas.ReviewOut:
    """Serialize a Review with its dish/restaurant/user context loaded."""
    return schemas.ReviewOut(
        id=review.id,
        dish_id=review.dish_id,
        restaurant_id=review.dish.restaurant_id,
        dish_name=review.dish.name,
        username=review.user.username,
        rating=review.rating,
        comment=review.comment,
        is_verified=review.is_verified,
        created_at=review.created_at,
    )


def get_reviews_for_dish(db: Session, dish_id: int) -> list[schemas.ReviewOut]:
    reviews = (
        db.query(models.Review)
        .options(joinedload(models.Review.dish), joinedload(models.Review.user))
        .filter(models.Review.dish_id == dish_id)
        .order_by(models.Review.created_at.desc())
        .all()
    )
    return [review_to_out(r) for r in reviews]


def get_reviews_for_restaurant(db: Session, restaurant_id: int) -> list[schemas.ReviewOut]:
    """A restaurant's reviews = the reviews of its dishes (reviews are
    dish-anchored; the restaurant link flows through the dish)."""
    reviews = (
        db.query(models.Review)
        .join(models.Dish, models.Review.dish_id == models.Dish.id)
        .options(joinedload(models.Review.dish), joinedload(models.Review.user))
        .filter(models.Dish.restaurant_id == restaurant_id)
        .order_by(models.Review.created_at.desc())
        .all()
    )
    return [review_to_out(r) for r in reviews]
