import os

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException

load_dotenv()

PLACES_SEARCH_URL = "https://places.googleapis.com/v1/places:searchText"
PLACES_BASE = "https://places.googleapis.com/v1"
LEGACY_DETAILS_URL = "https://maps.googleapis.com/maps/api/place/details/json"
LEGACY_PHOTO_URL = "https://maps.googleapis.com/maps/api/place/photo"
MAX_PHOTOS_PER_PLACE = 8
SEARCH_FIELD_MASK = (
    "places.id,places.displayName,places.formattedAddress,"
    "places.nationalPhoneNumber,places.googleMapsUri,places.websiteUri,"
    "places.addressComponents,places.photos"
)
DETAILS_FIELD_MASK = (
    "id,displayName,formattedAddress,nationalPhoneNumber,googleMapsUri,"
    "websiteUri,addressComponents,photos"
)
AREA_TYPES = (
    "sublocality",
    "sublocality_level_1",
    "neighborhood",
    "locality",
    "administrative_area_level_2",
)
PHOTOS_HELP = (
    "Google returned no photos. Enable billing on your Google Cloud project and "
    "turn on Places API (New). The same API key is used — no separate photo API. "
    "You can also upload a photo manually below."
)


def _api_key() -> str:
    key = os.getenv("GOOGLE_PLACES_API_KEY", "").strip()
    if not key:
        raise HTTPException(
            status_code=503,
            detail="Google Places is not configured. Set GOOGLE_PLACES_API_KEY.",
        )
    return key


def _normalize_place_id(place_id: str) -> str:
    place_id = place_id.strip()
    if not place_id:
        raise HTTPException(status_code=400, detail="place_id is required.")
    if place_id.startswith("places/"):
        return place_id
    return f"places/{place_id}"


def _raw_place_id(place_id: str) -> str:
    return _normalize_place_id(place_id).removeprefix("places/")


def _validate_photo_name(photo_name: str) -> str:
    name = photo_name.strip()
    if name.startswith("legacy:"):
        ref = name.removeprefix("legacy:")
        if not ref:
            raise HTTPException(status_code=400, detail="Invalid legacy photo reference.")
        return name
    if not name.startswith("places/") or "/photos/" not in name:
        raise HTTPException(status_code=400, detail="Invalid Google photo reference.")
    return name


def _extract_area(components: list[dict] | None) -> str | None:
    for area_type in AREA_TYPES:
        for component in components or []:
            if area_type in component.get("types", []):
                return component.get("longText") or component.get("shortText")
    return None


def _photo_attribution(attributions: list[dict] | None) -> str | None:
    if not attributions:
        return None
    first = attributions[0]
    return first.get("displayName") or first.get("uri")


def _parse_new_photos(photos: list[dict] | None) -> list[dict]:
    parsed = []
    for photo in (photos or [])[:MAX_PHOTOS_PER_PLACE]:
        name = photo.get("name")
        if not name:
            continue
        parsed.append(
            {
                "name": name,
                "width_px": photo.get("widthPx"),
                "height_px": photo.get("heightPx"),
                "attribution": _photo_attribution(photo.get("authorAttributions")),
            }
        )
    return parsed


def _legacy_place_photos(place_id: str) -> list[dict]:
    """Fallback for accounts where Places (New) returns empty photos arrays."""
    try:
        response = httpx.get(
            LEGACY_DETAILS_URL,
            params={
                "place_id": _raw_place_id(place_id),
                "fields": "photos",
                "key": _api_key(),
            },
            timeout=15.0,
        )
    except httpx.HTTPError:
        return []

    if not response.is_success:
        return []

    payload = response.json()
    if payload.get("status") != "OK":
        return []

    parsed = []
    for photo in (payload.get("result", {}).get("photos") or [])[:MAX_PHOTOS_PER_PLACE]:
        ref = photo.get("photo_reference")
        if not ref:
            continue
        parsed.append(
            {
                "name": f"legacy:{ref}",
                "width_px": photo.get("width"),
                "height_px": photo.get("height"),
                "attribution": None,
            }
        )
    return parsed


def _load_photos(place_id: str, new_photos: list[dict] | None) -> list[dict]:
    photos = _parse_new_photos(new_photos)
    if photos:
        return photos
    return _legacy_place_photos(place_id)


