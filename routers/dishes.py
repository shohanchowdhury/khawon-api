from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
import schemas
from dish_detail import get_dish, search_dishes

router = APIRouter(prefix="/dishes", tags=["Dishes"])


@router.get("/search", response_model=schemas.DishSearchResult)
def search_dishes_endpoint(
    q: str = Query(..., description="Dish to search, e.g. 'biryani'"),
    db: Session = Depends(get_db),
):
    """
    Search for a specific dish and compare it across restaurants.
    Matches on the dish's own name or its food type. Empty results on no
    match, not a 404 - a dish search coming up empty is a normal state.
    """
    return schemas.DishSearchResult(query=q, results=search_dishes(db, q))


@router.get("/{dish_id}", response_model=schemas.DishOut)
def get_dish_detail(dish_id: int, db: Session = Depends(get_db)):
    dish = get_dish(db, dish_id)
    if dish is None:
        raise HTTPException(status_code=404, detail="Dish not found")
    return dish
