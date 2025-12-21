from datetime import datetime, timezone

import spotipy
from motor.motor_asyncio import AsyncIOMotorDatabase
from pymongo import UpdateOne

from app.services.rate_limiter import spotify_rate_limiter
from app.utils.logger import logger


def parse_iso_datetime(value: str | datetime) -> datetime:
    """Parse ISO datetime string to datetime object."""
    if isinstance(value, datetime):
        return value
    # Handle 'Z' suffix (UTC)
    if value.endswith("Z"):
        value = value[:-1] + "+00:00"
    return datetime.fromisoformat(value)


async def upsert_play(db: AsyncIOMotorDatabase, play: dict) -> bool:
    """
    Upsert a single play to the database.
    Returns True if inserted, False if updated.
    """
    played_at = parse_iso_datetime(play.get("played_at"))

    played_at_rounded = play.get("played_at_rounded")
    if played_at_rounded is None and played_at:
        played_at_rounded = played_at.replace(second=0, microsecond=0)

    filter_doc = {
        "track_id": play["track_id"],
        "played_at_rounded": played_at_rounded,
    }

    update_doc = {
        "$set": {
            "name": play["name"],
            "artists": play["artists"],
            "artist_ids": play["artist_ids"],
            "album": play["album"],
            "album_id": play.get("album_id"),
            "album_art": play.get("album_art"),
            "duration_ms": play["duration_ms"],
            "explicit": play.get("explicit"),
            "popularity": play.get("popularity"),
            "disc_number": play.get("disc_number"),
            "track_number": play.get("track_number"),
            "isrc": play.get("isrc"),
            "played_at": played_at,
            "played_at_rounded": played_at_rounded,
        },
        "$setOnInsert": {
            "track_id": play["track_id"],
            "created_at": datetime.now(timezone.utc),
        },
    }

    if play.get("device_name") is not None:
        update_doc["$set"]["device_name"] = play["device_name"]
    if play.get("device_type") is not None:
        update_doc["$set"]["device_type"] = play["device_type"]
    if play.get("context_type") is not None:
        update_doc["$set"]["context_type"] = play["context_type"]
    if play.get("context_uri") is not None:
        update_doc["$set"]["context_uri"] = play["context_uri"]
    if play.get("shuffle_state") is not None:
        update_doc["$set"]["shuffle_state"] = play["shuffle_state"]

    result = await db.plays.update_one(filter_doc, update_doc, upsert=True)
    return result.upserted_id is not None


async def upsert_plays(db: AsyncIOMotorDatabase, plays: list[dict]) -> dict:
    """
    Bulk upsert plays to the database.
    Returns counts of inserted and updated.
    """
    if not plays:
        return {"inserted": 0, "updated": 0}

    operations = []
    for play in plays:
        played_at = parse_iso_datetime(play.get("played_at"))

        played_at_rounded = play.get("played_at_rounded")
        if played_at_rounded is None and played_at:
            played_at_rounded = played_at.replace(second=0, microsecond=0)

        filter_doc = {
            "track_id": play["track_id"],
            "played_at_rounded": played_at_rounded,
        }

        update_doc = {
            "$set": {
                "name": play["name"],
                "artists": play["artists"],
                "artist_ids": play["artist_ids"],
                "album": play["album"],
                "album_id": play.get("album_id"),
                "album_art": play.get("album_art"),
                "duration_ms": play["duration_ms"],
                "explicit": play.get("explicit"),
                "popularity": play.get("popularity"),
                "disc_number": play.get("disc_number"),
                "track_number": play.get("track_number"),
                "isrc": play.get("isrc"),
                "played_at": played_at,
                "played_at_rounded": played_at_rounded,
            },
            "$setOnInsert": {
                "track_id": play["track_id"],
                "created_at": datetime.now(timezone.utc),
            },
        }

        if play.get("device_name") is not None:
            update_doc["$set"]["device_name"] = play["device_name"]
        if play.get("device_type") is not None:
            update_doc["$set"]["device_type"] = play["device_type"]
        if play.get("context_type") is not None:
            update_doc["$set"]["context_type"] = play["context_type"]
        if play.get("context_uri") is not None:
            update_doc["$set"]["context_uri"] = play["context_uri"]
        if play.get("shuffle_state") is not None:
            update_doc["$set"]["shuffle_state"] = play["shuffle_state"]

        operations.append(UpdateOne(filter_doc, update_doc, upsert=True))

    result = await db.plays.bulk_write(operations)
    return {
        "inserted": result.upserted_count,
        "updated": result.modified_count,
    }


