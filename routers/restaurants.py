import re
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from auth import get_current_user
from database import get_db
import models
import schemas
from places import fetch_place_photo_bytes
from dish_detail import get_dishes_for_restaurant, get_reviews_for_restaurant
from storage import upload_image_bytes

router = APIRouter(prefix="/restaurants", tags=["Restaurants"])


def _num(v):
    return float(v) if v is not None else None


def _food_types_for_restaurants(db: Session, ids: list[int]) -> dict[int, list[models.FoodType]]:
    """A restaurant's food types are derived from its active products."""
    out: dict[int, list[models.FoodType]] = {}
    if not ids:
        return out
    for restaurant_id, food_type in (
        db.query(models.Product.restaurant_id, models.FoodType)
        .join(models.FoodType, models.Product.food_type_id == models.FoodType.id)
        .filter(models.Product.restaurant_id.in_(ids), models.Product.is_active.is_(True))
        .distinct()
        .all()
    ):
        out.setdefault(restaurant_id, []).append(food_type)
    return out


def _restaurant_review_stats(db: Session, ids: list[int]) -> dict[int, tuple]:
    """A restaurant's rating = the ratings of its products' reviews (reviews are
    dish-anchored; the restaurant link flows through the product)."""
    if not ids:
        return {}
    return {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Product.restaurant_id,
            func.avg(models.ProductReview.rating),
            func.count(models.ProductReview.id),
        )
        .join(models.ProductReview, models.ProductReview.product_id == models.Product.id)
        .filter(models.Product.restaurant_id.in_(ids))
        .group_by(models.Product.restaurant_id)
        .all()
    }


def _restaurant_out(
    r: models.Restaurant,
    food_types: list[models.FoodType],
    average_rating: float | None,
    review_count: int,
) -> schemas.RestaurantOut:
    return schemas.RestaurantOut(
        id=r.id,
        name=r.name,
        area=r.area,
        address=r.address,
        phone=r.phone,
        # v2 schema has no google_maps_url / website_url columns -> None.
        google_maps_url=None,
        website_url=None,
        google_place_id=r.google_place_id,
        image_url=r.hero_image_url,
        food_types=[schemas.FoodTypeOut(id=ft.id, name=ft.name) for ft in food_types],
        average_rating=average_rating,
        review_count=review_count,
        match_status=r.match_status,
        source_restaurant_code=r.source_restaurant_code,
        chain_name=r.chain.name if r.chain else None,
        chain_code=r.chain.chain_code if r.chain else None,
        budget=r.budget_tier,
        foodpanda_rating=_num(r.old_rating),
        foodpanda_review_number=r.old_review_count,
        raw_cuisines=[link.cuisine.name for link in r.cuisine_links if link.cuisine],
        logo_url=r.logo_image_url,
        latitude=_num(r.latitude),
        longitude=_num(r.longitude),
    )


def build_restaurant_out(
    r: models.Restaurant,
    db: Session,
    average_rating: float | None = None,
    review_count: int | None = None,
) -> schemas.RestaurantOut:
    """Single restaurant -> RestaurantOut. Rating stats are derived from its
    products' reviews unless caller passes a scoped override."""
    if average_rating is None and review_count is None:
        avg_raw, review_count = _restaurant_review_stats(db, [r.id]).get(r.id, (None, 0))
        average_rating = round(float(avg_raw), 1) if avg_raw else None
    food_types = _food_types_for_restaurants(db, [r.id]).get(r.id, [])
    return _restaurant_out(r, food_types, average_rating, review_count or 0)