def _parse_place(place: dict, *, include_legacy_photos: bool = True) -> dict:
    display_name = place.get("displayName") or {}
    place_id = place.get("id") or place.get("name")
    normalized_id = _normalize_place_id(place_id) if place_id else None
    new_photos = place.get("photos")
    photos = (
        _load_photos(normalized_id, new_photos)
        if include_legacy_photos and normalized_id
        else _parse_new_photos(new_photos)
    )

    return {
        "place_id": normalized_id or place_id,
        "name": display_name.get("text") or "",
        "address": place.get("formattedAddress"),
        "area": _extract_area(place.get("addressComponents")),
        "phone": place.get("nationalPhoneNumber"),
        "google_maps_url": place.get("googleMapsUri"),
        "website_url": place.get("websiteUri"),
        "photos": photos,
        "photos_help": PHOTOS_HELP if not photos else None,
    }


def _places_error(response: httpx.Response) -> HTTPException:
    if response.status_code == 403:
        return HTTPException(
            status_code=502,
            detail="Google Places access denied. Enable Places API (New) and billing in Google Cloud Console.",
        )
    detail = response.text[:240] or response.reason_phrase
    return HTTPException(
        status_code=502,
        detail=f"Google Places error ({response.status_code}): {detail}",
    )


def get_place_details(place_id: str) -> dict:
    """Fetch a single place with photos (New API, legacy fallback if needed)."""
    resource_name = _normalize_place_id(place_id)

    try:
        response = httpx.get(
            f"{PLACES_BASE}/{resource_name}",
            headers={
                "X-Goog-Api-Key": _api_key(),
                "X-Goog-FieldMask": DETAILS_FIELD_MASK,
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Google Places request failed: {exc}",
        ) from exc

    if not response.is_success:
        raise _places_error(response)

    place = response.json()
    if not place.get("id") and not place.get("name"):
        raise HTTPException(status_code=404, detail="Place not found on Google.")
    return _parse_place(place)


def search_places(query: str, area: str | None = None, limit: int = 5) -> list[dict]:
    text_query = query.strip()
    if not text_query:
        raise HTTPException(status_code=400, detail="Search query is required.")

    if area and area.strip():
        text_query = f"{text_query} {area.strip()}, Dhaka, Bangladesh"
    else:
        text_query = f"{text_query}, Bangladesh"

    payload = {
        "textQuery": text_query,
        "regionCode": "BD",
        "languageCode": "en",
        "maxResultCount": limit,
    }

    try:
        response = httpx.post(
            PLACES_SEARCH_URL,
            json=payload,
            headers={
                "Content-Type": "application/json",
                "X-Goog-Api-Key": _api_key(),
                "X-Goog-FieldMask": SEARCH_FIELD_MASK,
            },
            timeout=15.0,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(
            status_code=502,
            detail=f"Google Places request failed: {exc}",
        ) from exc

    if not response.is_success:
        raise _places_error(response)

    places = response.json().get("places") or []
    results = []
    for place in places:
        if not place.get("id"):
            continue
        parsed = _parse_place(place)
        if not parsed["photos"]:
            try:
                detailed = get_place_details(parsed["place_id"])
                parsed["photos"] = detailed["photos"]
                parsed["photos_help"] = detailed.get("photos_help")
            except HTTPException:
                parsed["photos_help"] = PHOTOS_HELP
        results.append(parsed)
    return results


def fetch_place_photo_bytes(photo_name: str, max_width: int = 1200) -> tuple[bytes, str]:
    safe_name = _validate_photo_name(photo_name)
    max_width = max(1, min(max_width, 4800))

    if safe_name.startswith("legacy:"):
        ref = safe_name.removeprefix("legacy:")
        try:
            response = httpx.get(
                LEGACY_PHOTO_URL,
                params={"maxwidth": max_width, "photo_reference": ref, "key": _api_key()},
                follow_redirects=True,
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Google photo request failed: {exc}",
            ) from exc
    else:
        try:
            response = httpx.get(
                f"{PLACES_BASE}/{safe_name}/media",
                params={"maxWidthPx": max_width, "skipHttpRedirect": "false"},
                headers={"X-Goog-Api-Key": _api_key()},
                follow_redirects=True,
                timeout=20.0,
            )
        except httpx.HTTPError as exc:
            raise HTTPException(
                status_code=502,
                detail=f"Google photo request failed: {exc}",
            ) from exc

    if not response.is_success:
        raise HTTPException(
            status_code=502,
            detail=(
                "Google photo download failed. Enable billing on Google Cloud and "
                "Places API (New) — same key, no extra photo API."
            ),
        )

    content_type = response.headers.get("content-type", "image/jpeg").split(";")[0]
    if not content_type.startswith("image/"):
        content_type = "image/jpeg"

    if len(response.content) < 100:
        raise HTTPException(status_code=502, detail="Google returned an empty photo.")

    return response.content, content_type
