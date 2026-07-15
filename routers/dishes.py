from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
import schemas
from dish_detail import (
    get_canonical_dish_comparison,
    get_dish,
    get_reviews_for_dish,
    search_canonical_dishes,
    search_dishes,
)

router = APIRouter(prefix="/dishes", tags=["Dishes"])


@router.get("/search", response_model=schemas.DishSearchResult)
def search_dishes_endpoint(
    q: str = Query(..., description="Dish to search, e.g. 'biryani'"),
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """
    Search for a food. Returns two things:
      - canonical_matches: a small capped "compare across restaurants" strip.
      - dishes: the full paginated flat dish list, most-relevant first, which
        also includes single-restaurant / non-canonical dishes so nothing that
        matches is hidden. total/offset/limit describe this dish list.
    Empty results on no match, not a 404 - coming up empty is a normal state.
    """
    canonical_matches = search_canonical_dishes(db, q)
    dishes, total = search_dishes(db, q, offset=offset, limit=limit)
    return schemas.DishSearchResult(
        query=q,
        canonical_matches=canonical_matches,
        total=total,
        offset=offset,
        limit=limit,
        dishes=dishes,
    )


@router.get("/compare/{canonical_dish_id}", response_model=schemas.DishCompareResult)
def compare_dish(
    canonical_dish_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    """One canonical dish across every restaurant serving it, best-rated first."""
    result = get_canonical_dish_comparison(
        db,
        canonical_dish_id,
        offset=offset,
        limit=limit,
    )
    if result is None:
        raise HTTPException(status_code=404, detail="Canonical dish not found")
    return result


@router.get("/{dish_id}/reviews", response_model=schemas.ReviewListResult)
def get_dish_reviews(
    dish_id: int,
    offset: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    dish = get_dish(db, dish_id)
    if dish is None:
        raise HTTPException(status_code=404, detail="Dish not found")
    reviews, total = get_reviews_for_dish(db, dish_id, offset=offset, limit=limit)
    return schemas.ReviewListResult(reviews=reviews, total=total, offset=offset, limit=limit)


@router.get("/{dish_id}", response_model=schemas.DishOut)
def get_dish_detail(dish_id: int, db: Session = Depends(get_db)):
    dish = get_dish(db, dish_id)
    if dish is None:
        raise HTTPException(status_code=404, detail="Dish not found")
    return dish
