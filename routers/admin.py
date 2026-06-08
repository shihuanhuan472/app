import hashlib
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from database import get_db
from dependencies import require_roles
from models import User
from schemas import Page, Result, UserCreate, UserQueryByPage, UserResponse, UserUpdateByAdmin
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.pagination import build_pagination_payload
from utils.roles import (
    get_expected_perm_for_role,
    is_role_perm_consistent,
    normalize_perm_value,
    normalize_role_value,
)

"""
管理员相关操作，即对用户的增删改查。
"""

router = APIRouter(prefix="/admin", tags=["管理员"])
logger = logging.getLogger(__name__)


def _normalize_and_validate_role_perm(role_value, perm_value):
    normalized_role = normalize_role_value(role_value)
    normalized_perm = normalize_perm_value(perm_value)

    if normalized_role is None or normalized_perm is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色或权限参数非法")

    if not is_role_perm_consistent(normalized_role, normalized_perm):
        expected_perm = get_expected_perm_for_role(normalized_role)
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            BizCode.BAD_REQUEST,
            f"角色与权限不匹配：该角色仅允许权限值 {expected_perm}",
        )

    return normalized_role, normalized_perm


@router.post("/add_user", summary="管理员添加用户")
async def add_user(
    user: UserCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        phone = user.phone
        email = user.email
        username = user.username
        normalized_role, normalized_perm = _normalize_and_validate_role_perm(user.role, user.perm)

        phone_result = await db.execute(select(User).where(User.phone == phone, User.status == 1))
        user_phone_find = phone_result.scalar_one_or_none()
        if user_phone_find:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "手机号已被其他用户使用")

        if email is not None:
            email_result = await db.execute(select(User).where(User.email == email, User.status == 1))
            user_email_find = email_result.scalar_one_or_none()
            if user_email_find:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "邮箱已被其他用户使用")

        if username is not None:
            username_result = await db.execute(select(User).where(User.username == username, User.status == 1))
            user_username_find = username_result.scalar_one_or_none()
            if user_username_find:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "用户名已存在，添加失败")

        hashed_password = hashlib.md5("123456".encode()).hexdigest()

        deleted_user_result = await db.execute(select(User).where(User.username == username, User.status == 0))
        user_delete = deleted_user_result.scalar_one_or_none()

        if user_delete:
            user_delete.status = user.status if user.status is not None else 1
            user_delete.phone = phone
            user_delete.email = email
            user_delete.role = normalized_role
            user_delete.perm = normalized_perm
            user_delete.password = hashed_password
            user_delete.full_name = user.full_name
            user_delete.department = user.department
            user_delete.created_time = datetime.now()
            user_delete.last_login = None
            await db.commit()
            await db.refresh(user_delete)
        else:
            user_dict = user.model_dump(exclude={"password", "status"}, exclude_none=True)
            user_dict["role"] = normalized_role
            user_dict["perm"] = normalized_perm

            new_user = User(
                **user_dict,
                password=hashed_password,
                status=user.status if user.status is not None else 1,
                created_time=datetime.now(),
                last_login=None,
            )
            db.add(new_user)
            await db.commit()
            await db.refresh(new_user)

        return Result.success()
    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"创建用户失败: {str(e)}")


@router.patch("/update_user", summary="管理员更新用户信息")
async def update_user(
    new_user: UserUpdateByAdmin,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    """
    更新用户信息，包括软删除（status=0）。
    """
    try:
        result = await db.execute(select(User).where(User.id == new_user.id))
        user = result.scalar_one_or_none()

        if not user:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "用户不存在")

        new_user_dict = new_user.model_dump(exclude_unset=True)
        if not new_user_dict:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请提供需要更新的字段")

        should_validate_role_perm = ("role" in new_user_dict) or ("perm" in new_user_dict)
        if should_validate_role_perm:
            target_role = new_user_dict.get("role", user.role)
            target_perm = new_user_dict.get("perm", user.perm)
            normalized_role, normalized_perm = _normalize_and_validate_role_perm(target_role, target_perm)
            new_user_dict["role"] = normalized_role
            new_user_dict["perm"] = normalized_perm

        if "phone" in new_user_dict and new_user_dict["phone"] != user.phone:
            phone_result = await db.execute(
                select(User).where(
                    User.phone == new_user_dict["phone"],
                    User.id != user.id,
                    User.status == 1,
                )
            )
            exist_phone = phone_result.scalar_one_or_none()
            if exist_phone:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "手机号已被其他用户使用")

        if "email" in new_user_dict and new_user_dict["email"] and new_user_dict["email"] != user.email:
            email_result = await db.execute(
                select(User).where(
                    User.email == new_user_dict["email"],
                    User.id != user.id,
                    User.status == 1,
                )
            )
            exist_email = email_result.scalar_one_or_none()
            if exist_email:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "邮箱已被其他用户使用")

        for field, value in new_user_dict.items():
            if value is not None and field != "id":
                setattr(user, field, value)

        await db.commit()
        await db.refresh(user)

        data = UserResponse.from_orm(user)
        return Result.success_with_data(data)

    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        logger.error(f"用户更新异常: {str(e)}", exc_info=True)
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "服务器内部错误，请稍后重试")


@router.get("/users", summary="管理员查询所有用户数据")
async def get_users(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        result = await db.execute(select(User).where(User.status == 1))
        users = result.scalars().all()
        users_data = [UserResponse.from_orm(user) for user in users]
        return Result.success_with_data(users_data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.get("/user/{id}", summary="管理员查询某个用户信息")
async def get_user_by_id(
    id,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        result = await db.execute(select(User).where(User.status == 1, User.id == id))
        user = result.scalar_one_or_none()
        if not user:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "资源未找到")
        user_data = UserResponse.from_orm(user)
        return Result.success_with_data(user_data)
    except AppException:
        raise
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.post("/users/page", summary="管理员分页查询用户信息")
async def get_user_page(
    page: Page,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        offset = (page.page - 1) * page.size

        total_count_result = await db.execute(select(func.count()).select_from(User).where(User.status == 1))
        total_count = total_count_result.scalar_one()

        result = await db.execute(select(User).where(User.status == 1).offset(offset).limit(page.size))
        users = result.scalars().all()

        users_data = [UserResponse.from_orm(user) for user in users]
        data = build_pagination_payload(total_count, page.page, page.size, users_data, "users")
        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")


@router.post("/query", summary="查询用户信息")
async def query(
    query: UserQueryByPage,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    try:
        offset = (query.page - 1) * query.size

        filters = and_(
            User.status == 1,
            or_(
                User.username.like(f"%{query.data}%"),
                User.phone.like(f"%{query.data}%"),
                User.full_name.like(f"%{query.data}%"),
                User.department.like(f"%{query.data}%"),
            ),
        )

        total_count_result = await db.execute(select(func.count()).select_from(User).where(filters))
        total_count = total_count_result.scalar_one()

        result = await db.execute(select(User).where(filters).offset(offset).limit(query.size))
        users = result.scalars().all()

        users_response = [UserResponse.from_orm(user) for user in users]

        data = build_pagination_payload(total_count, query.page, query.size, users_response, "users")

        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")
