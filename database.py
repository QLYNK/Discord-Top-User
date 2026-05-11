import os
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import UpdateOne

# Environment variable se MongoDB URI lena
MONGO_URI = os.getenv("MONGO_URI")

# Async MongoDB client setup
client = AsyncIOMotorClient(MONGO_URI)
db = client["LeaderboardBotDB"]

# Collections
settings_col = db["GuildSettings"]
activity_col = db["ActivityData"]

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