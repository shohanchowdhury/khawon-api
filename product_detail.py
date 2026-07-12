from sqlalchemy.orm import Session, joinedload
from sqlalchemy import func, or_

import models
import schemas


def _product_query(db: Session):
    return db.query(models.Product).options(
        joinedload(models.Product.food_type),
        joinedload(models.Product.restaurant),
        joinedload(models.Product.cuisine_links).joinedload(models.ProductCuisine.cuisine),
        joinedload(models.Product.flavor_tag_links).joinedload(models.ProductFlavorTag.flavor_tag),
    )


def enrich_products(db: Session, products: list[models.Product]) -> list[schemas.ProductOut]:
    if not products:
        return []

    ids = [p.id for p in products]
    review_stats = {
        row[0]: (row[1], row[2])
        for row in db.query(
            models.Review.product_id,
            func.avg(models.Review.rating),
            func.count(models.Review.id),
        )
        .filter(models.Review.product_id.in_(ids))
        .group_by(models.Review.product_id)
        .all()
    }

    results = []
    for p in products:
        avg_raw, review_count = review_stats.get(p.id, (None, 0))
        avg_rating = round(float(avg_raw), 1) if avg_raw else None

        results.append(
            schemas.ProductOut(
                id=p.id,
                name=p.name,
                description=p.description,
                price_bdt=p.price_bdt,
                image_url=p.image_url,
                is_sold_out=p.is_sold_out,
                category_raw=p.category_raw,
                variations=p.variations,
                food_type=schemas.FoodTypeOut.model_validate(p.food_type) if p.food_type else None,
                cuisines=[schemas.CuisineOut.model_validate(link.cuisine) for link in p.cuisine_links],
                flavor_tags=[schemas.FlavorTagOut.model_validate(link.flavor_tag) for link in p.flavor_tag_links],
                restaurant=schemas.RestaurantSummaryOut.model_validate(p.restaurant),
                average_rating=avg_rating,
                review_count=review_count or 0,
            )
        )
    return results


def _sort_by_rating(products: list[schemas.ProductOut]) -> list[schemas.ProductOut]:
    products.sort(key=lambda x: (x.average_rating is None, -(x.average_rating or 0)))
    return products


def search_products(db: Session, q: str) -> list[schemas.ProductOut]:
    pattern = f"%{q}%"
    products = (
        _product_query(db)
        .outerjoin(models.FoodType, models.Product.food_type_id == models.FoodType.id)
        .filter(or_(models.Product.name.ilike(pattern), models.FoodType.name.ilike(pattern)))
        .all()
    )
    return _sort_by_rating(enrich_products(db, products))


def get_products_for_restaurant(db: Session, restaurant_id: int) -> list[schemas.ProductOut]:
    products = (
        _product_query(db)
        .filter(models.Product.restaurant_id == restaurant_id)
        .all()
    )
    return enrich_products(db, products)


def get_product(db: Session, product_id: int) -> schemas.ProductOut | None:
    product = _product_query(db).filter(models.Product.id == product_id).first()
    if product is None:
        return None
    return enrich_products(db, [product])[0]
