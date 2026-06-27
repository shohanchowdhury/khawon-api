import io
import os
import secrets
import time

import httpx
from dotenv import load_dotenv
from fastapi import HTTPException
from huggingface_hub import InferenceClient
from PIL import Image

from storage import MAX_BYTES

load_dotenv()

CACHE_TTL_SECONDS = 3600
MAX_GENERATIONS = 4
DEFAULT_MODEL = "black-forest-labs/FLUX.1-schnell"
GENERATION_HELP = (
    "AI image generation is not configured. Create a fine-grained HF token with "
    "'Make calls to Inference Providers' at huggingface.co/settings/tokens and set "
    "HF_TOKEN in your API environment. You can also upload a photo manually below."
)
PERMISSION_HELP = (
    "Your Hugging Face token cannot call Inference Providers. You must CREATE A NEW "
    "fine-grained token (editing an old Read/Write token does not work). At "
    "huggingface.co/settings/tokens/new?tokenType=fineGrained enable "
    "'Make calls to Inference Providers', put the new token in HF_TOKEN, then "
    "restart the API. Run 'huggingface-cli logout' if you previously logged in locally."
)

_generation_cache: dict[str, tuple[float, bytes, str]] = {}


def _hf_token() -> str | None:
    for name in ("HF_TOKEN", "HUGGINGFACE_API_TOKEN", "HUGGING_FACE_API_KEY"):
        value = os.getenv(name, "").strip()
        if value:
            return value
    return None


def _hf_model() -> str:
    return os.getenv("HF_IMAGE_MODEL", DEFAULT_MODEL).strip() or DEFAULT_MODEL


def _hf_provider() -> str:
    return os.getenv("HF_INFERENCE_PROVIDER", "hf-inference").strip() or "hf-inference"


def _build_prompt(food_name: str) -> str:
    food_name = food_name.strip()
    if not food_name:
        raise HTTPException(status_code=400, detail="Food name is required.")
    return (
        f"Professional food photography of {food_name}, Bangladeshi cuisine, "
        "appetizing dish on a plate, well-lit, shallow depth of field, "
        "high quality, no text, no watermark"
    )


def _purge_expired_cache() -> None:
    now = time.time()
    expired = [key for key, (expires_at, _, _) in _generation_cache.items() if expires_at <= now]
    for key in expired:
        _generation_cache.pop(key, None)


def _cache_store(data: bytes, content_type: str) -> str:
    if len(data) > MAX_BYTES:
        raise HTTPException(status_code=502, detail="Generated image is too large.")
    _purge_expired_cache()
    image_id = secrets.token_urlsafe(16)
    _generation_cache[image_id] = (time.time() + CACHE_TTL_SECONDS, data, content_type)
    return image_id


def get_cached_image(image_id: str) -> tuple[bytes, str]:
    _purge_expired_cache()
    entry = _generation_cache.get(image_id.strip())
    if not entry:
        raise HTTPException(
            status_code=404,
            detail="Generated image expired or not found. Generate again and save promptly.",
        )
    _, data, content_type = entry
    return data, content_type


def pop_cached_image(image_id: str) -> tuple[bytes, str]:
    _purge_expired_cache()
    entry = _generation_cache.pop(image_id.strip(), None)
    if not entry:
        raise HTTPException(
            status_code=404,
            detail="Generated image expired or not found. Generate again and save promptly.",
        )
    _, data, content_type = entry
    return data, content_type


def _pil_to_bytes(image: Image.Image) -> tuple[bytes, str]:
    if image.mode not in ("RGB", "RGBA"):
        image = image.convert("RGB")
    buffer = io.BytesIO()
    image.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if not data:
        raise HTTPException(status_code=502, detail="Generated image is empty.")
    return data, "image/png"


def _generate_one_image(client: InferenceClient, prompt: str, seed: int) -> tuple[bytes, str]:
    try:
        image = client.text_to_image(
            prompt,
            model=_hf_model(),
            seed=seed,
        )
    except httpx.HTTPError as exc:
        raise HTTPException(status_code=502, detail=f"AI image generation failed: {exc}") from exc
    except Exception as exc:
        message = str(exc)
        if "403" in message and ("permission" in message.lower() or "forbidden" in message.lower()):
            raise HTTPException(status_code=403, detail=PERMISSION_HELP) from exc
        if "429" in message or "rate limit" in message.lower():
            raise HTTPException(
                status_code=429,
                detail="Hugging Face rate limit reached. Wait a minute and try again.",
            ) from exc
        raise HTTPException(status_code=502, detail=f"AI image generation failed: {message}") from exc

    if not isinstance(image, Image.Image):
        raise HTTPException(status_code=502, detail="Unexpected response from image model.")
    return _pil_to_bytes(image)


def generate_food_images(query: str, limit: int = 3) -> dict:
    """Generate food photos with Hugging Face text-to-image."""
    token = _hf_token()
    if not token:
        return {"photos": [], "search_help": GENERATION_HELP}

    count = max(1, min(limit, MAX_GENERATIONS))
    prompt = _build_prompt(query)
    client = InferenceClient(api_key=token, provider=_hf_provider())

    photos = []
    for index in range(count):
        seed = index + 1
        try:
            data, content_type = _generate_one_image(client, prompt, seed)
            image_id = _cache_store(data, content_type)
            photos.append(
                {
                    "id": image_id,
                    "image_url": image_id,
                    "thumbnail_url": image_id,
                    "title": f"AI option {index + 1}",
                    "source_url": "https://huggingface.co",
                }
            )
        except HTTPException as exc:
            if photos:
                break
            if exc.status_code == 429:
                return {"photos": [], "search_help": exc.detail}
            if exc.status_code == 403:
                return {"photos": [], "search_help": exc.detail}
            return {
                "photos": [],
                "search_help": (
                    f"{exc.detail} Also accept the model license at "
                    f"https://huggingface.co/{_hf_model()}"
                ),
            }

    if not photos:
        return {"photos": [], "search_help": GENERATION_HELP}

    return {"photos": photos, "search_help": None}