async def ensure_plays_indexes(db: AsyncIOMotorDatabase) -> None:
    """Create indexes for the plays and artists collections."""
    # Plays collection
    await db.plays.create_index(
        [("track_id", 1), ("played_at_rounded", 1)],
        unique=True,
        name="track_played_unique",
    )
    await db.plays.create_index("played_at", name="played_at_idx")
    await db.plays.create_index("artist_ids", name="artist_ids_idx")
    await db.plays.create_index("album_id", name="album_id_idx")

    # Artists collection
    await db.artists.create_index("artist_id", unique=True, name="artist_id_unique")
    await db.artists.create_index("genres", name="genres_idx")

    # Albums collection
    await db.albums.create_index("album_id", unique=True, name="album_id_unique")

    logger.info("Database indexes ensured")


async def sync_missing_artists(
    db: AsyncIOMotorDatabase, sp: spotipy.Spotify, artist_ids: list[str]
) -> int:
    """Fetch and store artists that don't exist in DB. Returns count synced."""
    if not artist_ids:
        return 0

    # Check which artists already exist
    existing = await db.artists.find(
        {"artist_id": {"$in": artist_ids}}, {"artist_id": 1}
    ).to_list(length=len(artist_ids))
    existing_ids = {doc["artist_id"] for doc in existing}

    missing_ids = [aid for aid in artist_ids if aid not in existing_ids]
    if not missing_ids:
        return 0

    # Fetch from Spotify
    artists_data = sp.artists(missing_ids)
    spotify_rate_limiter.record_requests(1)
    artists = artists_data.get("artists", [])

    docs = []
    for artist in artists:
        if artist:
            docs.append(
                {
                    "artist_id": artist["id"],
                    "name": artist["name"],
                    "genres": artist.get("genres", []),
                    "popularity": artist.get("popularity"),
                    "image": (
                        artist["images"][0]["url"] if artist.get("images") else None
                    ),
                }
            )

    if docs:
        await db.artists.insert_many(docs)
        logger.info(f"Synced {len(docs)} artists")

    return len(docs)


async def sync_missing_album(
    db: AsyncIOMotorDatabase, sp: spotipy.Spotify, album_id: str | None
) -> int:
    """Fetch and store album if it doesn't exist in DB. Returns 1 if synced, 0 otherwise."""
    if not album_id:
        return 0

    # Check if album exists
    existing = await db.albums.find_one({"album_id": album_id}, {"_id": 1})
    if existing:
        return 0

    # Fetch from Spotify
    album = sp.album(album_id)
    spotify_rate_limiter.record_requests(1)
    if not album:
        return 0

    doc = {
        "album_id": album["id"],
        "name": album["name"],
        "album_type": album.get("album_type"),
        "total_tracks": album.get("total_tracks"),
        "release_date": album.get("release_date"),
        "release_date_precision": album.get("release_date_precision"),
        "label": album.get("label"),
        "popularity": album.get("popularity"),
        "image": album["images"][0]["url"] if album.get("images") else None,
        "artist_ids": [a["id"] for a in album.get("artists", [])],
    }

    await db.albums.insert_one(doc)
    logger.info(f"Synced album: {album['name']}")

    return 1


