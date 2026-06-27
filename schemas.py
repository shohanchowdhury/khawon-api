from pydantic import BaseModel, Field
from typing import Optional
from datetime import datetime


# ── Food Types ──────────────────────────────────────────────

class FoodTypeCreate(BaseModel):
    name: str
    description: Optional[str] = None

class FoodTypeOut(BaseModel):
    id: int
    name: str
    description: Optional[str]

    model_config = {"from_attributes": True}


# ── Restaurants ─────────────────────────────────────────────

class RestaurantCreate(BaseModel):
    name: str
    area: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    google_maps_url: Optional[str] = None
    food_type_ids: list[int] = []   # which food types this restaurant serves

class RestaurantOut(BaseModel):
    id: int
    name: str
    area: Optional[str]
    address: Optional[str]
    phone: Optional[str]
    google_maps_url: Optional[str]
    food_types: list[FoodTypeOut] = []
    average_rating: Optional[float] = None
    review_count: int = 0

    model_config = {"from_attributes": True}


# ── Reviews ─────────────────────────────────────────────────

class ReviewCreate(BaseModel):
    restaurant_id: int
    food_type_id: int
    reviewer_name: Optional[str] = "Anonymous"
    rating: int = Field(..., ge=1, le=5)
    comment: Optional[str] = None

class ReviewOut(BaseModel):
    id: int
    restaurant_id: int
    food_type_id: int
    reviewer_name: Optional[str]
    rating: int
    comment: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


# ── Search ───────────────────────────────────────────────────

class SearchResult(BaseModel):
    food_type: FoodTypeOut
    restaurants: list[RestaurantOut]
