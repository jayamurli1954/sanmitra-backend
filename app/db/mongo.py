from typing import Tuple

from app.config import get_settings

_client = None


def _mongo_client_kwargs() -> dict[str, int]:
    settings = get_settings()
    return {
        "serverSelectionTimeoutMS": settings.MONGO_SERVER_SELECTION_TIMEOUT_MS,
        "connectTimeoutMS": settings.MONGO_CONNECT_TIMEOUT_MS,
        "socketTimeoutMS": settings.MONGO_SOCKET_TIMEOUT_MS,
    }


async def init_mongo() -> None:
    global _client

    settings = get_settings()
    if not settings.MONGODB_URI:
        _client = None
        return

    if _client is None:
        try:
            from motor.motor_asyncio import AsyncIOMotorClient
        except Exception:
            _client = None
            return
        _client = AsyncIOMotorClient(settings.MONGODB_URI, **_mongo_client_kwargs())


async def close_mongo() -> None:
    global _client

    if _client is not None:
        _client.close()
    _client = None


def get_mongo_client():
    if _client is None:
        raise RuntimeError("MongoDB is not initialized")
    return _client


def get_mongo_database():
    settings = get_settings()
    return get_mongo_client()[settings.MONGO_DB_NAME]


def get_collection(name: str):
    return get_mongo_database()[name]


async def ping_mongo() -> Tuple[bool, str]:
    settings = get_settings()
    if not settings.MONGODB_URI:
        return False, "not_configured"

    try:
        if _client is None:
            try:
                from motor.motor_asyncio import AsyncIOMotorClient
            except Exception:
                return False, "driver_missing"
            temp_client = AsyncIOMotorClient(settings.MONGODB_URI, **_mongo_client_kwargs())
            try:
                await temp_client.admin.command("ping")
                return True, "ok"
            finally:
                temp_client.close()

        await _client.admin.command("ping")
        return True, "ok"
    except Exception as exc:
        return False, str(exc)
