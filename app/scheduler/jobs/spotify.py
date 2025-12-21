from app.database import MongoDBConnectionManager
from app.services.spotify import (
    get_auth_manager,
    get_spotify_client,
    get_redis_client,
    get_current_playback,
    get_recently_played,
    cache_now_playing,
    cache_now_playing_svg,
    NOW_PLAYING_SVG_CACHE_KEY,
)
from app.services.svg import generate_now_playing_svg
from app.services.plays import upsert_play, upsert_plays, sync_missing_artists, sync_missing_album
from app.utils.logger import logger


async def poll_current_playback():
    """Poll current playback every 30 seconds, save to DB and cache to Redis."""
    auth_manager = get_auth_manager()
    token_info = auth_manager.get_cached_token()
    if not token_info:
        return {"status": "skipped", "reason": "not authenticated"}

    sp = get_spotify_client()
    redis_client = get_redis_client()

    data = get_current_playback(sp)

    if not data:
        # Nothing playing - delete cache, let it expire to "Offline"
        cache_now_playing(redis_client, None)
        redis_client.delete(NOW_PLAYING_SVG_CACHE_KEY)
        return {"status": "ok", "playing": False}

    now_playing = data["now_playing"]

    # Calculate TTL: remaining time + 30 sec buffer
    remaining_ms = now_playing["duration_ms"] - now_playing["progress_ms"]
    ttl_seconds = max((remaining_ms // 1000) + 30, 60)  # At least 60 sec

    cache_now_playing(redis_client, now_playing, ttl_seconds)

    # Generate and cache SVG with same TTL
    svg = generate_now_playing_svg(
        title=now_playing["title"],
        artist=now_playing["artist"],
        album_art_url=now_playing["album_art"],
        is_playing=now_playing["is_playing"],
    )
    cache_now_playing_svg(redis_client, svg, ttl_seconds)

    async with MongoDBConnectionManager() as db:
        is_new = await upsert_play(db, data["play"])

        # Sync missing artists/album if new play
        if is_new:
            play = data["play"]
            await sync_missing_artists(db, sp, play.get("artist_ids", []))
            await sync_missing_album(db, sp, play.get("album_id"))

    return {"status": "ok", "playing": True, "inserted": is_new}


async def poll_recently_played():
    """Poll recently played every hour, save to DB with exact played_at."""
    auth_manager = get_auth_manager()
    token_info = auth_manager.get_cached_token()
    if not token_info:
        return {"status": "skipped", "reason": "not authenticated"}

    sp = get_spotify_client()
    plays = get_recently_played(sp, limit=50)

    if not plays:
        return {"status": "ok", "plays": 0}

    async with MongoDBConnectionManager() as db:
        result = await upsert_plays(db, plays)

    logger.info(
        f"poll_recently_played: {result['inserted']} inserted, "
        f"{result['updated']} updated"
    )
    return {"status": "ok", **result}
