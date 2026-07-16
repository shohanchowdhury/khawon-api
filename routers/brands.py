from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from brand_dishes import build_brand_dishes, dish_slug
from database import get_db
from dish_detail import _product_query, _product_review_stats
import models
from restaurant_reviews import resolve_display_rating, restaurant_review_stats
import schemas

router = APIRouter(prefix="/brands", tags=["Brands"])


@router.get("/{chain_id}", response_model=schemas.BrandDetailOut)
def get_brand(chain_id: int, db: Session = Depends(get_db)):
    """A brand and its branches. The branch list feeds the map/directions view."""
    chain = db.query(models.RestaurantChain).filter(models.RestaurantChain.id == chain_id).first()
    if not chain:
        raise HTTPException(status_code=404, detail="Brand not found")

    branches = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.chain_id == chain_id, models.Restaurant.is_active.is_(True))
        .order_by(models.Restaurant.name)
        .all()
    )
    stats = restaurant_review_stats(db, [b.id for b in branches])
    # Brand rating = review-count-weighted average across branches, falling
    # back to the (weighted) foodpanda rating while khawon reviews are thin.
    # Swaps to nearest-branch when geo lands.
    total_n = sum(stats.get(b.id, (None, 0))[1] or 0 for b in branches)
    khawon_avg = (
        round(sum(float(stats[b.id][0]) * stats[b.id][1] for b in branches if b.id in stats) / total_n, 1)
        if total_n else None
    )
    fp = [(float(b.old_rating), b.old_review_count or 0) for b in branches if b.old_rating is not None]
    fp_n = sum(n for _, n in fp)
    fp_avg = round(sum(r * n for r, n in fp) / fp_n, 1) if fp_n else (fp[0][0] if fp else None)
    rating, _count, source = resolve_display_rating(khawon_avg, total_n, fp_avg, fp_n)

    return schemas.BrandDetailOut(
        id=chain.id,
        name=chain.name,
        branch_count=len(branches),
        branches=[
            schemas.RestaurantSummaryOut(
                id=b.id, name=b.name, area=b.area, address=b.address,
                image_url=b.hero_image_url, google_place_id=b.google_place_id,
            )
            for b in branches
        ],
        display_rating=rating,
        display_rating_source=source,
    )


@router.get("/{chain_id}/dishes/{food_type_id}/{slug}", response_model=schemas.BrandDishDetailOut)
def get_brand_dish(chain_id: int, food_type_id: int, slug: str, db: Session = Depends(get_db)):
    """One brand's dish with the per-branch breakdown (each branch's price and
    its own product_id -- which is what POST /reviews takes).

    food_type_id is in the path because the brand-dish key includes it: a
    {chain_id, slug}-only URL would collide a brand's "Chicken" curry with its
    "Chicken" pizza. The natural key is used instead of a serial id on purpose
    -- ids churn on reload, the key does not, so deep links survive.
    """
    products = [
        p for p in (
            _product_query(db)
            .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
            .filter(
                models.Restaurant.chain_id == chain_id,
                models.Product.food_type_id == food_type_id,
                models.Product.is_active.is_(True),
            )
            .all()
        )
        if dish_slug(p.normalized_name) == slug
    ]
    if not products:
        raise HTTPException(status_code=404, detail="Brand dish not found")

    card = build_brand_dishes(db, products)[0]
    stats = _product_review_stats(db, [p.id for p in products])
    branches = []
    for p in sorted(products, key=lambda x: x.restaurant.name):
        avg_raw, n = stats.get(p.id, (None, 0))
        branches.append(schemas.BrandBranchOut(
            restaurant_id=p.restaurant.id,
            restaurant_name=p.restaurant.name,
            area=p.restaurant.area,
            product_id=p.id,
            price_bdt=float(p.base_price_bdt),
            is_sold_out=p.is_sold_out,
            average_rating=round(float(avg_raw), 1) if avg_raw else None,
            review_count=n or 0,
        ))
    return schemas.BrandDishDetailOut(**card.model_dump(), branches=branches)
