import os
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import ReturnDocument, UpdateOne

# Environment variable se MongoDB URI lena
MONGO_URI = os.getenv("MONGO_URI")

# Async MongoDB client setup
client = AsyncIOMotorClient(MONGO_URI)
db = client["LeaderboardBotDB"]

# Collections
settings_col = db["GuildSettings"]
activity_col = db["ActivityData"]
user_profiles_col = db["user_profiles"]


def _default_user_profile(user_id: int) -> dict:
    return {
        "user_id": user_id,
        "points": 0,
        "wins": 0,
        "losses": 0,
        "total_games": 0,
    }

async def get_guild_settings(guild_id: int):
    """Server ki settings fetch karta hai, nahi hone par default create karta hai."""
    settings = await settings_col.find_one({"guild_id": guild_id})
    if not settings:
        settings = {
            "guild_id": guild_id,
            "announcement_channel_id": None,
            "logs_channel_id": None,
            "reward_role_id": None,
            "interval_days": 7,  # Default weekly
            "top_count": 3,      # Default top 3
            "last_reset_time": None
        }
        await settings_col.insert_one(settings)
    return settings

async def update_guild_settings(guild_id: int, data: dict):
    """Server ki settings ko update karta hai (jaise naya channel ya role set karna)."""
    await settings_col.update_one(
        {"guild_id": guild_id},
        {"$set": data},
        upsert=True
    )

async def add_message(guild_id: int, user_id: int):
    """User ka message count +1 karta hai."""
    await activity_col.update_one(
        {"guild_id": guild_id, "user_id": user_id},
        {"$inc": {"message_count": 1}},
        upsert=True
    )

async def get_top_users(guild_id: int, limit: int = 3):
    """Leaderboard ke liye top N users nikalta hai."""
    cursor = activity_col.find({"guild_id": guild_id}).sort("message_count", -1).limit(limit)
    return await cursor.to_list(length=limit)

async def get_all_users(guild_id: int):
    """Logs banane ke liye server ke saare users ka data nikalta hai."""
    cursor = activity_col.find({"guild_id": guild_id}).sort("message_count", -1)
    return await cursor.to_list(length=None)

async def reset_activity(guild_id: int):
    """Sirf messages ka data delete karta hai (Soft Reset)."""
    await activity_col.delete_many({"guild_id": guild_id})

async def hard_reset_guild(guild_id: int):
    """Server ka pura data (Messages + Settings) nuke kar deta hai (Hard Reset)."""
    await activity_col.delete_many({"guild_id": guild_id})
    await settings_col.delete_many({"guild_id": guild_id})


async def bulk_update_activity(buffer_data: dict):
    """RAM buffer ko ek jhatke me MongoDB me push karta hai."""
    operations = []
    
    # buffer_data format: { guild_id: { user_id: count, user_id: count } }
    for guild_id, users in buffer_data.items():
        for user_id, count in users.items():
            operations.append(
                UpdateOne(
                    {"guild_id": guild_id, "user_id": user_id},
                    {"$inc": {"message_count": count}},
                    upsert=True
                )
            )
    
    # Agar operations list me data hai, tabhi DB call karo
    if operations:
        await activity_col.bulk_write(operations)


async def get_user_profile(user_id: int) -> dict:
    """Economy profile fetch karta hai, nahi hone par default create karta hai."""
    return await user_profiles_col.find_one_and_update(
        {"user_id": user_id},
        {"$setOnInsert": _default_user_profile(user_id)},
        upsert=True,
        return_document=ReturnDocument.AFTER,
    )


async def bulk_update_user_profiles(updates: list[dict]):
    """Ek ya zyada user economy profiles ko bulk me update karta hai."""
    operations = []

    for update in updates:
        user_id = update.get("user_id")
        if user_id is None:
            continue

        inc_fields = {
            field: int(update.get(field, 0))
            for field in ("points", "wins", "losses", "total_games")
            if int(update.get(field, 0)) != 0
        }

        payload = {"$setOnInsert": _default_user_profile(int(user_id))}
        if inc_fields:
            payload["$inc"] = inc_fields

        operations.append(UpdateOne({"user_id": int(user_id)}, payload, upsert=True))

    if operations:
        await user_profiles_col.bulk_write(operations)


async def get_sorted_user_profiles(limit: int | None = None) -> list[dict]:
    """Points ke hisaab se global sorted profiles return karta hai."""
    cursor = user_profiles_col.find({}).sort([("points", -1), ("user_id", 1)])
    if limit is not None:
        cursor = cursor.limit(limit)
    return await cursor.to_list(length=limit)


async def get_user_global_rank(user_id: int) -> int:
    """Deterministic global rank return karta hai; ties me lower user_id ko higher rank milta hai."""
    profile = await get_user_profile(user_id)
    higher_count = await user_profiles_col.count_documents(
        {
            "$or": [
                {"points": {"$gt": profile["points"]}},
                {"points": profile["points"], "user_id": {"$lt": user_id}},
            ]
        }
    )
    return higher_count + 1
