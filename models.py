from sqlalchemy import (
    Column, Integer, String, Text, ForeignKey,
    DateTime, CheckConstraint
)
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from database import Base


class User(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String(255), nullable=False, unique=True, index=True)
    username = Column(String(50), nullable=False, unique=True, index=True)
    hashed_password = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    reviews = relationship("Review", back_populates="user")


class FoodType(Base):
    __tablename__ = "food_types"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False, unique=True)
    description = Column(Text, nullable=True)

    restaurant_links = relationship("RestaurantFoodType", back_populates="food_type")


class Restaurant(Base):
    __tablename__ = "restaurants"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(200), nullable=False)
    area = Column(String(100), nullable=True)       # e.g. Dhanmondi, Gulshan
    address = Column(Text, nullable=True)
    phone = Column(String(30), nullable=True)
    google_maps_url = Column(Text, nullable=True)

    food_type_links = relationship("RestaurantFoodType", back_populates="restaurant")
    reviews = relationship("Review", back_populates="restaurant")


class RestaurantFoodType(Base):
    """Join table: which restaurant serves which food type"""
    __tablename__ = "restaurant_food_types"

    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), primary_key=True)
    food_type_id = Column(Integer, ForeignKey("food_types.id", ondelete="CASCADE"), primary_key=True)

    restaurant = relationship("Restaurant", back_populates="food_type_links")
    food_type = relationship("FoodType", back_populates="restaurant_links")


class Review(Base):
    __tablename__ = "reviews"

    id = Column(Integer, primary_key=True, index=True)
    restaurant_id = Column(Integer, ForeignKey("restaurants.id", ondelete="CASCADE"), nullable=False)
    food_type_id = Column(Integer, ForeignKey("food_types.id", ondelete="CASCADE"), nullable=False)
    user_id = Column(Integer, ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    reviewer_name = Column(String(100), nullable=True)
    rating = Column(Integer, CheckConstraint("rating BETWEEN 1 AND 5"), nullable=False)
    comment = Column(Text, nullable=True)
    created_at = Column(DateTime, server_default=func.now())

    restaurant = relationship("Restaurant", back_populates="reviews")
    user = relationship("User", back_populates="reviews")
