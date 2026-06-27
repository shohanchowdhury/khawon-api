from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
import models
import schemas

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.post("/", response_model=schemas.ReviewOut, status_code=201)
def submit_review(
    data: schemas.ReviewCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Submit a review for a restaurant (requires sign-in)"""
    restaurant = db.query(models.Restaurant).filter(
        models.Restaurant.id == data.restaurant_id
    ).first()
    if not restaurant:
        raise HTTPException(status_code=404, detail="Restaurant not found")

    food_type = db.query(models.FoodType).filter(
        models.FoodType.id == data.food_type_id
    ).first()
    if not food_type:
        raise HTTPException(status_code=404, detail="Food type not found")

    link = db.query(models.RestaurantFoodType).filter(
        models.RestaurantFoodType.restaurant_id == data.restaurant_id,
        models.RestaurantFoodType.food_type_id == data.food_type_id,
    ).first()
    if not link:
        raise HTTPException(
            status_code=400,
            detail=f"'{restaurant.name}' does not serve '{food_type.name}'",
        )

    review = models.Review(
        restaurant_id=data.restaurant_id,
        food_type_id=data.food_type_id,
        user_id=current_user.id,
        reviewer_name=current_user.username,
        rating=data.rating,
        comment=data.comment,
    )
    db.add(review)
    db.commit()
    db.refresh(review)
    return review


@router.delete("/{review_id}", status_code=204)
def delete_review(
    review_id: int,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    review = db.query(models.Review).filter(models.Review.id == review_id).first()
    if not review:
        raise HTTPException(status_code=404, detail="Review not found")
    if review.user_id != current_user.id:
        raise HTTPException(status_code=403, detail="You can only delete your own reviews")
    db.delete(review)
    db.commit()
