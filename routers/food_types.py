from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from typing import Annotated
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import get_current_user
from database import get_db
import models
import schemas
from food_images import pop_cached_image
from storage import upload_food_image, upload_image_bytes

router = APIRouter(prefix="/food-types", tags=["Food Types"])


async def _resolve_food_image(
    ai_image_id: str | None,
    image: UploadFile | None,
) -> str | None:
    if image and image.filename:
        return await upload_food_image(image)
    if ai_image_id and ai_image_id.strip():
        photo_bytes, _ = pop_cached_image(ai_image_id.strip())
        return upload_image_bytes(photo_bytes, folder="khawon/food-types")
    return None


def _enrich_food_types(
    food_types: list[models.FoodType], db: Session
) -> list[schemas.FoodTypePopularOut]:
    if not food_types:
        return []

    ids = [ft.id for ft in food_types]

    restaurant_counts = dict(
        db.query(
            models.RestaurantFoodType.food_type_id,
            func.count(models.RestaurantFoodType.restaurant_id),
        )
        .filter(models.RestaurantFoodType.food_type_id.in_(ids))
        .group_by(models.RestaurantFoodType.food_type_id)
        .all()
    )

    review_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Review.food_type_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(models.Review.food_type_id.in_(ids))
        .group_by(models.Review.food_type_id)
        .all()
    }

    results = []
    for ft in food_types:
        avg_raw, review_count = review_stats.get(ft.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None
        results.append(
            schemas.FoodTypePopularOut(
                id=ft.id,
                name=ft.name,
                description=ft.description,
                image_url=ft.image_url,
                restaurant_count=restaurant_counts.get(ft.id, 0),
                review_count=review_count or 0,
                average_rating=avg_rating,
            )
        )
    return results


@router.get("/", response_model=list[schemas.FoodTypeOut])
def list_food_types(db: Session = Depends(get_db)):
    """Get all food types (e.g. Ramen, Biriyani, Burger)"""
    return db.query(models.FoodType).order_by(models.FoodType.name).all()


@router.get("/top", response_model=list[schemas.FoodTypePopularOut])
def get_top_food_types(
    limit: int = Query(8, ge=1, le=50),
    db: Session = Depends(get_db),
):
    """Top food types ranked by review count, then average rating."""
    food_types = db.query(models.FoodType).all()
    results = _enrich_food_types(food_types, db)
    results.sort(
        key=lambda x: (-x.review_count, -(x.average_rating or 0), x.name.lower())
    )
    return results[:limit]


@router.get("/catalogue", response_model=list[schemas.FoodTypePopularOut])
def get_food_catalogue(
    q: str | None = Query(None, description="Filter by food name"),
    db: Session = Depends(get_db),
):
    """Full food catalogue with stats, sorted alphabetically."""
    query = db.query(models.FoodType)
    if q:
        query = query.filter(models.FoodType.name.ilike(f"%{q}%"))
    food_types = query.order_by(models.FoodType.name).all()
    return _enrich_food_types(food_types, db)


@router.get("/{food_type_id}", response_model=schemas.FoodTypeOut)
def get_food_type(food_type_id: int, db: Session = Depends(get_db)):
    ft = db.query(models.FoodType).filter(models.FoodType.id == food_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Food type not found")
    return ft


@router.post("/", response_model=schemas.FoodTypeOut, status_code=201)
async def create_food_type(
    name: str = Form(...),
    description: str | None = Form(None),
    ai_image_id: str | None = Form(None),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Add a new food type (requires sign-in). Optional AI image or file upload."""
    existing = db.query(models.FoodType).filter(
        models.FoodType.name.ilike(name)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Food type '{name}' already exists")

    image_url = await _resolve_food_image(ai_image_id, image)

    ft = models.FoodType(
        name=name,
        description=description or None,
        image_url=image_url,
    )
    db.add(ft)
    db.commit()
    db.refresh(ft)
    return ft


@router.put("/{food_type_id}", response_model=schemas.FoodTypeOut)
async def update_food_type(
    food_type_id: int,
    name: str = Form(...),
    description: str | None = Form(None),
    ai_image_id: str | None = Form(None),
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Update a food type (requires sign-in). Optional AI image or file upload."""
    ft = db.query(models.FoodType).filter(models.FoodType.id == food_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Food type not found")

    duplicate = (
        db.query(models.FoodType)
        .filter(models.FoodType.name.ilike(name), models.FoodType.id != food_type_id)
        .first()
    )
    if duplicate:
        raise HTTPException(status_code=400, detail=f"Food type '{name}' already exists")

    ft.name = name
    ft.description = description or None
    new_image_url = await _resolve_food_image(ai_image_id, image)
    if new_image_url:
        ft.image_url = new_image_url

    db.commit()
    db.refresh(ft)
    return ft


@router.put("/{food_type_id}/photo", response_model=schemas.FoodTypeOut)
async def update_food_type_photo(
    food_type_id: int,
    ai_image_id: Annotated[str | None, Form()] = None,
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Update only the food type photo from AI generation or a file upload."""
    ft = db.query(models.FoodType).filter(models.FoodType.id == food_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Food type not found")

    new_image_url = await _resolve_food_image(ai_image_id, image)
    if not new_image_url:
        raise HTTPException(
            status_code=400,
            detail="Provide an AI image selection or upload an image file.",
        )

    ft.image_url = new_image_url
    db.commit()
    db.refresh(ft)
    return ft


@router.delete("/{food_type_id}", status_code=204)
def delete_food_type(
    food_type_id: int,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    ft = db.query(models.FoodType).filter(models.FoodType.id == food_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Food type not found")
    db.delete(ft)
    db.commit()
