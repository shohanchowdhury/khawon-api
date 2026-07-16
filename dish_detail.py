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
from brand_dishes import build_brand_dishes, dish_slug
from product_image_pools import pick_random_url, product_image_pools
from restaurant_reviews import resolve_display_rating, restaurant_review_stats


def _product_query(db: Session):
    return db.query(models.Product).options(
        joinedload(models.Product.food_type),
        joinedload(models.Product.category),
        joinedload(models.Product.cuisine),
        # brand card needs the chain; joined here so grouping does not N+1
        joinedload(models.Product.restaurant).joinedload(models.Restaurant.chain),
        joinedload(models.Product.variations),
        joinedload(models.Product.flavor_tag_links).joinedload(models.ProductFlavorTag.flavor_tag),
    )


def _product_review_stats(db: Session, product_ids: list[int]) -> dict[int, tuple]:
    """(avg_rating, review_count) per product, from APPROVED product_reviews.

    Ratings are computed live from approved reviews (the products.rating /
    review_count columns are reserved for future denormalization and are not
    maintained yet - see the note in schema.sql)."""
    if not product_ids:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.ProductReview.product_id,
            func.avg(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .filter(
            models.ProductReview.product_id.in_(product_ids),
            models.ProductReview.status == "approved",
        )
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


def normalize_product_image_url(url: str | None) -> str | None:
    """Fix Foodpanda URLs stored with an unresolved width template."""
    if not url:
        return None
    if "?width=%s" in url:
        return url.replace("?width=%s", "?width=400")
    return url


def enrich_dishes(db: Session, dishes: list[models.Product]) -> list[schemas.DishOut]:
    if not dishes:
        return []

    review_stats = _product_review_stats(db, [d.id for d in dishes])
    # Restaurant-level rating (khawon-else-foodpanda) for each dish's inline
    # restaurant card, one grouped query for the whole batch.
    rest_stats = restaurant_review_stats(db, list({d.restaurant_id for d in dishes}))

    results = []
    for d in dishes:
        avg_raw, review_count = review_stats.get(d.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None

        r_avg_raw, r_count = rest_stats.get(d.restaurant_id, (None, 0))
        r_avg = round(float(r_avg_raw), 1) if r_avg_raw else None
        r_disp, _, r_src = resolve_display_rating(
            r_avg, r_count, d.restaurant.old_rating, d.restaurant.old_review_count
        )

        results.append(
            schemas.DishOut(
                id=d.id,
                name=d.name,
                description=d.description,
                price_bdt=float(d.base_price_bdt) if d.base_price_bdt is not None else None,
                image_url=normalize_product_image_url(d.image_url),
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
                    display_rating=r_disp,
                    display_rating_source=r_src,
                ),
                average_rating=avg_rating,
                review_count=review_count or 0,
            )
        )
    return results


def _sort_by_rating(dishes: list[schemas.DishOut]) -> list[schemas.DishOut]:
    dishes.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))
    return dishes


def _canonical_match_stats_batch(
    db: Session,
    canonicals: list[models.CanonicalDish],
) -> list[schemas.CanonicalDishMatch]:
    if not canonicals:
        return []

    ids = [canonical.id for canonical in canonicals]

    product_stats = {
        row[0]: (row[1], row[2], row[3], row[4])
        for row in db.query(
            models.Product.canonical_dish_id,
            # Brands, not branches: three Domino's branches are one restaurant
            # to a diner comparing prices. Branch dedupe is the chain layer's
            # job; this layer compares ACROSS brands.
            func.count(func.distinct(models.Restaurant.chain_id)),
            func.count(models.Product.id),
            func.min(models.Product.base_price_bdt),
            func.max(models.Product.base_price_bdt),
        )
        .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
        .filter(
            models.Product.canonical_dish_id.in_(ids),
            models.Product.is_active.is_(True),
        )
        .group_by(models.Product.canonical_dish_id)
        .all()
    }

    rating_stats = {
        row[0]: row[1]
        for row in db.query(
            models.Product.canonical_dish_id,
            func.avg(models.ProductReview.rating),
        )
        .join(models.Product, models.ProductReview.product_id == models.Product.id)
        .filter(
            models.Product.canonical_dish_id.in_(ids),
            models.ProductReview.status == "approved",
        )
        .group_by(models.Product.canonical_dish_id)
        .all()
    }

    image_pools = product_image_pools(
        db,
        group_column=models.Product.canonical_dish_id,
        entity_ids=ids,
    )

    results = []
    for canonical in canonicals:
        restaurant_count, dish_count, min_price, max_price = product_stats.get(
            canonical.id,
            (0, 0, None, None),
        )
        avg_raw = rating_stats.get(canonical.id)
        pool = image_pools.get(canonical.id, [])
        image_url = pick_random_url(pool) or canonical.image_url
        results.append(
            schemas.CanonicalDishMatch(
                id=canonical.id,
                name=canonical.name,
                food_type=_food_type_out(canonical.food_type),
                aliases=canonical.aliases,
                image_url=image_url,
                restaurant_count=restaurant_count or 0,
                dish_count=dish_count or 0,
                average_rating=round(float(avg_raw), 1) if avg_raw else None,
                min_price_bdt=float(min_price) if min_price is not None else None,
                max_price_bdt=float(max_price) if max_price is not None else None,
            )
        )
    return results


