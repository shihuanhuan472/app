import hashlib
import logging
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Body, Depends, status
from sqlalchemy import select, text, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal, get_db
from models import RoleGroup, User
from schemas import Result, UserLogin
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.JwtUtils import jwt_utils
from utils.roles import PermissionCode, get_user_permissions, has_permission

router = APIRouter(prefix="/auth", tags=["认证"])
logger = logging.getLogger(__name__)

ROLE_ADMIN = "admin"
ROLE_TECH_RW = "technician_rw"
ROLE_REVIEWER = "reviewer"
ROLE_MAINTENANCE = "maintenance_staff"
LAST_LOGIN_LOCK_WAIT_TIMEOUT = 2


def _normalize_role(selected_role: str):
    """Normalize frontend role value and keep compatibility aliases."""
    if not selected_role:
        return None

    role = selected_role.strip().lower()
    aliases = {
        "admin": ROLE_ADMIN,
        "technician_rw": ROLE_TECH_RW,
        "reviewer": ROLE_REVIEWER,
        "maintenance_staff": ROLE_MAINTENANCE,
        "readonly": ROLE_MAINTENANCE,
        "maintenance": ROLE_MAINTENANCE,
        "维修人员": ROLE_MAINTENANCE,
        "technician": ROLE_TECH_RW,
        "技术人员": ROLE_TECH_RW,
        "review": ROLE_REVIEWER,
        "审核人员": ROLE_REVIEWER,
        "管理员": ROLE_ADMIN,
        "0": ROLE_ADMIN,
        "1": ROLE_TECH_RW,
        "2": ROLE_REVIEWER,
        "3": ROLE_MAINTENANCE,
    }
    return aliases.get(role)


def _role_match(user: User, canonical_role: str) -> bool:
    """Keep login role selection compatible, but validate against role-group permissions."""
    permission_map = {
        ROLE_ADMIN: PermissionCode.ADMIN,
        ROLE_TECH_RW: PermissionCode.READ_WRITE,
        ROLE_REVIEWER: PermissionCode.REVIEW,
        ROLE_MAINTENANCE: PermissionCode.READ_ONLY,
    }
    expected_permission = permission_map.get(canonical_role)
    if expected_permission is None:
        return False
    return has_permission(user, expected_permission)


async def _update_last_login_best_effort(user_id: int, login_time: datetime):
    async with AsyncSessionLocal() as session:
        try:
            bind = session.get_bind()
            if bind.dialect.name == "mysql":
                await session.execute(
                    text("SET innodb_lock_wait_timeout = :timeout"),
                    {"timeout": LAST_LOGIN_LOCK_WAIT_TIMEOUT},
                )

            await session.execute(
                update(User)
                .where(User.id == user_id)
                .values(last_login=login_time)
            )
            await session.commit()
        except Exception:
            await session.rollback()
            logger.warning("更新用户最后登录时间失败，已忽略 user_id=%s", user_id, exc_info=True)


@router.post("/login", summary="用户登录")
async def login(
    login_data: UserLogin,
    background_tasks: BackgroundTasks,
    db: AsyncSession = Depends(get_db),
):
    result = await db.execute(
        select(User)
        .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
        .where(User.username == login_data.username)
    )
    user = result.scalar_one_or_none()

    if not user:
        raise AppException(status.HTTP_401_UNAUTHORIZED, BizCode.AUTH_INVALID_CREDENTIALS, "用户名或密码错误")

    if user.status == 0:
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.AUTH_ACCOUNT_DISABLED, "账户不可用")

    hashed_password = hashlib.md5(login_data.password.encode()).hexdigest()
    if user.password != hashed_password:
        raise AppException(status.HTTP_401_UNAUTHORIZED, BizCode.AUTH_INVALID_CREDENTIALS, "用户名或密码错误")

    canonical_role = _normalize_role(login_data.role)
    if login_data.role and not canonical_role:
        raise AppException(status.HTTP_401_UNAUTHORIZED, BizCode.AUTH_ROLE_INVALID, "登录身份非法")

    if canonical_role and not _role_match(user, canonical_role):
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.AUTH_ROLE_MISMATCH, "所选身份与账号角色/权限配置不匹配")

    login_time = datetime.now()
    permissions = sorted(get_user_permissions(user))
    role_group = getattr(user, "role_group", None)
    role_group_name = getattr(role_group, "name", None)
    payload = {
        "username": user.username,
        "phone": user.phone,
        "role": user.role,
        "perm": getattr(user, "perm", None),
        "role_group_id": getattr(user, "role_group_id", None),
        "role_group_name": role_group_name,
        "permissions": permissions,
        "login_role": canonical_role,
        "user_id": user.id,
    }

    access_token = jwt_utils.create_access_token(subject=user.id, payload=payload)
    refresh_token = jwt_utils.create_refresh_token(subject=user.id, payload=payload)
    background_tasks.add_task(_update_last_login_best_effort, user.id, login_time)

    return Result.success_with_data(
        {
            "access_token": access_token,
            "refresh_token": refresh_token,
            "token_type": "bearer",
            "expires_in": jwt_utils.config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            "user": {
                "id": user.id,
                "username": user.username,
                "phone": user.phone,
                "role": user.role,
                "perm": getattr(user, "perm", None),
                "role_group_id": getattr(user, "role_group_id", None),
                "role_group_name": role_group_name,
                "permissions": permissions,
                "last_login": login_time,
            },
        }
    )


@router.post("/refresh", summary="刷新 Token")
async def refresh_token(token_data: dict = Body(..., description="刷新令牌请求，包含 refresh_token")):
    refresh_token_value = token_data.get("refresh_token")
    if not refresh_token_value:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "缺少 refresh_token 参数")

    try:
        new_access_token = jwt_utils.refresh_access_token(refresh_token_value)
        return Result.success_with_data(
            {
                "access_token": new_access_token,
                "token_type": "bearer",
                "expires_in": jwt_utils.config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,
            }
        )
    except AppException:
        raise
    except Exception as e:
        raise AppException(status.HTTP_401_UNAUTHORIZED, BizCode.UNAUTHORIZED, f"刷新令牌失败: {str(e)}")
