from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from database import get_db
import schemas
from product_detail import get_product, search_products

router = APIRouter(prefix="/products", tags=["Products"])


@router.get("/search", response_model=schemas.DishSearchResult)
def search_dishes(
    q: str = Query(..., description="Dish to search, e.g. 'biryani'"),
    db: Session = Depends(get_db),
):
    """
    Search for a specific dish and compare it across restaurants.
    Matches on the dish's own name or its food type. Empty results on no
    match, not a 404 - a dish search coming up empty is a normal state.
    """
    return schemas.DishSearchResult(query=q, results=search_products(db, q))


@router.get("/{product_id}", response_model=schemas.ProductOut)
def get_product_detail(product_id: int, db: Session = Depends(get_db)):
    product = get_product(db, product_id)
    if product is None:
        raise HTTPException(status_code=404, detail="Product not found")
    return product