def search_canonical_dishes(
    db: Session,
    q: str,
    *,
    limit: int = 10,
) -> list[schemas.CanonicalDishMatch]:
    """Canonical dishes whose name, aliases, or food type match the query.

    This is the "compare these across restaurants" highlight strip - a small
    capped list, not the full paginated result (that's the flat dish list from
    search_dishes, which also surfaces single-restaurant/non-canonical dishes).
    """
    pattern = f"%{q}%"
    q_lower = q.lower().strip()

    candidates = (
        db.query(models.CanonicalDish)
        .options(joinedload(models.CanonicalDish.food_type))
        .outerjoin(models.FoodType, models.CanonicalDish.food_type_id == models.FoodType.id)
        .filter(or_(
            models.CanonicalDish.name.ilike(pattern),
            models.FoodType.name.ilike(pattern),
            func.array_to_string(models.CanonicalDish.aliases, " ").ilike(pattern),
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
    results = _canonical_match_stats_batch(db, matched)
    # Most widely available first - comparison is the point
    results.sort(key=lambda m: (-m.restaurant_count, -m.dish_count))
    return results[:limit]


def _search_rank(name: str, q_lower: str) -> int:
    """Relevance bucket: exact < prefix < substring < matched-via-type/canonical."""
    n = (name or "").lower()
    if n == q_lower:
        return 0
    if n.startswith(q_lower):
        return 1
    if q_lower in n:
        return 2
    return 3  # matched only through food_type / canonical name, not its own name


def search_dishes(
    db: Session,
    q: str,
    *,
    offset: int = 0,
    limit: int = 20,
) -> tuple[list[schemas.BrandDishOut], int]:
    """Brand dish cards matching the query, paginated, most-relevant first.

    A chain appears ONCE: its branches collapse into one card (Domino's
    Margherita x3 branches -> 1 card with branch_count=3). Non-canonical
    dishes are included (a dish at one restaurant has canonical_dish_id NULL
    but must still be findable).

    Ranks on a lightweight (id, name, key) fetch and only hydrates the page's
    groups, so a broad query ("chicken") never joins thousands of rows.
    """
    pattern = f"%{q}%"
    q_lower = q.lower().strip()

    rows = (
        db.query(
            models.Product.id,
            models.Product.name,
            models.Restaurant.chain_id,
            models.Product.food_type_id,
            models.Product.normalized_name,
        )
        .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
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

    # group candidate rows into brand cards BEFORE paginating, so the page
    # size counts cards, not branch rows
    groups: dict[tuple, list] = {}
    for r in rows:
        groups.setdefault((r.chain_id, r.food_type_id, r.normalized_name), []).append(r)

    def rank(key):
        best = min(_search_rank(r.name, q_lower) for r in groups[key])
        label = min((r.name or "").lower() for r in groups[key])
        return (best, label)

    ordered_keys = sorted(groups, key=rank)
    total = len(ordered_keys)
    page_keys = ordered_keys[offset : offset + limit]
    if not page_keys:
        return [], total

    page_ids = [r.id for k in page_keys for r in groups[k]]
    prods = _product_query(db).filter(models.Product.id.in_(page_ids)).all()
    cards = build_brand_dishes(db, prods)

    # Restore relevance order. The card exposes `slug`, not normalized_name,
    # so key the lookup on the slug the card will actually carry.
    order = {(k[0], k[1], dish_slug(k[2])): i for i, k in enumerate(page_keys)}
    cards.sort(key=lambda c: order.get((c.brand.id, c.food_type_id, c.slug), len(order)))
    return cards, total


def get_canonical_dish_comparison(
    db: Session,
    canonical_dish_id: int,
    *,
    offset: int = 0,
    limit: int = 20,
) -> schemas.DishCompareResult | None:
    """THE compare view: one canonical dish across every BRAND serving it.
    A chain is one row with branch_count > 1, not one row per branch --
    comparing a dish to itself across branches is not a comparison, and the
    row count now agrees with restaurant_count (which counts brands)."""
    canonical = (
        db.query(models.CanonicalDish)
        .options(joinedload(models.CanonicalDish.food_type))
        .filter(models.CanonicalDish.id == canonical_dish_id)
        .first()
    )
    if canonical is None:
        return None

    products = (
        _product_query(db)
        .filter(
            models.Product.canonical_dish_id == canonical_dish_id,
            models.Product.is_active.is_(True),
        )
        .all()
    )
    cards = build_brand_dishes(db, products)
    cards.sort(key=lambda c: (c.average_rating is None, -(c.average_rating or 0)))
    total = len(cards)
    page = cards[offset : offset + limit]

    rated = [c for c in cards if c.average_rating is not None]
    avg_rating = (
        round(sum(c.average_rating for c in rated) / len(rated), 1) if rated else None
    )
    min_price = min((c.price_min_bdt for c in cards), default=None)
    max_price = max((c.price_max_bdt for c in cards), default=None)

    return schemas.DishCompareResult(
        canonical_dish=schemas.CanonicalDishOut(
            id=canonical.id,
            name=canonical.name,
            food_type=_food_type_out(canonical.food_type),
            aliases=canonical.aliases,
            image_url=canonical.image_url,
        ),
        dishes=page,
        total=total,
        offset=offset,
        limit=limit,
        average_rating=avg_rating,
        min_price_bdt=min_price,
        max_price_bdt=max_price,
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


def get_reviews_for_dish(
    db: Session, dish_id: int, *, offset: int = 0, limit: int = 20
) -> tuple[list[schemas.ReviewOut], int]:
    base = (
        db.query(models.ProductReview)
        .filter(
            models.ProductReview.product_id == dish_id,
            models.ProductReview.status == "approved",
        )
    )
    total = base.order_by(None).count()
    reviews = (
        base.options(joinedload(models.ProductReview.product), joinedload(models.ProductReview.user))
        .order_by(models.ProductReview.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    return [review_to_out(r) for r in reviews], total
