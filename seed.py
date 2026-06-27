"""Seed sample Bangladesh food data for local development."""

from database import SessionLocal, engine
import models

SAMPLE_FOOD_TYPES = [
    {"name": "Biriyani", "description": "Fragrant spiced rice with meat"},
    {"name": "Kacchi Biriyani", "description": "Slow-cooked mutton biriyani in dum style"},
    {"name": "Ramen", "description": "Japanese noodle soup"},
    {"name": "Fuchka", "description": "Crispy shells with tangy tamarind water"},
    {"name": "Chotpoti", "description": "Spiced chickpeas and potatoes with tamarind"},
    {"name": "Haleem", "description": "Slow-cooked lentil and meat stew"},
    {"name": "Khichuri", "description": "Comfort rice and lentil one-pot dish"},
    {"name": "Beef Tehari", "description": "Spiced rice cooked with beef"},
    {"name": "Paratha", "description": "Flaky layered flatbread, often with egg or keema"},
    {"name": "Shawarma", "description": "Wrapped grilled meat with sauce and salad"},
    {"name": "Burger", "description": "Classic beef or chicken burgers"},
    {"name": "Pizza", "description": "Wood-fired or pan pizza slices and pies"},
    {"name": "Kebab", "description": "Grilled skewered meat, seekh or shami style"},
    {"name": "Mishti Doi", "description": "Sweet fermented yogurt dessert"},
    {"name": "Pitha", "description": "Traditional rice cakes and winter sweets"},
]

SAMPLE_RESTAURANTS = [
    {
        "name": "Sultan's Dine",
        "area": "Dhanmondi",
        "address": "Road 27, Dhanmondi, Dhaka",
        "phone": "+880 1711-000001",
        "google_maps_url": "https://maps.google.com",
        "food_types": ["Biriyani"],
    },
    {
        "name": "Tokyo Express",
        "area": "Gulshan",
        "address": "Gulshan 2, Dhaka",
        "phone": "+880 1711-000002",
        "google_maps_url": "https://maps.google.com",
        "food_types": ["Ramen"],
    },
    {
        "name": "Old Dhaka Fuchka House",
        "area": "Old Dhaka",
        "address": "Chawkbazar, Old Dhaka",
        "phone": "+880 1711-000003",
        "google_maps_url": "https://maps.google.com",
        "food_types": ["Fuchka", "Biriyani"],
    },
]

SAMPLE_REVIEWS = [
    {"restaurant": "Sultan's Dine", "food_type": "Biriyani", "reviewer_name": "Rahim", "rating": 5, "comment": "Best biriyani in Dhanmondi!"},
    {"restaurant": "Tokyo Express", "food_type": "Ramen", "reviewer_name": "Sadia", "rating": 4, "comment": "Rich broth, generous portions."},
    {"restaurant": "Old Dhaka Fuchka House", "food_type": "Fuchka", "reviewer_name": "Karim", "rating": 5, "comment": "Authentic street-style fuchka."},
]


def seed_food_types(db):
    added = 0
    for ft_data in SAMPLE_FOOD_TYPES:
        existing = db.query(models.FoodType).filter(
            models.FoodType.name.ilike(ft_data["name"])
        ).first()
        if existing:
            continue
        db.add(models.FoodType(**ft_data))
        added += 1
    db.commit()
    return added


def seed():
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        added = seed_food_types(db)
        if added:
            print(f"Added {added} food type(s).")
        else:
            print("All sample food types already exist.")

        if db.query(models.Restaurant).count() > 0:
            return

        food_type_map = {
            ft.name: ft.id
            for ft in db.query(models.FoodType).all()
        }

        restaurant_map = {}
        for r_data in SAMPLE_RESTAURANTS:
            food_names = r_data.pop("food_types")
            restaurant = models.Restaurant(**r_data)
            db.add(restaurant)
            db.flush()
            restaurant_map[restaurant.name] = restaurant.id

            for name in food_names:
                link = models.RestaurantFoodType(
                    restaurant_id=restaurant.id,
                    food_type_id=food_type_map[name],
                )
                db.add(link)

        for rev_data in SAMPLE_REVIEWS:
            review = models.Review(
                restaurant_id=restaurant_map[rev_data["restaurant"]],
                food_type_id=food_type_map[rev_data["food_type"]],
                reviewer_name=rev_data["reviewer_name"],
                rating=rev_data["rating"],
                comment=rev_data["comment"],
            )
            db.add(review)

        db.commit()
        print("Seeded restaurants and reviews.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
