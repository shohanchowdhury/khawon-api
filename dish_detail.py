from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

import models
import schemas


def _dish_query(db: Session):
    return db.query(models.Dish).options(
        joinedload(models.Dish.food_type),
        joinedload(models.Dish.restaurant),
        joinedload(models.Dish.cuisine_links).joinedload(models.DishCuisine.cuisine),
        joinedload(models.Dish.flavor_tag_links).joinedload(models.DishFlavorTag.flavor_tag),
    )


def enrich_dishes(db: Session, dishes: list[models.Dish]) -> list[schemas.DishOut]:
    if not dishes:
        return []

    ids = [d.id for d in dishes]
    review_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Review.dish_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(models.Review.dish_id.in_(ids))
        .group_by(models.Review.dish_id)
        .all()
    }

    results = []
    for d in dishes:
        avg_raw, review_count = review_stats.get(d.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None

        results.append(
            schemas.DishOut(
                id=d.id,
                name=d.name,
                description=d.description,
                price_bdt=d.price_bdt,
                image_url=d.image_url,
                is_sold_out=d.is_sold_out,
                category_raw=d.category_raw,
                variations=d.variations,
                food_type=schemas.FoodTypeOut.model_validate(d.food_type) if d.food_type else None,
                cuisines=[schemas.CuisineOut.model_validate(link.cuisine) for link in d.cuisine_links],
                flavor_tags=[schemas.FlavorTagOut.model_validate(link.flavor_tag) for link in d.flavor_tag_links],
                restaurant=schemas.RestaurantSummaryOut.model_validate(d.restaurant),
                average_rating=avg_rating,
                review_count=review_count or 0,
            )
        )
    return results


def _sort_by_rating(dishes: list[schemas.DishOut]) -> list[schemas.DishOut]:
    dishes.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))
    return dishes


def search_dishes(db: Session, q: str) -> list[schemas.DishOut]:
    pattern = f"%{q}%"
    dishes = (
        _dish_query(db)
        .outerjoin(models.FoodType, models.Dish.food_type_id == models.FoodType.id)
        .filter(or_(models.Dish.name.ilike(pattern), models.FoodType.name.ilike(pattern)))
        .all()
    )
    return _sort_by_rating(enrich_dishes(db, dishes))


def get_dishes_for_restaurant(db: Session, restaurant_id: int) -> list[schemas.DishOut]:
    dishes = (
        _dish_query(db)
        .filter(models.Dish.restaurant_id == restaurant_id)
        .all()
    )
    return enrich_dishes(db, dishes)


def get_dish(db: Session, dish_id: int) -> schemas.DishOut | None:
    dish = _dish_query(db).filter(models.Dish.id == dish_id).first()
    if dish is None:
        return None
    return enrich_dishes(db, [dish])[0]