def _enrich_restaurants(
    restaurants: list[models.Restaurant], db: Session
) -> list[schemas.RestaurantOut]:
    if not restaurants:
        return []
    ids = [r.id for r in restaurants]
    review_stats = _restaurant_review_stats(db, ids)
    food_types_by_restaurant = _food_types_for_restaurants(db, ids)

    results = []
    for r in restaurants:
        avg_raw, review_count = review_stats.get(r.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None
        results.append(
            _restaurant_out(r, food_types_by_restaurant.get(r.id, []), avg_rating, review_count or 0)
        )
    return results


def _enrich(restaurant: models.Restaurant, db: Session) -> schemas.RestaurantOut:
    return _enrich_restaurants([restaurant], db)[0]


async def _resolve_restaurant_image(
    google_photo_name: str | None,
    image: UploadFile | None,
) -> str | None:
    if image and image.filename:
        data = await image.read()
        return upload_image_bytes(data, folder="khawon/restaurants")
    if google_photo_name and google_photo_name.strip():
        photo_bytes, _ = fetch_place_photo_bytes(google_photo_name.strip(), max_width=1600)
        return upload_image_bytes(photo_bytes, folder="khawon/restaurants")
    return None


@router.get("/", response_model=list[schemas.RestaurantOut])
def list_restaurants(db: Session = Depends(get_db)):
    """List all restaurants"""
    restaurants = db.query(models.Restaurant).order_by(models.Restaurant.name).all()
    return _enrich_restaurants(restaurants, db)


@router.get("/catalogue", response_model=list[schemas.RestaurantOut])
def get_restaurant_catalogue(
    q: str | None = Query(None, description="Filter by name, area, or address"),
    db: Session = Depends(get_db),
):
    """Browse all restaurants with optional text filter."""
    query = db.query(models.Restaurant)
    if q:
        pattern = f"%{q}%"
        query = query.filter(
            or_(
                models.Restaurant.name.ilike(pattern),
                models.Restaurant.area.ilike(pattern),
                models.Restaurant.address.ilike(pattern),
            )
        )
    restaurants = query.order_by(models.Restaurant.name).all()
    return _enrich_restaurants(restaurants, db)


@router.get("/{restaurant_id}", response_model=schemas.RestaurantOut)
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return _enrich(r, db)


@router.post("/", response_model=schemas.RestaurantOut, status_code=201)
async def create_restaurant(
    name: str = Form(...),
    area: str | None = Form(None),
    address: str | None = Form(None),
    phone: str | None = Form(None),
    google_maps_url: str | None = Form(None),
    website_url: str | None = Form(None),
    google_place_id: str | None = Form(None),
    google_photo_name: str | None = Form(None),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Add a new restaurant (requires sign-in). Food types are derived from
    the restaurant's dishes, not set directly."""
    image_url = await _resolve_restaurant_image(google_photo_name, image)

    # A user-created restaurant still needs a stable natural key (schema requires
    # source_restaurant_code NOT NULL UNIQUE); derive a slug + short unique suffix.
    slug = re.sub(r"[^a-z0-9]+", "-", name.lower()).strip("-")[:40] or "restaurant"
    code = f"user-{slug}-{uuid.uuid4().hex[:8]}"

    restaurant = models.Restaurant(
        source_restaurant_code=code,
        name=name,
        area=area or None,
        address=address or None,
        phone=phone or None,
        # v2 schema has no google_maps_url / website_url columns; those form
        # fields are accepted for backward compat but not persisted.
        google_place_id=google_place_id or None,
        hero_image_url=image_url,
    )
    db.add(restaurant)
    db.commit()
    db.refresh(restaurant)
    return _enrich(restaurant, db)


@router.put("/{restaurant_id}", response_model=schemas.RestaurantOut)
async def update_restaurant(
    restaurant_id: int,
    name: str = Form(...),
    area: str | None = Form(None),
    address: str | None = Form(None),
    phone: str | None = Form(None),
    google_maps_url: str | None = Form(None),
    website_url: str | None = Form(None),
    google_place_id: str | None = Form(None),
    google_photo_name: str | None = Form(None),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    r.name = name
    r.area = area or None
    r.address = address or None
    r.phone = phone or None
    # google_maps_url / website_url have no column in the v2 schema (accepted
    # for backward compat but not persisted).
    r.google_place_id = google_place_id or None

    new_image_url = await _resolve_restaurant_image(google_photo_name, image)
    if new_image_url:
        r.hero_image_url = new_image_url

    db.commit()
    db.refresh(r)
    return _enrich(r, db)


@router.put("/{restaurant_id}/photo", response_model=schemas.RestaurantOut)
async def update_restaurant_photo(
    restaurant_id: int,
    google_photo_name: Annotated[str | None, Form()] = None,
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Update only the restaurant photo from Google or a file upload."""
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    new_image_url = await _resolve_restaurant_image(google_photo_name, image)
    if not new_image_url:
        raise HTTPException(
            status_code=400,
            detail="Provide a Google photo selection or upload an image file.",
        )

    r.hero_image_url = new_image_url
    db.commit()
    db.refresh(r)
    return _enrich(r, db)


@router.delete("/{restaurant_id}", status_code=204)
def delete_restaurant(
    restaurant_id: int,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    db.delete(r)
    db.commit()


@router.get("/{restaurant_id}/dishes", response_model=list[schemas.DishOut])
def get_restaurant_dishes(restaurant_id: int, db: Session = Depends(get_db)):
    """A restaurant's menu (dishes)."""
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return get_dishes_for_restaurant(db, restaurant_id)


@router.get("/{restaurant_id}/reviews", response_model=list[schemas.ReviewOut])
def get_restaurant_reviews(restaurant_id: int, db: Session = Depends(get_db)):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return get_reviews_for_restaurant(db, restaurant_id)
