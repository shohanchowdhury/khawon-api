from fastapi import APIRouter, Query
from fastapi.responses import Response

import schemas
from places import fetch_place_photo_bytes, get_place_details, search_places

router = APIRouter(prefix="/places", tags=["Places"])


@router.get("/search", response_model=list[schemas.PlaceSearchResult])
def search_places_endpoint(
    q: str = Query(..., min_length=2, description="Restaurant name to look up"),
    area: str | None = Query(None, description="Optional area, e.g. Dhanmondi"),
    limit: int = Query(5, ge=1, le=10),
):
    """Search Google Places for restaurant details (Maps link, website, phone, address, photos)."""
    return search_places(q, area=area, limit=limit)


@router.get("/details", response_model=schemas.PlaceSearchResult)
def get_place_details_endpoint(
    place_id: str = Query(..., description="Google place id, e.g. places/ChIJ..."),
):
    """Fetch Google place details including photos for an existing place id."""
    return get_place_details(place_id)


@router.get("/photo")
def get_place_photo(
    photo_name: str = Query(..., description="Google Places photo resource name"),
    max_width: int = Query(400, ge=100, le=1600),
):
    """Proxy a Google Places photo for thumbnails (API key stays on server)."""
    data, content_type = fetch_place_photo_bytes(photo_name, max_width=max_width)
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
