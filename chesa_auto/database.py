"""
MongoDB layer.

Uses PyMongo's native async client (``pymongo.AsyncMongoClient``).
Motor is deprecated and older Motor releases crash on import with newer
PyMongo (``ImportError: cannot import name '_QUERY_OPTIONS'``), so it is no
longer used anywhere in this project.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Optional

from pymongo import AsyncMongoClient
from pymongo.asynchronous.database import AsyncDatabase

from .config import config
from .logger import get_logger

log = get_logger("DB")

_client: Optional[AsyncMongoClient] = None
_db: Optional[AsyncDatabase] = None
_failed = False


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _connect() -> None:
    global _client, _db, _failed
    if _db is not None or _failed:
        return
    if not config.MONGO_URI:
        log.warning("MONGO_URI not set — DB features disabled.")
        _failed = True
        return
    try:
        _client = AsyncMongoClient(
            config.MONGO_URI,
            serverSelectionTimeoutMS=10_000,
            tz_aware=True,
        )
        _db = _client[config.MONGO_DB]
        log.info("MongoDB client ready → db=%s", config.MONGO_DB)
    except Exception as e:  # bad URI etc. — keep the bot running without DB
        log.error("MongoDB init failed: %s", e)
        _failed = True


def db() -> Optional[AsyncDatabase]:
    if _db is None:
        _connect()
    return _db


async def ping() -> bool:
    d = db()
    if d is None:
        return False
    try:
        await d.command("ping")
        return True
    except Exception as e:
        log.error("MongoDB ping failed: %s", e)
        return False


async def close() -> None:
    global _client, _db
    if _client is not None:
        try:
            await _client.close()
        except Exception:
            pass
    _client = None
    _db = None


async def add_user(user_id: int, first_name: str, username: str) -> None:
    d = db()
    if d is None:
        return
    try:
        await d["users"].update_one(
            {"user_id": user_id},
            {
                "$set": {
                    "first_name": first_name or "",
                    "username": username or "",
                    "last_seen": _now(),
                },
                "$setOnInsert": {"joined": _now()},
            },
            upsert=True,
        )
    except Exception as e:
        log.warning("add_user failed: %s", e)


async def count_users() -> int:
    d = db()
    if d is None:
        return 0
    try:
        return await d["users"].count_documents({})
    except Exception as e:
        log.warning("count_users failed: %s", e)
        return 0


async def list_user_ids() -> list[int]:
    d = db()
    if d is None:
        return []
    try:
        cur = d["users"].find({}, {"user_id": 1, "_id": 0})
        return [doc["user_id"] async for doc in cur if "user_id" in doc]
    except Exception as e:
        log.warning("list_user_ids failed: %s", e)
        return []


async def remove_user(user_id: int) -> None:
    d = db()
    if d is None:
        return
    try:
        await d["users"].delete_one({"user_id": user_id})
    except Exception as e:
        log.warning("remove_user failed: %s", e)


async def record_game(user_id: int, result: str, moves: int) -> None:
    d = db()
    if d is None:
        return
    try:
        await d["games"].insert_one(
            {"user_id": user_id, "result": result, "moves": moves, "played_at": _now()}
        )
    except Exception as e:
        log.warning("record_game failed: %s", e)


async def user_stats(user_id: int) -> dict[str, Any]:
    stats = {"wins": 0, "losses": 0, "draws": 0, "total": 0}
    d = db()
    if d is None:
        return stats
    # Stored results are "win" / "loss" / "draw"; map them to the plural keys.
    key_map = {"win": "wins", "loss": "losses", "draw": "draws"}
    try:
        cur = await d["games"].aggregate(
            [
                {"$match": {"user_id": user_id}},
                {"$group": {"_id": "$result", "count": {"$sum": 1}}},
            ]
        )
        async for doc in cur:
            key = key_map.get(doc["_id"])
            if key:
                stats[key] = doc["count"]
    except Exception as e:
        log.warning("user_stats failed: %s", e)
    stats["total"] = stats["wins"] + stats["losses"] + stats["draws"]
    return stats


async def set_setting(key: str, value: Any) -> None:
    d = db()
    if d is None:
        return
    try:
        await d["settings"].update_one({"key": key}, {"$set": {"value": value}}, upsert=True)
    except Exception as e:
        log.warning("set_setting(%s) failed: %s", key, e)


async def get_setting(key: str, default: Any = None) -> Any:
    d = db()
    if d is None:
        return default
    try:
        doc = await d["settings"].find_one({"key": key})
    except Exception as e:
        log.warning("get_setting(%s) failed: %s", key, e)
        return default
    return doc["value"] if doc else default


async def delete_setting(key: str) -> None:
    d = db()
    if d is None:
        return
    try:
        await d["settings"].delete_one({"key": key})
    except Exception as e:
        log.warning("delete_setting(%s) failed: %s", key, e)
