from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, MONGO_URI
import logging
from datetime import datetime, timezone
from telethon.errors import UserDeactivatedError, AuthKeyUnregisteredError, SessionRevokedError

logger = logging.getLogger(__name__)

_db = None

def get_db():
    global _db
    if _db is None:
        client = AsyncIOMotorClient(MONGO_URI)
        _db = client["reportbot"]
    return _db

async def save_session(user_id: int, phone: str, session_string: str):
    db = get_db()
    await db.sessions.update_one(
        {"phone": phone},
        {"$set": {"phone": phone, "session": session_string, "owner": user_id, "active": True}},
        upsert=True
    )

async def get_all_sessions() -> list[dict]:
    db = get_db()
    return await db.sessions.find({"active": True}).to_list(length=None)


async def get_session_by_phone(phone: str) -> dict | None:
    return await get_db().sessions.find_one({"phone": phone})


async def get_expired_sessions() -> list[dict]:
    return await get_db().sessions.find({"active": False}).sort("phone", 1).to_list(length=None)


async def check_session_authorized(session_string: str) -> bool:
    client = await build_client(session_string)
    try:
        await client.connect()
        return await client.is_user_authorized()
    finally:
        if client.is_connected():
            await client.disconnect()


async def refresh_all_sessions() -> dict:
    """Recheck stored sessions. Transient network failures never mark an account expired."""
    db = get_db()
    records = await db.sessions.find({}).to_list(length=None)
    result = {"total": len(records), "active": 0, "expired": 0, "errors": 0}
    revoked_errors = (UserDeactivatedError, AuthKeyUnregisteredError, SessionRevokedError)

    for record in records:
        phone = record.get("phone", "Unknown")
        try:
            client = await build_client(record["session"])
            try:
                await client.connect()
                authorized = await client.is_user_authorized()
            finally:
                if client.is_connected():
                    await client.disconnect()

            await db.sessions.update_one(
                {"_id": record["_id"]},
                {"$set": {
                    "active": bool(authorized),
                    "status_checked_at": datetime.now(timezone.utc),
                    "status_error": None,
                }},
            )
            result["active" if authorized else "expired"] += 1
        except revoked_errors as exc:
            await db.sessions.update_one(
                {"_id": record["_id"]},
                {"$set": {
                    "active": False,
                    "status_checked_at": datetime.now(timezone.utc),
                    "status_error": type(exc).__name__,
                }},
            )
            result["expired"] += 1
        except Exception as exc:
            logger.warning("Session status check failed for %s: %s", phone, exc)
            await db.sessions.update_one(
                {"_id": record["_id"]},
                {"$set": {
                    "status_checked_at": datetime.now(timezone.utc),
                    "status_error": type(exc).__name__,
                }},
            )
            result["errors"] += 1

    return result

async def get_user_sessions(user_id: int) -> list[dict]:
    db = get_db()
    return await db.sessions.find({"owner": user_id, "active": True}).to_list(length=None)

async def mark_expired(phone: str):
    db = get_db()
    await db.sessions.update_one(
        {"phone": phone},
        {"$set": {"active": False, "status_checked_at": datetime.now(timezone.utc)}},
    )

async def delete_session(phone: str, user_id: int) -> bool:
    db = get_db()
    result = await db.sessions.delete_one({"phone": phone, "owner": user_id})
    return result.deleted_count > 0

async def get_total_count() -> int:
    db = get_db()
    return await db.sessions.count_documents({})

async def get_active_count() -> int:
    db = get_db()
    return await db.sessions.count_documents({"active": True})

async def get_expired_count() -> int:
    db = get_db()
    return await db.sessions.count_documents({"active": False})

async def build_client(session_str: str, index: int = 0) -> TelegramClient:
    return TelegramClient(
        StringSession(session_str),
        API_ID,
        API_HASH,
        connection_retries=1,
        retry_delay=1,
        timeout=15,
    )
