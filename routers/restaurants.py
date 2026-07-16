"""Restaurant endpoints -- where 'restaurant' means BRAND.

Every restaurant is a brand: Bella Italia is a brand with 3 branches, a
standalone place is a brand of one. {restaurant_id} in these paths is the
chain_id. Branch-scoped admin operations live in routers/branches.py.

The brand-dish URL carries (food_type_id, slug) instead of a serial id on
purpose: a brand dish is a grouping, not a row, serial ids churn on every
pipeline reload, and without food_type_id a brand's "Chicken" curry would
collide with its "Chicken" pizza at the same URL.
"""

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from auth import get_current_user
from brand_browse import build_brand_list, matching_chain_ids
from brand_dishes import build_brand_dishes, dish_slug
from database import get_db
from dish_detail import _product_query, _product_review_stats
import models
from restaurant_reviews import (
    brand_display_rating,
    get_reviews_for_brand,
    restaurant_review_to_out,
)
import schemas

router = APIRouter(prefix="/restaurants", tags=["Restaurants"])


# ---------------------------------------------------------------------------
# Brand list (browse)
# ---------------------------------------------------------------------------

@router.get("/", response_model=list[schemas.BrandListOut])
def list_restaurants(db: Session = Depends(get_db)):
    """All brands. Bella Italia appears once with branch_count=3."""
    chain_ids = [row[0] for row in matching_chain_ids(db, None).all()]
    return build_brand_list(db, chain_ids)


@router.get("/catalogue", response_model=schemas.RestaurantCatalogueResult)
def get_restaurant_catalogue(
    q: str | None = Query(None, description="Filter by brand, branch, area, or address"),
    offset: int = Query(0, ge=0),
    limit: int = Query(24, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Browse brands with optional text filter and pagination."""
    rows = matching_chain_ids(db, q).all()
    total = len(rows)
    page_ids = [row[0] for row in rows[offset:offset + limit]]
    return schemas.RestaurantCatalogueResult(
        restaurants=build_brand_list(db, page_ids),
        total=total,
        offset=offset,
        limit=limit,
    )


# ---------------------------------------------------------------------------
# Brand page + menu + dish detail
# ---------------------------------------------------------------------------

def _get_chain(db: Session, chain_id: int) -> models.RestaurantChain:
    chain = db.query(models.RestaurantChain).filter(models.RestaurantChain.id == chain_id).first()
    if not chain:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return chain


def _branches(db: Session, chain_id: int) -> list[models.Restaurant]:
    return (
        db.query(models.Restaurant)
        .filter(models.Restaurant.chain_id == chain_id, models.Restaurant.is_active.is_(True))
        .order_by(models.Restaurant.name)
        .all()
    )


@router.get("/{restaurant_id}", response_model=schemas.BrandDetailOut)
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    """The brand page: locations as tags, pooled display rating."""
    chain = _get_chain(db, restaurant_id)
    branches = _branches(db, restaurant_id)
    rating, _count, source = brand_display_rating(db, branches)
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


@router.get("/{restaurant_id}/menu", response_model=list[schemas.BrandDishOut])
def get_restaurant_menu(restaurant_id: int, db: Session = Depends(get_db)):
    """The brand's merged menu: union of every branch's dishes, deduped into
    brand cards ('at 2 of 3 branches' when availability differs). One menu for
    the whole chain -- a standalone restaurant's menu is unchanged in shape."""
    _get_chain(db, restaurant_id)
    products = (
        _product_query(db)
        .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
        .filter(
            models.Restaurant.chain_id == restaurant_id,
            models.Product.is_active.is_(True),
        )
        .all()
    )
    cards = build_brand_dishes(db, products)
    cards.sort(key=lambda c: ((c.category_raw or ""), c.name.lower()))
    return cards


@router.get("/{restaurant_id}/dishes/{food_type_id}/{slug}", response_model=schemas.BrandDishDetailOut)
def get_restaurant_dish(restaurant_id: int, food_type_id: int, slug: str, db: Session = Depends(get_db)):
    """One brand dish with the per-branch breakdown (each branch's price and
    its own product_id -- which is what POST /reviews takes for dish reviews)."""
    products = [
        p for p in (
            _product_query(db)
            .join(models.Restaurant, models.Restaurant.id == models.Product.restaurant_id)
            .filter(
                models.Restaurant.chain_id == restaurant_id,
                models.Product.food_type_id == food_type_id,
                models.Product.is_active.is_(True),
            )
            .all()
        )
        if dish_slug(p.normalized_name) == slug
    ]
    if not products:
        raise HTTPException(status_code=404, detail="Dish not found")

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


# ---------------------------------------------------------------------------
# Location reviews (attach to a branch, pool per brand)
# ---------------------------------------------------------------------------

@router.get("/{restaurant_id}/reviews", response_model=schemas.RestaurantReviewListResult)
def get_restaurant_reviews(
    restaurant_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """Location reviews pooled across the brand's branches, newest first. Each
    review is tagged with its branch name/area. Dish reviews are separate,
    served per-dish from /dishes/{id}/reviews."""
    _get_chain(db, restaurant_id)
    reviews, total = get_reviews_for_brand(db, restaurant_id, offset=offset, limit=limit)
    return schemas.RestaurantReviewListResult(reviews=reviews, total=total, offset=offset, limit=limit)


@router.post("/{restaurant_id}/reviews", response_model=schemas.RestaurantReviewOut, status_code=201)
def submit_restaurant_review(
    restaurant_id: int,
    data: schemas.RestaurantReviewCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Review a LOCATION of this brand (requires sign-in). branch_id picks
    which location the reviewer visited; one review per user per location,
    resubmitting updates it. The brand page shows the pooled rating."""
    _get_chain(db, restaurant_id)
    branch = db.query(models.Restaurant).filter(models.Restaurant.id == data.branch_id).first()
    if not branch or branch.chain_id != restaurant_id:
        raise HTTPException(status_code=400, detail="branch_id is not a location of this restaurant")

    review = (
        db.query(models.RestaurantReview)
        .filter(
            models.RestaurantReview.user_id == current_user.id,
            models.RestaurantReview.restaurant_id == data.branch_id,
        )
        .first()
    )
    if review is None:
        review = models.RestaurantReview(restaurant_id=data.branch_id, user_id=current_user.id)
        db.add(review)
    review.rating = data.rating
    review.body = data.comment
    # Post-moderation: visible immediately, moderated reactively (see reviews.py).
    review.status = "approved"

    db.commit()
    db.refresh(review)
    return restaurant_review_to_out(review)


@router.delete("/{restaurant_id}/reviews/{review_id}", status_code=204)
def delete_restaurant_review(
    restaurant_id: int,
    review_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    review = (
        db.query(models.RestaurantReview)
        .join(models.Restaurant, models.Restaurant.id == models.RestaurantReview.restaurant_id)
        .filter(
            models.RestaurantReview.id == review_id,
            models.Restaurant.chain_id == restaurant_id,
        )
        .first()
    )
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own reviews")
    db.delete(review)
    db.commit()
