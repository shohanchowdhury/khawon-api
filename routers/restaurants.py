import json
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_
from auth import get_current_user
from database import get_db
import models
import schemas
from places import fetch_place_photo_bytes
from product_detail import get_products_for_restaurant
from storage import upload_image_bytes

router = APIRouter(prefix="/restaurants", tags=["Restaurants"])


def _enrich_restaurants(
    restaurants: list[models.Restaurant], db: Session
) -> list[schemas.RestaurantOut]:
    if not restaurants:
        return []

    ids = [r.id for r in restaurants]

    review_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Review.restaurant_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(models.Review.restaurant_id.in_(ids))
        .group_by(models.Review.restaurant_id)
        .all()
    }

    links = (
        db.query(models.RestaurantFoodType)
        .filter(models.RestaurantFoodType.restaurant_id.in_(ids))
        .options(joinedload(models.RestaurantFoodType.food_type))
        .all()
    )
    food_types_by_restaurant: dict[int, list[models.FoodType]] = {}
    for link in links:
        food_types_by_restaurant.setdefault(link.restaurant_id, []).append(
            link.food_type
        )

    results = []
    for r in restaurants:
        avg_raw, review_count = review_stats.get(r.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None
        food_types = food_types_by_restaurant.get(r.id, [])

        results.append(
            schemas.RestaurantOut(
                id=r.id,
                name=r.name,
                area=r.area,
                address=r.address,
                phone=r.phone,
                google_maps_url=r.google_maps_url,
                website_url=r.website_url,
                google_place_id=r.google_place_id,
                image_url=r.image_url,
                food_types=[
                    schemas.FoodTypeOut.model_validate(ft) for ft in food_types
                ],
                average_rating=avg_rating,
                review_count=review_count or 0,
            )
        )
    return results


def _enrich(restaurant: models.Restaurant, db: Session) -> schemas.RestaurantOut:
    return _enrich_restaurants([restaurant], db)[0]


def _parse_food_type_ids(raw: str) -> list[int]:
    try:
        ids = json.loads(raw or "[]")
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="Invalid food_type_ids JSON.") from exc
    if not isinstance(ids, list):
        raise HTTPException(status_code=400, detail="food_type_ids must be a JSON array.")
    return [int(value) for value in ids]


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


def _set_food_type_links(
    restaurant_id: int, food_type_ids: list[int], db: Session
) -> None:
    db.query(models.RestaurantFoodType).filter(
        models.RestaurantFoodType.restaurant_id == restaurant_id
    ).delete()

    for ft_id in food_type_ids:
        ft = db.query(models.FoodType).filter(models.FoodType.id == ft_id).first()
        if not ft:
            raise HTTPException(status_code=400, detail=f"Food type ID {ft_id} not found")
        db.add(
            models.RestaurantFoodType(
                restaurant_id=restaurant_id, food_type_id=ft_id
            )
        )


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
    food_type_ids: str = Form("[]"),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Add a new restaurant with its food types (requires sign-in)."""
    image_url = await _resolve_restaurant_image(google_photo_name, image)

    restaurant = models.Restaurant(
        name=name,
        area=area or None,
        address=address or None,
        phone=phone or None,
        google_maps_url=google_maps_url or None,
        website_url=website_url or None,
        google_place_id=google_place_id or None,
        image_url=image_url,
    )
    db.add(restaurant)
    db.flush()

    _set_food_type_links(restaurant.id, _parse_food_type_ids(food_type_ids), db)

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
    food_type_ids: str = Form("[]"),
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
    r.google_maps_url = google_maps_url or None
    r.website_url = website_url or None
    r.google_place_id = google_place_id or None

    new_image_url = await _resolve_restaurant_image(google_photo_name, image)
    if new_image_url:
        r.image_url = new_image_url

    _set_food_type_links(restaurant_id, _parse_food_type_ids(food_type_ids), db)

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

    r.image_url = new_image_url
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


@router.get("/{restaurant_id}/products", response_model=list[schemas.ProductOut])
def get_restaurant_products(restaurant_id: int, db: Session = Depends(get_db)):
    """A restaurant's menu (dishes)."""
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return get_products_for_restaurant(db, restaurant_id)


@router.get("/{restaurant_id}/reviews", response_model=list[schemas.ReviewOut])
def get_restaurant_reviews(restaurant_id: int, db: Session = Depends(get_db)):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return db.query(models.Review).filter(
        models.Review.restaurant_id == restaurant_id
    ).order_by(models.Review.created_at.desc()).all()
