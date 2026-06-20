"""User persistence helpers + first-boot superadmin bootstrap."""
from __future__ import annotations

import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.security import hash_password
from app.models.enums import UserRole
from app.models.user import User


async def get_by_email(db: AsyncSession, email: str) -> User | None:
    result = await db.execute(select(User).where(User.email == email.lower()))
    return result.scalar_one_or_none()


async def get_by_id(db: AsyncSession, user_id: uuid.UUID) -> User | None:
    return await db.get(User, user_id)


async def create_user(
    db: AsyncSession,
    *,
    email: str,
    password: str,
    role: UserRole = UserRole.USER,
    commit: bool = True,
) -> User:
    user = User(
        email=email.lower(),
        password_hash=hash_password(password),
        role=role,
    )
    db.add(user)
    if commit:
        await db.commit()
        await db.refresh(user)
    else:
        await db.flush()
    return user


async def bootstrap_superadmin(db: AsyncSession) -> User | None:
    """Create the initial superadmin if the users table is empty.

    Idempotent and safe to call on every startup. Returns the created user, or
    None if users already exist.
    """
    count = await db.scalar(select(func.count()).select_from(User))
    if count:
        return None
    return await create_user(
        db,
        email=settings.ADMIN_EMAIL,
        password=settings.ADMIN_PASSWORD,
        role=UserRole.SUPERADMIN,
    )
