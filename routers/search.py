from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
import models
import schemas
from food_detail import build_food_detail, get_restaurants_for_food_type

router = APIRouter(prefix="/search", tags=["Search"])


@router.get("/", response_model=schemas.SearchResult)
def search_food(
    q: str = Query(..., description="Food type to search, e.g. 'ramen'"),
    db: Session = Depends(get_db),
):
    """
    Search for a food type and get the best restaurants in Bangladesh.
    Results are sorted by average rating (highest first).
    """
    food_type = db.query(models.FoodType).filter(
        models.FoodType.name.ilike(f"%{q}%")
    ).first()

    if not food_type:
        raise HTTPException(
            status_code=404,
            detail=f"No food type found matching '{q}'. Try 'ramen', 'biriyani', etc."
        )

    restaurants = get_restaurants_for_food_type(db, food_type.id)

    return schemas.SearchResult(
        food_type=schemas.FoodTypeOut.model_validate(food_type),
        restaurants=restaurants,
    )
