from fastapi import APIRouter, Query
from fastapi.responses import Response

import schemas
from food_images import generate_food_images, get_cached_image

router = APIRouter(prefix="/food-images", tags=["Food Images"])


def _generation_response(result: dict) -> schemas.FoodImageSearchResponse:
    return schemas.FoodImageSearchResponse(
        photos=[schemas.FoodImageSearchResult(**photo) for photo in result["photos"]],
        search_help=result.get("search_help"),
    )


@router.get("/generate", response_model=schemas.FoodImageSearchResponse)
def generate_food_images_endpoint(
    q: str = Query(..., min_length=2, description="Food name to generate images for"),
    limit: int = Query(3, ge=1, le=4),
):
    """Generate food photos with Hugging Face text-to-image."""
    result = generate_food_images(q, limit=limit)
    return _generation_response(result)


@router.get("/search", response_model=schemas.FoodImageSearchResponse)
def search_food_images_endpoint(
    q: str = Query(..., min_length=2, description="Food name to generate images for"),
    limit: int = Query(3, ge=1, le=4),
):
    """Alias for /generate (legacy route name)."""
    result = generate_food_images(q, limit=limit)
    return _generation_response(result)


@router.get("/generated/{image_id}")
def get_generated_food_image(image_id: str):
    """Serve a cached AI-generated image for preview."""
    data, content_type = get_cached_image(image_id)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "private, max-age=3600"},
    )
