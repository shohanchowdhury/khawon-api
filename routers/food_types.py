from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import get_current_user
from database import get_db
import models
import schemas
from storage import upload_food_image

router = APIRouter(prefix="/food-types", tags=["Food Types"])


def _enrich_food_type(ft: models.FoodType, db: Session) -> schemas.FoodTypePopularOut:
    restaurant_count = (
        db.query(models.RestaurantFoodType)
        .filter(models.RestaurantFoodType.food_type_id == ft.id)
        .count()
    )
    agg = (
        db.query(func.avg(models.Review.rating), func.count(models.Review.id))
        .filter(models.Review.food_type_id == ft.id)
        .first()
    )
    avg_rating = round(float(agg[0]), 1) if agg[0] else None
    review_count = agg[1] or 0

    return schemas.FoodTypePopularOut(
        id=ft.id,
        name=ft.name,
        description=ft.description,
        image_url=ft.image_url,
        restaurant_count=restaurant_count,
        review_count=review_count,
        average_rating=avg_rating,
    )


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
    results = [_enrich_food_type(ft, db) for ft in db.query(models.FoodType).all()]
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
    return [_enrich_food_type(ft, db) for ft in food_types]


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
    image: UploadFile | None = File(None),
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Add a new food type (requires sign-in). Optional image upload."""
    existing = db.query(models.FoodType).filter(
        models.FoodType.name.ilike(name)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Food type '{name}' already exists")

    image_url = None
    if image and image.filename:
        image_url = await upload_food_image(image)

    ft = models.FoodType(
        name=name,
        description=description or None,
        image_url=image_url,
    )
    db.add(ft)
    db.commit()
    db.refresh(ft)
    return ft


@router.delete("/{food_type_id}", status_code=204)
def delete_food_type(food_type_id: int, db: Session = Depends(get_db)):
    ft = db.query(models.FoodType).filter(models.FoodType.id == food_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Food type not found")
    db.delete(ft)
    db.commit()
