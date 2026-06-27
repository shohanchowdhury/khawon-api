from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from sqlalchemy import func
from auth import get_current_user
from database import get_db
import models
import schemas

router = APIRouter(prefix="/restaurants", tags=["Restaurants"])


def _enrich(restaurant: models.Restaurant, db: Session) -> schemas.RestaurantOut:
    """Attach average rating and food types to a restaurant object."""
    agg = (
        db.query(func.avg(models.Review.rating), func.count(models.Review.id))
        .filter(models.Review.restaurant_id == restaurant.id)
        .first()
    )
    avg_rating = round(float(agg[0]), 1) if agg[0] else None
    review_count = agg[1] or 0

    food_types = [link.food_type for link in restaurant.food_type_links]

    return schemas.RestaurantOut(
        id=restaurant.id,
        name=restaurant.name,
        area=restaurant.area,
        address=restaurant.address,
        phone=restaurant.phone,
        google_maps_url=restaurant.google_maps_url,
        food_types=[schemas.FoodTypeOut.model_validate(ft) for ft in food_types],
        average_rating=avg_rating,
        review_count=review_count,
    )


@router.get("/", response_model=list[schemas.RestaurantOut])
def list_restaurants(db: Session = Depends(get_db)):
    """List all restaurants"""
    restaurants = db.query(models.Restaurant).order_by(models.Restaurant.name).all()
    return [_enrich(r, db) for r in restaurants]


@router.get("/{restaurant_id}", response_model=schemas.RestaurantOut)
def get_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return _enrich(r, db)


@router.post("/", response_model=schemas.RestaurantOut, status_code=201)
def create_restaurant(
    data: schemas.RestaurantCreate,
    db: Session = Depends(get_db),
    _current_user: models.User = Depends(get_current_user),
):
    """Add a new restaurant with its food types (requires sign-in)"""
    restaurant = models.Restaurant(
        name=data.name,
        area=data.area,
        address=data.address,
        phone=data.phone,
        google_maps_url=data.google_maps_url,
    )
    db.add(restaurant)
    db.flush()  # get the ID before committing

    for ft_id in data.food_type_ids:
        ft = db.query(models.FoodType).filter(models.FoodType.id == ft_id).first()
        if not ft:
            raise HTTPException(status_code=400, detail=f"Food type ID {ft_id} not found")
        link = models.RestaurantFoodType(restaurant_id=restaurant.id, food_type_id=ft_id)
        db.add(link)

    db.commit()
    db.refresh(restaurant)
    return _enrich(restaurant, db)


@router.put("/{restaurant_id}", response_model=schemas.RestaurantOut)
def update_restaurant(restaurant_id: int, data: schemas.RestaurantCreate, db: Session = Depends(get_db)):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    r.name = data.name
    r.area = data.area
    r.address = data.address
    r.phone = data.phone
    r.google_maps_url = data.google_maps_url

    # Reset food type links
    db.query(models.RestaurantFoodType).filter(
        models.RestaurantFoodType.restaurant_id == restaurant_id
    ).delete()

    for ft_id in data.food_type_ids:
        ft = db.query(models.FoodType).filter(models.FoodType.id == ft_id).first()
        if not ft:
            raise HTTPException(status_code=400, detail=f"Food type ID {ft_id} not found")
        link = models.RestaurantFoodType(restaurant_id=r.id, food_type_id=ft_id)
        db.add(link)

    db.commit()
    db.refresh(r)
    return _enrich(r, db)


@router.delete("/{restaurant_id}", status_code=204)
def delete_restaurant(restaurant_id: int, db: Session = Depends(get_db)):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    db.delete(r)
    db.commit()


@router.get("/{restaurant_id}/reviews", response_model=list[schemas.ReviewOut])
def get_restaurant_reviews(restaurant_id: int, db: Session = Depends(get_db)):
    r = db.query(models.Restaurant).filter(models.Restaurant.id == restaurant_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="Restaurant not found")
    return db.query(models.Review).filter(
        models.Review.restaurant_id == restaurant_id
    ).order_by(models.Review.created_at.desc()).all()
