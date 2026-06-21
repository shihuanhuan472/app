import hashlib
import logging
from datetime import datetime

from fastapi import APIRouter, Depends, status
from sqlalchemy import and_, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import get_db
from dependencies import require_roles
from models import RoleGroup, RoleGroupPermission, User
from schemas import (
    Page,
    Result,
    RoleGroupCreate,
    RoleGroupUpdate,
    UserCreate,
    UserQueryByPage,
    UserResponse,
    UserUpdateByAdmin,
)
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.api_key import generate_api_key
from utils.pagination import build_pagination_payload
from utils.roles import (
    get_expected_perm_for_role,
    get_user_permissions,
    is_role_perm_consistent,
    legacy_role_perm_for_permissions,
    normalize_permission_codes,
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

def _serialize_role_group(role_group: RoleGroup) -> dict:
    return {
        "id": role_group.id,
        "code": role_group.code,
        "name": role_group.name,
        "description": role_group.description,
        "permissions": [
            permission.permission_code
            for permission in (role_group.permissions or [])
            if permission.permission_code
        ],
        "is_system": role_group.is_system,
        "is_deleted": role_group.is_deleted,
        "created_time": role_group.created_time,
        "updated_time": role_group.updated_time,
    }


def _serialize_user(user: User) -> dict:
    role_group = getattr(user, "role_group", None)
    return {
        "id": user.id,
        "username": user.username,
        "phone": user.phone,
        "email": user.email,
        "full_name": user.full_name,
        "role": user.role,
        "perm": getattr(user, "perm", None),
        "role_group_id": getattr(user, "role_group_id", None),
        "role_group_name": getattr(role_group, "name", None),
        "permissions": sorted(get_user_permissions(user)),
        "department": user.department,
        "api_key": getattr(user, "api_key", None),
        "created_time": user.created_time,
        "last_login": user.last_login,
    }


async def _get_role_group_or_400(db: AsyncSession, role_group_id: int) -> RoleGroup:
    result = await db.execute(
        select(RoleGroup)
        .options(selectinload(RoleGroup.permissions))
        .where(RoleGroup.id == role_group_id, RoleGroup.is_deleted == 0)
    )
    role_group = result.scalar_one_or_none()
    if role_group is None:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色组不存在或已停用")
    return role_group


async def _apply_role_group_to_user(db: AsyncSession, user: User, role_group_id: int):
    role_group = await _get_role_group_or_400(db, role_group_id)
    permissions = [permission.permission_code for permission in role_group.permissions]
    legacy_role, legacy_perm = legacy_role_perm_for_permissions(permissions)
    user.role_group_id = role_group.id
    user.role = legacy_role
    user.perm = legacy_perm


async def _generate_unique_api_key(db: AsyncSession) -> str:
    for _ in range(10):
        api_key = generate_api_key()
        result = await db.execute(select(User.id).where(User.api_key == api_key))
        if result.scalar_one_or_none() is None:
            return api_key
    raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, "API Key 生成失败")


