from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from auth import get_current_user
from database import get_db
from dish_detail import review_to_out
import models
import schemas

router = APIRouter(prefix="/reviews", tags=["Reviews"])


@router.post("/", response_model=schemas.ReviewOut, status_code=201)
def submit_review(
    data: schemas.ReviewCreate,
    db: Session = Depends(get_db),
    current_user: models.User = Depends(get_current_user),
):
    """Review a dish (requires sign-in). The restaurant is derived through the
    dish. One review per user per dish - submitting again updates your review."""
    dish = db.query(models.Dish).filter(models.Dish.id == data.dish_id).first()
    if not dish:
        raise HTTPException(status_code=404, detail="Dish not found")

    review = db.query(models.Review).filter(
        models.Review.user_id == current_user.id,
        models.Review.dish_id == data.dish_id,
    ).first()
    if review is None:
        review = models.Review(dish_id=data.dish_id, user_id=current_user.id)
        db.add(review)
    review.rating = data.rating
    review.comment = data.comment

    db.commit()
    db.refresh(review)
    return review_to_out(review)


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