async def sync_all_missing_metadata(
    db: AsyncIOMotorDatabase, sp: spotipy.Spotify
) -> dict:
    """
    Scan all plays and sync any missing artists/albums.
    Returns counts of synced artists and albums.
    """
    artists_synced = 0
    albums_synced = 0

    # Get all unique artist_ids from plays
    artist_ids_cursor = db.plays.aggregate([
        {"$unwind": "$artist_ids"},
        {"$group": {"_id": "$artist_ids"}},
    ])
    all_artist_ids = [doc["_id"] async for doc in artist_ids_cursor]

    # Check which exist in DB
    existing_artists = await db.artists.find(
        {"artist_id": {"$in": all_artist_ids}}, {"artist_id": 1}
    ).to_list(length=len(all_artist_ids))
    existing_artist_ids = {doc["artist_id"] for doc in existing_artists}

    missing_artist_ids = [aid for aid in all_artist_ids if aid not in existing_artist_ids]

    # Fetch missing artists in batches of 50
    for i in range(0, len(missing_artist_ids), 50):
        await spotify_rate_limiter.wait_if_needed()
        batch = missing_artist_ids[i : i + 50]
        artists_data = sp.artists(batch)
        spotify_rate_limiter.record_requests(1)
        artists = artists_data.get("artists", [])

        docs = []
        for artist in artists:
            if artist:
                docs.append(
                    {
                        "artist_id": artist["id"],
                        "name": artist["name"],
                        "genres": artist.get("genres", []),
                        "popularity": artist.get("popularity"),
                        "image": (
                            artist["images"][0]["url"] if artist.get("images") else None
                        ),
                    }
                )

        if docs:
            await db.artists.insert_many(docs)
            artists_synced += len(docs)

    # Get all unique album_ids from plays
    album_ids_cursor = db.plays.aggregate([
        {"$match": {"album_id": {"$ne": None}}},
        {"$group": {"_id": "$album_id"}},
    ])
    all_album_ids = [doc["_id"] async for doc in album_ids_cursor]

    # Check which exist in DB
    existing_albums = await db.albums.find(
        {"album_id": {"$in": all_album_ids}}, {"album_id": 1}
    ).to_list(length=len(all_album_ids))
    existing_album_ids = {doc["album_id"] for doc in existing_albums}

    missing_album_ids = [aid for aid in all_album_ids if aid not in existing_album_ids]

    # Fetch missing albums in batches of 20
    for i in range(0, len(missing_album_ids), 20):
        await spotify_rate_limiter.wait_if_needed()
        batch = missing_album_ids[i : i + 20]
        albums_data = sp.albums(batch)
        spotify_rate_limiter.record_requests(1)
        albums = albums_data.get("albums", [])

        docs = []
        for album in albums:
            if album:
                docs.append(
                    {
                        "album_id": album["id"],
                        "name": album["name"],
                        "album_type": album.get("album_type"),
                        "total_tracks": album.get("total_tracks"),
                        "release_date": album.get("release_date"),
                        "release_date_precision": album.get("release_date_precision"),
                        "label": album.get("label"),
                        "popularity": album.get("popularity"),
                        "image": (
                            album["images"][0]["url"] if album.get("images") else None
                        ),
                        "artist_ids": [a["id"] for a in album.get("artists", [])],
                    }
                )

        if docs:
            await db.albums.insert_many(docs)
            albums_synced += len(docs)

    if artists_synced or albums_synced:
        logger.info(
            f"sync_all_missing_metadata: {artists_synced} artists, {albums_synced} albums"
        )

    return {"artists_synced": artists_synced, "albums_synced": albums_synced}


async def backfill_plays(db: AsyncIOMotorDatabase, sp: spotipy.Spotify) -> dict:
    """
    Backfill existing plays with missing track data (album_id, isrc, etc.).
    Fetches track info from Spotify and updates plays.
    """
    # Find plays missing key fields
    missing_cursor = db.plays.aggregate([
        {
            "$match": {
                "$or": [
                    {"album_id": {"$exists": False}},
                    {"isrc": {"$exists": False}},
                ]
            }
        },
        {"$group": {"_id": "$track_id"}},
    ])
    missing_track_ids = [doc["_id"] async for doc in missing_cursor]

    if not missing_track_ids:
        return {"tracks_fetched": 0, "plays_updated": 0}

    tracks_fetched = 0
    plays_updated = 0
    track_data = {}

    # Fetch tracks in batches of 50
    for i in range(0, len(missing_track_ids), 50):
        await spotify_rate_limiter.wait_if_needed()
        batch = missing_track_ids[i : i + 50]
        tracks_response = sp.tracks(batch)
        spotify_rate_limiter.record_requests(1)
        tracks = tracks_response.get("tracks", [])

        for track in tracks:
            if track:
                track_data[track["id"]] = {
                    "album_id": track["album"]["id"],
                    "explicit": track.get("explicit"),
                    "popularity": track.get("popularity"),
                    "disc_number": track.get("disc_number"),
                    "track_number": track.get("track_number"),
                    "isrc": track.get("external_ids", {}).get("isrc"),
                }
                tracks_fetched += 1

    # Update plays with fetched data
    for track_id, data in track_data.items():
        result = await db.plays.update_many(
            {"track_id": track_id},
            {"$set": data},
        )
        plays_updated += result.modified_count

    logger.info(f"backfill_plays: {tracks_fetched} tracks, {plays_updated} plays updated")

    return {"tracks_fetched": tracks_fetched, "plays_updated": plays_updated}