@router.get("/role_groups", summary="管理员查询角色组")
async def get_role_groups(
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    result = await db.execute(
        select(RoleGroup)
        .options(selectinload(RoleGroup.permissions))
        .where(RoleGroup.is_deleted == 0)
        .order_by(RoleGroup.id.asc())
    )
    return Result.success_with_data([_serialize_role_group(role_group) for role_group in result.scalars().all()])


@router.post("/role_groups", summary="管理员创建角色组")
async def create_role_group(
    role_group_data: RoleGroupCreate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    permissions = normalize_permission_codes(role_group_data.permissions)
    if not permissions:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请至少选择一个权限")

    code = (role_group_data.code or role_group_data.name).strip()
    if not code:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色编码不能为空")

    exists_result = await db.execute(
        select(RoleGroup).where(
            RoleGroup.is_deleted == 0,
            or_(RoleGroup.code == code, RoleGroup.name == role_group_data.name),
        )
    )
    if exists_result.scalar_one_or_none():
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "角色组名称或编码已存在")

    role_group = RoleGroup(
        code=code,
        name=role_group_data.name.strip(),
        description=role_group_data.description,
        is_system=0,
        is_deleted=0,
        created_time=datetime.now(),
        updated_time=datetime.now(),
    )
    db.add(role_group)
    await db.flush()
    for permission in permissions:
        db.add(RoleGroupPermission(role_group_id=role_group.id, permission_code=permission))
    await db.commit()
    await db.refresh(role_group)

    role_group = await _get_role_group_or_400(db, role_group.id)
    return Result.success_with_data(_serialize_role_group(role_group))


@router.patch("/role_groups", summary="管理员更新角色组")
async def update_role_group(
    role_group_data: RoleGroupUpdate,
    db: AsyncSession = Depends(get_db),
    current_user: User = Depends(require_roles("admin")),
):
    role_group = await _get_role_group_or_400(db, role_group_data.id)

    if role_group_data.is_deleted == 1:
        if role_group.is_system:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "系统角色组不能删除")
        role_group.is_deleted = 1
        role_group.updated_time = datetime.now()
        await db.commit()
        return Result.success()

    if role_group_data.name is not None:
        role_group.name = role_group_data.name.strip()
    if role_group_data.code is not None:
        if role_group.is_system:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "系统角色组编码不能修改")
        role_group.code = role_group_data.code.strip()
    if role_group_data.description is not None:
        role_group.description = role_group_data.description

    if role_group_data.permissions is not None:
        permissions = normalize_permission_codes(role_group_data.permissions)
        if not permissions:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请至少选择一个权限")
        await db.execute(
            RoleGroupPermission.__table__.delete().where(RoleGroupPermission.role_group_id == role_group.id)
        )
        for permission in permissions:
            db.add(RoleGroupPermission(role_group_id=role_group.id, permission_code=permission))

        legacy_role, legacy_perm = legacy_role_perm_for_permissions(permissions)
        users_result = await db.execute(select(User).where(User.role_group_id == role_group.id))
        for user in users_result.scalars().all():
            user.role = legacy_role
            user.perm = legacy_perm

    role_group.updated_time = datetime.now()
    await db.commit()
    role_group = await _get_role_group_or_400(db, role_group.id)
    return Result.success_with_data(_serialize_role_group(role_group))


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
        role_group_id = user.role_group_id

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
            if role_group_id:
                await _apply_role_group_to_user(db, user_delete, role_group_id)
            else:
                user_delete.role = normalized_role
                user_delete.perm = normalized_perm
            user_delete.password = hashed_password
            user_delete.full_name = user.full_name
            user_delete.department = user.department
            if not user_delete.api_key:
                user_delete.api_key = await _generate_unique_api_key(db)
            user_delete.created_time = datetime.now()
            user_delete.last_login = None
            await db.commit()
            await db.refresh(user_delete)
        else:
            user_dict = user.model_dump(exclude={"password", "status", "role_group_id"}, exclude_none=True)
            user_dict["role"] = normalized_role
            user_dict["perm"] = normalized_perm

            new_user = User(
                **user_dict,
                password=hashed_password,
                api_key=await _generate_unique_api_key(db),
                status=user.status if user.status is not None else 1,
                created_time=datetime.now(),
                last_login=None,
            )
            if role_group_id:
                await _apply_role_group_to_user(db, new_user, role_group_id)
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

        role_group_id = new_user_dict.pop("role_group_id", None)
        if role_group_id:
            await _apply_role_group_to_user(db, user, int(role_group_id))

        should_validate_role_perm = role_group_id is None and (("role" in new_user_dict) or ("perm" in new_user_dict))
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

        refreshed_result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.id == user.id)
        )
        data = _serialize_user(refreshed_result.scalar_one())
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
        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.status == 1)
        )
        users = result.scalars().all()
        users_data = [_serialize_user(user) for user in users]
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
        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.status == 1, User.id == id)
        )
        user = result.scalar_one_or_none()
        if not user:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.NOT_FOUND, "资源未找到")
        user_data = _serialize_user(user)
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

        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.status == 1)
            .offset(offset)
            .limit(page.size)
        )
        users = result.scalars().all()

        users_data = [_serialize_user(user) for user in users]
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

        result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(filters)
            .offset(offset)
            .limit(query.size)
        )
        users = result.scalars().all()

        users_response = [_serialize_user(user) for user in users]

        data = build_pagination_payload(total_count, query.page, query.size, users_response, "users")

        return Result.success_with_data(data)
    except Exception as e:
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"查询失败: {str(e)}")
