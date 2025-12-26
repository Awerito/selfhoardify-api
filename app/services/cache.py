import base64
import hashlib
import urllib.request

import redis

from app.config import RedisConfig

ALBUM_ART_TTL = 60 * 60 * 24 * 7  # 7 days


def fetch_image_as_base64(url: str) -> str | None:
    """Download an image and convert it to base64."""
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            data = response.read()
            return base64.b64encode(data).decode("utf-8")
    except Exception:
        return None


def get_redis_client() -> redis.Redis:
    return redis.from_url(RedisConfig.url)


def get_album_art_cache_key(url: str) -> str:
    url_hash = hashlib.md5(url.encode()).hexdigest()
    return f"album_art:{url_hash}"


def get_cached_album_art(redis_client: redis.Redis, url: str) -> str | None:
    """Get album art base64 from cache, extends TTL on hit."""
    key = get_album_art_cache_key(url)
    data = redis_client.getex(key, ex=ALBUM_ART_TTL)
    if data:
        return data.decode("utf-8")
    return None


def cache_album_art(redis_client: redis.Redis, url: str, b64: str) -> None:
    """Cache album art base64 with 7 day TTL."""
    key = get_album_art_cache_key(url)
    redis_client.set(key, b64, ex=ALBUM_ART_TTL)


def ensure_album_art_cached(redis_client: redis.Redis, album_art_url: str | None) -> None:
    """Pre-cache album art if not already cached.

    Called by polling job to proactively cache thumbnails.
    """
    if not album_art_url:
        return
    if get_cached_album_art(redis_client, album_art_url):
        return  # Already cached
    b64 = fetch_image_as_base64(album_art_url)
    if b64:
        cache_album_art(redis_client, album_art_url, b64)
