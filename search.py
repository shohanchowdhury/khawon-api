from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from sqlalchemy import func
from database import get_db
import models
import schemas

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

    # Get all restaurants serving this food type, with their average rating
    restaurant_ids = (
        db.query(models.RestaurantFoodType.restaurant_id)
        .filter(models.RestaurantFoodType.food_type_id == food_type.id)
        .subquery()
    )

    # Aggregate ratings per restaurant
    rating_subq = (
        db.query(
            models.Review.restaurant_id,
            func.avg(models.Review.rating).label("avg_rating"),
            func.count(models.Review.id).label("review_count"),
        )
        .filter(models.Review.food_type_id == food_type.id)
        .group_by(models.Review.restaurant_id)
        .subquery()
    )

    restaurants = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.id.in_(restaurant_ids))
        .all()
    )

    # Build results with ratings attached
    results = []
    for r in restaurants:
        agg = db.query(
            func.avg(models.Review.rating),
            func.count(models.Review.id)
        ).filter(
            models.Review.restaurant_id == r.id,
            models.Review.food_type_id == food_type.id,
        ).first()

        avg_rating = round(float(agg[0]), 1) if agg[0] else None
        review_count = agg[1] or 0

        food_types = [link.food_type for link in r.food_type_links]

        results.append(schemas.RestaurantOut(
            id=r.id,
            name=r.name,
            area=r.area,
            address=r.address,
            phone=r.phone,
            google_maps_url=r.google_maps_url,
            food_types=[schemas.FoodTypeOut.model_validate(ft) for ft in food_types],
            average_rating=avg_rating,
            review_count=review_count,
        ))

    # Sort: rated restaurants first (by rating desc), then unrated ones
    results.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))

    return schemas.SearchResult(
        food_type=schemas.FoodTypeOut.model_validate(food_type),
        restaurants=results,
    )
