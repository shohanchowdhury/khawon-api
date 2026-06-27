"""Seed sample Bangladesh food data for local development."""

from database import SessionLocal, engine
import models

SAMPLE_FOOD_TYPES = [
    {"name": "Biriyani", "description": "Fragrant spiced rice with meat"},
    {"name": "Ramen", "description": "Japanese noodle soup"},
    {"name": "Fuchka", "description": "Crispy shells with tangy tamarind water"},
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


def seed():
    models.Base.metadata.create_all(bind=engine)
    db = SessionLocal()

    try:
        if db.query(models.FoodType).count() > 0:
            print("Database already has data — skipping seed.")
            return

        food_type_map = {}
        for ft_data in SAMPLE_FOOD_TYPES:
            ft = models.FoodType(**ft_data)
            db.add(ft)
            db.flush()
            food_type_map[ft.name] = ft.id

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
        print("Seeded food types, restaurants, and reviews.")
    finally:
        db.close()


if __name__ == "__main__":
    seed()
