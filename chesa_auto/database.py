import motor.motor_asyncio
from datetime import datetime
from typing import Any, Optional

from .config import config
from .logger import get_logger

log = get_logger("DB")

_client: Optional[motor.motor_asyncio.AsyncIOMotorClient] = None
_db = None


def _connect() -> None:
    global _client, _db
    if _db is not None:
        return
    if not config.MONGO_URI:
        log.warning("MONGO_URI not set — DB features disabled.")
        return
    _client = motor.motor_asyncio.AsyncIOMotorClient(config.MONGO_URI)
    _db = _client[config.MONGO_DB]
    log.info("MongoDB connected → db=%s", config.MONGO_DB)


def db():
    if _db is None:
        _connect()
    return _db


async def add_user(user_id: int, first_name: str, username: str) -> None:
    coll = db()
    if coll is None:
        return
    users = coll["users"]
    await users.update_one(
        {"user_id": user_id},
        {
            "$set": {
                "first_name": first_name,
                "username": username,
                "last_seen": datetime.utcnow(),
            },
            "$setOnInsert": {"joined": datetime.utcnow()},
        },
        upsert=True,
    )


async def count_users() -> int:
    coll = db()
    if coll is None:
        return 0
    return await coll["users"].count_documents({})


async def list_user_ids() -> list[int]:
    coll = db()
    if coll is None:
        return []
    cur = coll["users"].find({}, {"user_id": 1})
    return [doc["user_id"] async for doc in cur]


async def record_game(user_id: int, result: str, moves: int) -> None:
    coll = db()
    if coll is None:
        return
    await coll["games"].insert_one(
        {
            "user_id": user_id,
            "result": result,
            "moves": moves,
            "played_at": datetime.utcnow(),
        }
    )


async def user_stats(user_id: int) -> dict[str, Any]:
    coll = db()
    if coll is None:
        return {"wins": 0, "losses": 0, "draws": 0, "total": 0}
    cur = coll["games"].aggregate(
        [
            {"$match": {"user_id": user_id}},
            {"$group": {"_id": "$result", "count": {"$sum": 1}}},
        ]
    )
    stats = {"wins": 0, "losses": 0, "draws": 0, "total": 0}
    async for doc in cur:
        key = doc["_id"]
        if key in stats:
            stats[key] = doc["count"]
    stats["total"] = stats["wins"] + stats["losses"] + stats["draws"]
    return stats


async def set_setting(key: str, value: Any) -> None:
    coll = db()
    if coll is None:
        return
    await coll["settings"].update_one(
        {"key": key}, {"$set": {"value": value}}, upsert=True
    )


async def get_setting(key: str, default: Any = None) -> Any:
    coll = db()
    if coll is None:
        return default
    doc = await coll["settings"].find_one({"key": key})
    return doc["value"] if doc else default
