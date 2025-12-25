"""
Migration 003: Round play timestamps to the minute.

This ensures deduplication works correctly between real-time polling and backfill.

Run: python migrations/003_round_play_timestamps.py
"""

import asyncio
import os

from dotenv import load_dotenv
from motor.motor_asyncio import AsyncIOMotorClient

load_dotenv()


async def migrate():
    client = AsyncIOMotorClient(os.getenv("MONGO_URI"))
    db = client.selfhoardify

    print("Migration 003: Round play timestamps to the minute")
    print("=" * 60)

    # Count total plays
    total = await db.plays.count_documents({})
    print(f"Total plays: {total}")

    # Aggregate to find plays that need rounding and check for duplicates
    pipeline = [
        {
            "$project": {
                "_id": 1,
                "track_id": 1,
                "listened_at": 1,
                "listened_at_rounded": {
                    "$dateTrunc": {"date": "$listened_at", "unit": "minute"}
                },
            }
        },
        {
            "$group": {
                "_id": {"track_id": "$track_id", "listened_at_rounded": "$listened_at_rounded"},
                "docs": {"$push": {"_id": "$_id", "listened_at": "$listened_at"}},
                "count": {"$sum": 1},
            }
        },
        {"$match": {"count": {"$gt": 1}}},
    ]

    # Check for potential duplicates after rounding
    duplicates = await db.plays.aggregate(pipeline).to_list(length=1000)

    if duplicates:
        print(f"\nWARNING: {len(duplicates)} groups would have duplicates after rounding")
        print("These will be deduplicated (keeping the earliest):\n")

        ids_to_delete = []
        for dup in duplicates[:10]:  # Show first 10
            track_id = dup["_id"]["track_id"]
            rounded = dup["_id"]["listened_at_rounded"]
            docs = sorted(dup["docs"], key=lambda x: x["listened_at"])
            print(f"  track_id: {track_id}")
            print(f"  rounded:  {rounded}")
            print(f"  keeping:  {docs[0]['listened_at']}")
            for doc in docs[1:]:
                print(f"  deleting: {doc['listened_at']}")
                ids_to_delete.append(doc["_id"])
            print()

        if len(duplicates) > 10:
            print(f"  ... and {len(duplicates) - 10} more groups")
            # Collect all IDs to delete
            for dup in duplicates[10:]:
                docs = sorted(dup["docs"], key=lambda x: x["listened_at"])
                for doc in docs[1:]:
                    ids_to_delete.append(doc["_id"])

        # Delete duplicates
        if ids_to_delete:
            result = await db.plays.delete_many({"_id": {"$in": ids_to_delete}})
            print(f"\nDeleted {result.deleted_count} duplicate plays")

    # Now update all timestamps to rounded values
    print("\nRounding all timestamps to the minute...")

    # Use aggregation with $merge to update in place
    update_pipeline = [
        {
            "$set": {
                "listened_at": {"$dateTrunc": {"date": "$listened_at", "unit": "minute"}}
            }
        },
        {"$merge": {"into": "plays", "whenMatched": "replace"}},
    ]

    await db.plays.aggregate(update_pipeline).to_list(length=1)

    # Verify
    sample = await db.plays.find_one()
    if sample:
        ts = sample["listened_at"]
        print(f"\nSample timestamp after migration: {ts}")
        print(f"Seconds: {ts.second}, Microseconds: {ts.microsecond}")

    print("\nMigration complete!")


if __name__ == "__main__":
    asyncio.run(migrate())
