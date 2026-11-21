from motor.motor_asyncio import AsyncIOMotorClient
from config import MONGO_URI

_client = None

def _collection():
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(MONGO_URI)
    return _client["reportbot"]["access"]

async def remember_user(user_id: int, name: str, username: str | None):
    await _collection().update_one(
        {"user_id": int(user_id)},
        {"$set": {"user_id": int(user_id), "name": name or "Unknown", "username": username}},
        upsert=True,
    )

async def is_sudo(user_id: int) -> bool:
    return await _collection().count_documents(
        {"user_id": int(user_id), "role": "sudo"}, limit=1
    ) > 0

async def add_sudo(user_id: int, name: str, username: str | None):
    await _collection().update_one(
        {"user_id": int(user_id)},
        {"$set": {
            "user_id": int(user_id), "name": name or "Unknown",
            "username": username, "role": "sudo"
        }},
        upsert=True,
    )

async def get_sudo_users() -> list[dict]:
    return await _collection().find(
        {"role": "sudo"}, {"_id": 0, "user_id": 1, "name": 1, "username": 1}
    ).sort("user_id", 1).to_list(length=None)

async def remove_sudo(user_id: int) -> bool:
    result = await _collection().delete_one(
        {"user_id": int(user_id), "role": "sudo"}
    )
    return result.deleted_count > 0
