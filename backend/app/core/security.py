import hashlib
from datetime import datetime, timedelta
from typing import Optional
from uuid import uuid4
from jose import JWTError, jwt
from passlib.context import CryptContext
from app.core.config import settings

pwd_context = CryptContext(schemes=["bcrypt"], deprecated="auto")


def verify_password(plain_password: str, hashed_password: str) -> bool:
    return pwd_context.verify(plain_password, hashed_password)


def get_password_hash(password: str) -> str:
    return pwd_context.hash(password)


def _issue_token(data: dict, token_type: str, expires_delta: timedelta) -> str:
    """统一签发：携带 type（access/refresh 互斥）+ jti（供撤销台账关联）+ iat。"""
    to_encode = data.copy()
    to_encode.update({
        "type": token_type,
        "jti": uuid4().hex,
        "iat": datetime.utcnow(),
        "exp": datetime.utcnow() + expires_delta,
    })
    encoded_jwt = jwt.encode(to_encode, settings.SECRET_KEY, algorithm=settings.ALGORITHM)
    return encoded_jwt


def create_access_token(data: dict, expires_delta: Optional[timedelta] = None):
    delta = expires_delta or timedelta(minutes=settings.ACCESS_TOKEN_EXPIRE_MINUTES)
    return _issue_token(data, "access", delta)


def create_refresh_token(data: dict):
    return _issue_token(
        data, "refresh", timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS)
    )


def hash_token(token: str) -> str:
    """Refresh Token 只存 SHA-256：落库泄漏也无法回放明文 token。"""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def decode_token(token: str) -> Optional[dict]:
    try:
        payload = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
        return payload
    except JWTError:
        return None
