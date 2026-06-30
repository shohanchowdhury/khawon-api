from pydantic import BaseModel, EmailStr, Field
from typing import Optional
from datetime import datetime


# ── Users ────────────────────────────────────────────────────

class UserCreate(BaseModel):
    email: EmailStr
    username: str = Field(..., min_length=3, max_length=50)
    password: str = Field(..., min_length=6, max_length=128)

class UserOut(BaseModel):
    id: int
    email: EmailStr
    username: str
    created_at: datetime

    model_config = {"from_attributes": True}

class Token(BaseModel):
    access_token: str
    token_type: str = "bearer"


# ── Food Types ──────────────────────────────────────────────

class FoodTypeCreate(BaseModel):
    name: str
    description: Optional[str] = None

class FoodTypeOut(BaseModel):
    id: int
    name: str
    description: Optional[str]
    image_url: Optional[str] = None

    model_config = {"from_attributes": True}

class FoodTypePopularOut(FoodTypeOut):
    restaurant_count: int = 0
    review_count: int = 0
    average_rating: Optional[float] = None


class FoodImageSearchResult(BaseModel):
    id: str
    image_url: str
    thumbnail_url: str
    title: Optional[str] = None
    source_url: Optional[str] = None


class FoodImageSearchResponse(BaseModel):
    photos: list[FoodImageSearchResult] = []
    search_help: Optional[str] = None


# ── Restaurants ─────────────────────────────────────────────

class RestaurantCreate(BaseModel):
    name: str
    area: Optional[str] = None
    address: Optional[str] = None
    phone: Optional[str] = None
    google_maps_url: Optional[str] = None
    website_url: Optional[str] = None
    google_place_id: Optional[str] = None
    google_photo_name: Optional[str] = None
    food_type_ids: list[int] = []   # which food types this restaurant serves

class RestaurantOut(BaseModel):
    id: int
    name: str
    area: Optional[str]
    address: Optional[str]
    phone: Optional[str]
    google_maps_url: Optional[str]
    website_url: Optional[str] = None
    google_place_id: Optional[str] = None
    image_url: Optional[str] = None
    food_types: list[FoodTypeOut] = []
    average_rating: Optional[float] = None
    review_count: int = 0

    model_config = {"from_attributes": True}


class PlacePhotoOut(BaseModel):
    name: str
    width_px: Optional[int] = None
    height_px: Optional[int] = None
    attribution: Optional[str] = None


class PlaceSearchResult(BaseModel):
    place_id: str
    name: str
    address: Optional[str] = None
    area: Optional[str] = None
    phone: Optional[str] = None
    google_maps_url: Optional[str] = None
    website_url: Optional[str] = None
    photos: list[PlacePhotoOut] = []
    photos_help: Optional[str] = None


# ── Reviews ─────────────────────────────────────────────────

class ReviewCreate(BaseModel):
    restaurant_id: int
    food_type_id: int
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


class FoodDetailResult(BaseModel):
    food_type: FoodTypePopularOut
    restaurants: list[RestaurantOut]
