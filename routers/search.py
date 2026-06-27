from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session, joinedload
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

    restaurant_ids = [
        row[0]
        for row in db.query(models.RestaurantFoodType.restaurant_id)
        .filter(models.RestaurantFoodType.food_type_id == food_type.id)
        .all()
    ]

    if not restaurant_ids:
        return schemas.SearchResult(
            food_type=schemas.FoodTypeOut.model_validate(food_type),
            restaurants=[],
        )

    rating_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Review.restaurant_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(
            models.Review.food_type_id == food_type.id,
            models.Review.restaurant_id.in_(restaurant_ids),
        )
        .group_by(models.Review.restaurant_id)
        .all()
    }

    restaurants = (
        db.query(models.Restaurant)
        .filter(models.Restaurant.id.in_(restaurant_ids))
        .options(
            joinedload(models.Restaurant.food_type_links).joinedload(
                models.RestaurantFoodType.food_type
            )
        )
        .all()
    )

    results = []
    for r in restaurants:
        avg_raw, review_count = rating_stats.get(r.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None
        food_types = [link.food_type for link in r.food_type_links]

        results.append(
            schemas.RestaurantOut(
                id=r.id,
                name=r.name,
                area=r.area,
                address=r.address,
                phone=r.phone,
                google_maps_url=r.google_maps_url,
                website_url=r.website_url,
                google_place_id=r.google_place_id,
                image_url=r.image_url,
                food_types=[
                    schemas.FoodTypeOut.model_validate(ft) for ft in food_types
                ],
                average_rating=avg_rating,
                review_count=review_count or 0,
            )
        )

    results.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))

    return schemas.SearchResult(
        food_type=schemas.FoodTypeOut.model_validate(food_type),
        restaurants=results,
    )
