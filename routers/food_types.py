from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func

from auth import get_current_user
from database import get_db
import models
import schemas

router = APIRouter(prefix="/food-types", tags=["Food Types"])


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
    results = []

    for ft in food_types:
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

        results.append(schemas.FoodTypePopularOut(
            id=ft.id,
            name=ft.name,
            description=ft.description,
            restaurant_count=restaurant_count,
            review_count=review_count,
            average_rating=avg_rating,
        ))

    results.sort(
        key=lambda x: (-x.review_count, -(x.average_rating or 0), x.name.lower())
    )
    return results[:limit]


@router.get("/{food_type_id}", response_model=schemas.FoodTypeOut)
def get_food_type(food_type_id: int, db: Session = Depends(get_db)):
    ft = db.query(models.FoodType).filter(models.FoodType.id == food_type_id).first()
    if not ft:
        raise HTTPException(status_code=404, detail="Food type not found")
    return ft


@router.post("/", response_model=schemas.FoodTypeOut, status_code=201)
def create_food_type(
    data: schemas.FoodTypeCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Add a new food type (requires sign-in)"""
    existing = db.query(models.FoodType).filter(
        models.FoodType.name.ilike(data.name)
    ).first()
    if existing:
        raise HTTPException(status_code=400, detail=f"Food type '{data.name}' already exists")

    ft = models.FoodType(**data.model_dump())
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
