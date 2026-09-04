from datetime import datetime, timedelta
from uuid import uuid4
from fastapi import APIRouter, Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import delete, select, update

from app.core.database import get_db
from app.core.deps import get_current_active_user
from app.core.security import (
    verify_password,
    get_password_hash,
    create_access_token,
    create_refresh_token,
    decode_token,
    hash_token,
)
from app.models.user import User
from app.models.auth_session import AuthSession
from app.schemas.user import (
    UserCreate,
    UserResponse,
    Token,
    RefreshTokenRequest,
    LogoutRequest,
    MessageResponse,
)
from app.core.config import settings

router = APIRouter(prefix="/auth", tags=["authentication"])


def _build_token_response(user: User) -> tuple[str, str]:
    """签发一对 access + refresh token。"""
    access_token = create_access_token(data={"sub": user.username, "role": user.role})
    refresh_token = create_refresh_token(data={"sub": user.username, "role": user.role})
    return access_token, refresh_token


def _new_session(user_id, family_id, refresh_token) -> AuthSession:
    return AuthSession(
        user_id=user_id,
        family_id=family_id,
        token_hash=hash_token(refresh_token),
        expires_at=datetime.utcnow()
        + timedelta(days=settings.REFRESH_TOKEN_EXPIRE_DAYS),
    )


@router.post("/register", response_model=UserResponse, status_code=status.HTTP_201_CREATED)
async def register(user_data: UserCreate, db: AsyncSession = Depends(get_db)):
    """用户注册"""
    # 检查用户名是否已存在
    result = await db.execute(select(User).where(User.username == user_data.username))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Username already registered"
        )

    # 检查邮箱是否已存在
    result = await db.execute(select(User).where(User.email == user_data.email))
    if result.scalar_one_or_none():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Email already registered"
        )

    # 创建新用户
    hashed_password = get_password_hash(user_data.password)
    new_user = User(
        username=user_data.username,
        email=user_data.email,
        password_hash=hashed_password,
        role="user"
    )

    db.add(new_user)
    await db.flush()
    await db.refresh(new_user)

    return new_user


@router.post("/login", response_model=Token)
async def login(
    form_data: OAuth2PasswordRequestForm = Depends(),
    db: AsyncSession = Depends(get_db)
):
    """用户登录：返回 access（短效）+ refresh（长效，可撤销/轮换）。"""
    # 查找用户
    result = await db.execute(select(User).where(User.username == form_data.username))
    user = result.scalar_one_or_none()

    if not user or not verify_password(form_data.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Incorrect username or password",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled"
        )

    # 创建 token：access + refresh，refresh 写入可撤销台账
    access_token, refresh_token = _build_token_response(user)

    # 清理本人已过期的会话行，避免台账无限膨胀
    await db.execute(
        delete(AuthSession).where(
            AuthSession.user_id == user.id,
            AuthSession.expires_at < datetime.utcnow(),
        )
    )
    db.add(_new_session(user.id, uuid4(), refresh_token))

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@router.post("/refresh", response_model=Token)
async def refresh_token(
    body: RefreshTokenRequest,
    db: AsyncSession = Depends(get_db)
):
    """刷新 access token：校验 refresh token 后轮换出全新一对。

    - 轮换：旧 refresh 行标记 revoked_reason='rotated'，新行沿用同一 family
    - 重用检测：已吊销（如被窃取后轮换）的 refresh 再次出现 → 整族会话吊销
    """
    payload = decode_token(body.refresh_token)
    if payload is None or payload.get("type") != "refresh":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session_result = await db.execute(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(body.refresh_token)
        )
    )
    session = session_result.scalar_one_or_none()
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if session.revoked_at is not None:
        # 重用已吊销 token：整族吊销（该登录产生的所有会话作废）
        await db.execute(
            update(AuthSession)
            .where(
                AuthSession.family_id == session.family_id,
                AuthSession.revoked_at.is_(None),
            )
            .values(revoked_at=datetime.utcnow(), revoked_reason="reuse_detected")
        )
        await db.commit()  # 确保吊销落库后再返回 401
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has been revoked",
            headers={"WWW-Authenticate": "Bearer"},
        )

    if session.expires_at < datetime.utcnow():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Refresh token has expired",
            headers={"WWW-Authenticate": "Bearer"},
        )

    user_result = await db.execute(select(User).where(User.id == session.user_id))
    user = user_result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid refresh token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="User account is disabled",
        )

    # 轮换：新行沿用同 family，旧行标记 rotated 并记录去向
    access_token, refresh_token = _build_token_response(user)
    new_session = _new_session(user.id, session.family_id, refresh_token)
    db.add(new_session)
    await db.flush()
    session.revoked_at = datetime.utcnow()
    session.revoked_reason = "rotated"
    session.replaced_by = new_session.id

    return {
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer"
    }


@router.post("/logout", response_model=MessageResponse)
async def logout(
    body: LogoutRequest,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """注销当前设备：吊销指定 refresh token（幂等；非本人 token 不生效）。"""
    session_result = await db.execute(
        select(AuthSession).where(
            AuthSession.token_hash == hash_token(body.refresh_token)
        )
    )
    session = session_result.scalar_one_or_none()
    if (
        session is not None
        and session.user_id == current_user.id
        and session.revoked_at is None
    ):
        session.revoked_at = datetime.utcnow()
        session.revoked_reason = "logout"
    return MessageResponse(message="Logged out")


@router.post("/logout-all", response_model=MessageResponse)
async def logout_all(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(get_current_active_user),
):
    """注销所有设备：吊销当前用户的全部 refresh 会话。

    access token 最长存活 ACCESS_TOKEN_EXPIRE_MINUTES，属预期内的短窗口。
    """
    await db.execute(
        update(AuthSession)
        .where(
            AuthSession.user_id == current_user.id,
            AuthSession.revoked_at.is_(None),
        )
        .values(revoked_at=datetime.utcnow(), revoked_reason="logout_all")
    )
    return MessageResponse(message="All sessions logged out")


@router.get("/me", response_model=UserResponse)
async def get_current_user_info(
    current_user: User = Depends(get_current_active_user),
):
    """获取当前登录用户信息"""
    return current_user
