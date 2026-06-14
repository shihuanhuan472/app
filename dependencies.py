import os
from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from database import get_db
from sqlalchemy.orm import Session
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy import select
from models import User
from utils.JwtUtils import jwt_utils
from utils.api_key import looks_like_api_key
from utils.roles import UserRole, normalize_role_value, is_role_perm_consistent

# 定义 HTTP Bearer 认证方案
security = HTTPBearer(auto_error=False)


def _get_configured_api_keys() -> set[str]:
    return {
        key.strip()
        for key in os.getenv("SYSTEM_API_KEYS", "").split(",")
        if key.strip()
    }


async def _get_api_key_user(db: AsyncSession) -> Optional[User]:
    user_id = os.getenv("SYSTEM_API_KEY_USER_ID", "1").strip()
    if not user_id.isdigit():
        return None

    result = await db.execute(select(User).where(User.id == int(user_id)))
    user = result.scalar_one_or_none()
    if user is None or user.status == 0:
        return None
    return user


async def _get_user_by_api_key(api_key: str, db: AsyncSession) -> Optional[User]:
    if not api_key:
        return None

    result = await db.execute(
        select(User).where(
            User.api_key == api_key,
            User.status == 1,
        )
    )
    return result.scalar_one_or_none()

"""
用来登录校验的依赖
"""

async def get_current_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(security),
        db: AsyncSession = Depends(get_db)
):
    """
    获取当前用户信息的依赖函数（拦截器）

    这个函数会自动从请求头中提取 Authorization: Bearer <token>
    然后验证 token 并返回用户信息

    使用方式：
    @app.get("/protected")
    async def protected_route(current_user: dict = Depends(get_current_user)):
        return {"user": current_user}
    """
    token = credentials.credentials if credentials else None
    api_key = token if looks_like_api_key(token) else None

    user = await _get_user_by_api_key(api_key, db)
    if user is not None:
        return user

    if token and token in _get_configured_api_keys():
        user = await _get_api_key_user(db)
        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid API key user",
                headers={"WWW-Authenticate": "Bearer"},
            )
        return user

    if not token:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Not authenticated",
            headers={"WWW-Authenticate": "Bearer"},
        )

    try:
        # 验证 token
        payload = jwt_utils.verify_token(token, token_type="access")
        # 从 payload 中提取用户信息
        user_id = payload.get("sub")
        if user_id is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Invalid authentication credentials",
                headers={"WWW-Authenticate": "Bearer"},
            )
        # user = db.query(User).filter(User.id == user_id).first()

        result = await db.execute(select(User).where(User.id == user_id))
        user = result.scalar_one_or_none()

        if user is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="User not found",
                headers={"WWW-Authenticate": "Bearer"},
            )

        if user.status == 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="账户被删除"
            )
        return user

    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )


async def get_current_active_user(
        current_user: dict = Depends(get_current_user)
):
    """
    获取当前激活用户的依赖函数

    可以在这里添加额外的用户状态检查，比如：
    - 检查用户是否被禁用
    - 检查用户权限
    - 从数据库加载完整用户信息等
    """
    # 这里可以添加额外的用户状态检查
    # 例如从数据库查询用户是否被禁用
    # if user_is_disabled(current_user):
    #     raise HTTPException(status_code=400, detail="Inactive user")

    return current_user


def require_roles(*allowed_roles: str):
    """
    角色权限检查装饰器

    使用方式：
    @app.get("/admin")
    async def admin_route(current_user: dict = Depends(require_roles("admin"))):
        return {"message": "Admin access"}
    """

    async def role_checker(current_user: User = Depends(get_current_user)) -> dict:
        allowed_values = set()
        for role in allowed_roles:
            role_value = normalize_role_value(role)
            if role_value is not None:
                allowed_values.add(role_value)

        # Backward compatibility: if caller passes nothing, default to admin only
        if not allowed_values:
            allowed_values = {int(UserRole.ADMIN)}

        if not is_role_perm_consistent(current_user.role, getattr(current_user, "perm", None)):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Role and permission do not match"
            )

        current_role = normalize_role_value(current_user.role)
        if current_role not in allowed_values:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user

    return role_checker
