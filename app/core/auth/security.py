from datetime import datetime, timedelta, timezone
from functools import lru_cache
from typing import Any, Dict
from uuid import uuid4

from fastapi import HTTPException

from app.config import get_settings


@lru_cache(maxsize=1)
def _bcrypt_context():
    try:
        from passlib.context import CryptContext
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"passlib not installed: {exc}")
    return CryptContext(schemes=["bcrypt"], deprecated="auto")


@lru_cache(maxsize=1)
def _jwt_module():
    try:
        from jose import JWTError, jwt
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"python-jose not installed: {exc}")
    return JWTError, jwt


def hash_password(password: str) -> str:
    return _bcrypt_context().hash(password)


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return _bcrypt_context().verify(plain_password, hashed_password)


def decode_token(token: str) -> Dict[str, Any]:
    settings = get_settings()
    JWTError, jwt = _jwt_module()

    try:
        payload = jwt.decode(token, settings.JWT_SECRET, algorithms=[settings.JWT_ALGORITHM])
    except JWTError:
        raise HTTPException(status_code=401, detail="Invalid token")

    return payload


def create_access_token(payload: Dict[str, Any]) -> str:
    settings = get_settings()
    _JWTError, jwt = _jwt_module()

    expire = datetime.now(timezone.utc) + timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    to_encode = {**payload, "exp": expire, "type": "access"}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)


def create_refresh_token(payload: Dict[str, Any]) -> str:
    settings = get_settings()
    _JWTError, jwt = _jwt_module()

    expire = datetime.now(timezone.utc) + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    to_encode = {**payload, "exp": expire, "type": "refresh", "jti": str(uuid4())}
    return jwt.encode(to_encode, settings.JWT_SECRET, algorithm=settings.JWT_ALGORITHM)

