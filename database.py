import os
from motor.motor_asyncio import AsyncIOMotorClient
from pymongo import DeleteOne, UpdateOne

# Environment variable se MongoDB URI lena
MONGO_URI = os.getenv("MONGO_URI")

# Async MongoDB client setup
client = AsyncIOMotorClient(MONGO_URI)
db = client["LeaderboardBotDB"]

# Collections
settings_col = db["GuildSettings"]
activity_col = db["ActivityData"]
user_profiles_col = db["user_profiles"]
user_prefixes_col = db["UserPrefixes"]


def _default_user_profile(user_id: int) -> dict:
    return {
        "user_id": user_id,
        "points": 0,
        "wins": 0,
        "losses": 0,
        "total_games": 0,
    }


def _user_id_variants(user_id: int) -> list[int | str]:
    uid = int(user_id)
    return [uid, str(uid)]


def user_id_variants(user_id: int) -> list[int | str]:
    """Public helper for callers that need compatibility with legacy string user_id records."""
    return _user_id_variants(user_id)


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0

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
            "last_reset_time": None,
            "last_result_time": None,
            "pending_cycle_start": False,
        }
        await settings_col.insert_one(settings)
    return settings


async def get_server_prefix(guild_id: int) -> str | None:
    settings = await settings_col.find_one({"guild_id": int(guild_id)}, {"custom_prefix": 1})
    value = (settings or {}).get("custom_prefix")
    if isinstance(value, str) and value.strip():
        return value
    return None


async def set_server_prefix(guild_id: int, prefix: str) -> None:
    await settings_col.update_one(
        {"guild_id": int(guild_id)},
        {"$set": {"custom_prefix": prefix}},
        upsert=True,
    )


async def clear_server_prefix(guild_id: int) -> None:
    await settings_col.update_one({"guild_id": int(guild_id)}, {"$unset": {"custom_prefix": ""}})


async def get_user_prefix(user_id: int) -> str | None:
    doc = await user_prefixes_col.find_one({"user_id": int(user_id)}, {"prefix": 1})
    value = (doc or {}).get("prefix")
    if isinstance(value, str) and value.strip():
        return value
    return None


async def set_user_prefix(user_id: int, prefix: str) -> None:
    await user_prefixes_col.update_one(
        {"user_id": int(user_id)},
        {"$set": {"prefix": prefix}},
        upsert=True,
    )


async def clear_user_prefix(user_id: int) -> None:
    await user_prefixes_col.delete_one({"user_id": int(user_id)})


async def get_effective_prefixes(user_id: int, guild_id: int | None = None) -> list[str]:
    prefixes: list[str] = ["!"]
    user_prefix = await get_user_prefix(user_id)
    if user_prefix:
        prefixes.append(user_prefix)
    if guild_id is not None:
        server_prefix = await get_server_prefix(guild_id)
        if server_prefix:
            prefixes.append(server_prefix)
    seen = set()
    unique = []
    for prefix in prefixes:
        if prefix in seen:
            continue
        seen.add(prefix)
        unique.append(prefix)
    return unique

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
    uid = int(user_id)
    variants = _user_id_variants(uid)
    docs = await user_profiles_col.find({"user_id": {"$in": variants}}).to_list(length=None)

    if not docs:
        profile = _default_user_profile(uid)
        await user_profiles_col.insert_one(profile)
        return profile

    if len(docs) == 1:
        profile = docs[0]
        normalized = {
            "user_id": uid,
            "points": _safe_int(profile.get("points", 0)),
            "wins": _safe_int(profile.get("wins", 0)),
            "losses": _safe_int(profile.get("losses", 0)),
            "total_games": _safe_int(profile.get("total_games", 0)),
        }
        if profile.get("user_id") != uid or any(field not in profile for field in ("points", "wins", "losses", "total_games")):
            await user_profiles_col.update_one({"_id": profile["_id"]}, {"$set": normalized})
        return normalized

    merged = _default_user_profile(uid)
    merged["points"] = sum(_safe_int(doc.get("points", 0)) for doc in docs)
    merged["wins"] = sum(_safe_int(doc.get("wins", 0)) for doc in docs)
    merged["losses"] = sum(_safe_int(doc.get("losses", 0)) for doc in docs)
    merged["total_games"] = sum(_safe_int(doc.get("total_games", 0)) for doc in docs)

    canonical = next((doc for doc in docs if doc.get("user_id") == uid), docs[0])
    delete_ops = [DeleteOne({"_id": doc["_id"]}) for doc in docs if doc["_id"] != canonical["_id"]]

    await user_profiles_col.update_one({"_id": canonical["_id"]}, {"$set": merged})
    if delete_ops:
        await user_profiles_col.bulk_write(delete_ops)

    return merged


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

        uid = int(user_id)
        payload = {
            "$setOnInsert": _default_user_profile(uid),
            "$set": {"user_id": uid},
        }
        if inc_fields:
            payload["$inc"] = inc_fields

        operations.append(UpdateOne({"user_id": {"$in": user_id_variants(uid)}}, payload, upsert=True))

    if operations:
        await user_profiles_col.bulk_write(operations)


async def get_sorted_user_profiles(limit: int | None = None) -> list[dict]:
    """Points ke hisaab se global sorted profiles return karta hai."""
    docs = await user_profiles_col.find({}).to_list(length=None)
    merged: dict[int, dict] = {}
    for doc in docs:
        try:
            uid = int(doc.get("user_id"))
        except (TypeError, ValueError):
            continue
        current = merged.setdefault(uid, _default_user_profile(uid))
        current["points"] += _safe_int(doc.get("points", 0))
        current["wins"] += _safe_int(doc.get("wins", 0))
        current["losses"] += _safe_int(doc.get("losses", 0))
        current["total_games"] += _safe_int(doc.get("total_games", 0))

    profiles = sorted(merged.values(), key=lambda p: (-_safe_int(p.get("points", 0)), _safe_int(p.get("user_id", 0))))
    if limit is not None:
        return profiles[:limit]
    return profiles


async def get_user_global_rank(user_id: int) -> int:
    """Deterministic global rank return karta hai; ties me lower user_id ko higher rank milta hai."""
    uid = int(user_id)
    await get_user_profile(uid)
    profiles = await get_sorted_user_profiles()
    for idx, profile in enumerate(profiles, start=1):
        if _safe_int(profile.get("user_id")) == uid:
            return idx
    return len(profiles) + 1
