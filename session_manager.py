from motor.motor_asyncio import AsyncIOMotorClient
from telethon import TelegramClient
from telethon.sessions import StringSession
from config import API_ID, API_HASH, MONGO_URI
import logging

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

async def get_user_sessions(user_id: int) -> list[dict]:
    db = get_db()
    return await db.sessions.find({"owner": user_id, "active": True}).to_list(length=None)

async def mark_expired(phone: str):
    db = get_db()
    await db.sessions.update_one({"phone": phone}, {"$set": {"active": False}})

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
