import hashlib
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import status
import logging
from fastapi import APIRouter, Depends
from database import get_db
from dependencies import get_current_user, get_current_active_user, require_roles
from typing import Optional
from sqlalchemy import select
from sqlalchemy.orm import selectinload
from models import RoleGroup, User
from schemas import Result, UserUpdate, UserResponse, UserChangePassword
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.roles import get_user_permissions

router = APIRouter(prefix="/user", tags=["用户"])
logger = logging.getLogger(__name__)


def _serialize_profile(user: User) -> dict:
    role_group = getattr(user, "role_group", None)
    return {
        "id": user.id,
        "username": user.username,
        "phone": user.phone,
        "email": user.email,
        "full_name": user.full_name,
        "department": user.department,
        "api_key": user.api_key,
        "last_login": user.last_login,
        "created_time": user.created_time,
        "role": user.role,
        "perm": getattr(user, "perm", None),
        "role_group_id": getattr(user, "role_group_id", None),
        "role_group_name": getattr(role_group, "name", None),
        "permissions": sorted(get_user_permissions(user)),
    }


@router.get("/profile", summary="获取用户详细资料")
async def get_user_profile(current_user: User = Depends(get_current_active_user)):
    return Result.success_with_data(_serialize_profile(current_user))

@router.patch("/update", summary="更新用户信息")
async def update_user(new_user: UserUpdate,
                      current_user: User = Depends(get_current_active_user),
                      db: AsyncSession = Depends(get_db)):
    try:
        print(new_user)
        # user = db.query(User).filter(User.id == current_user.id).first()

        result = await db.execute(select(User).where(User.id == current_user.id))
        user = result.scalar_one_or_none()

        if not user:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.USER_NOT_FOUND, "用户不存在，更新用户信息失败")

        new_user = new_user.model_dump(exclude_unset=True)

        # 如果没有传入任何字段
        if not new_user:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请提供需要更新的字段")

        # 检查一下手机号唯一性
        if "phone" in new_user and new_user["phone"] != user.phone:
            # exist_phone = db.query(User).filter(User.phone == new_user["phone"],
            #                                     User.id != user.id,
            #                                     User.status == 1).first()

            phone_result = await db.execute(
                select(User).where(
                    User.phone == new_user["phone"],
                    User.id != user.id,
                    User.status == 1
                )
            )
            exist_phone = phone_result.scalar_one_or_none()

            if exist_phone:
                raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.USER_DUPLICATE_PHONE, "手机号已被占用")

        # 检查一下邮箱的唯一性
        if "email" in new_user and new_user["email"] and new_user["email"] != user.email:
            if new_user["email"]:
                # exist_email = db.query(User).filter(User.email == new_user["email"],
                #                                     User.id != user.id,
                #                                     User.status == 1).first()

                email_result = await db.execute(
                    select(User).where(
                        User.email == new_user["email"],
                        User.id != user.id,
                        User.status == 1
                    )
                )
                exist_email = email_result.scalar_one_or_none()

                if exist_email:
                    raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.USER_DUPLICATE_EMAIL, "邮箱已被占用")

        for field, value in new_user.items():
            if value is not None:
                setattr(user, field, value)

        await db.commit()

        refreshed_result = await db.execute(
            select(User)
            .options(selectinload(User.role_group).selectinload(RoleGroup.permissions))
            .where(User.id == user.id)
        )
        user = refreshed_result.scalar_one()

        return Result.success_with_data(_serialize_profile(user))
    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"用户更新失败：{str(e)}")

@router.put("/change_password", summary="修改密码")
async def change_password(password: UserChangePassword,
                          current_user: User = Depends(get_current_active_user),
                          db: AsyncSession = Depends(get_db)):
    try:
        # 检查一下旧密码
        old_password = password.old_password
        new_password = password.new_password
        hashed_old_password = hashlib.md5(old_password.encode()).hexdigest()
        # user = db.query(User).filter(User.id == current_user.id).first()

        result = await db.execute(select(User).where(User.id == current_user.id))
        user = result.scalar_one_or_none()

        if not user:
            raise AppException(status.HTTP_404_NOT_FOUND, BizCode.USER_NOT_FOUND, "用户不存在，更新密码失败")

        if hashed_old_password != user.password:
            raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "旧密码错误，更新密码失败")

        hashed_new_password = hashlib.md5(new_password.encode()).hexdigest()
        user.password = hashed_new_password
        await db.commit()
        await db.refresh(user)
        return Result.success()
    except AppException:
        raise
    except Exception as e:
        await db.rollback()
        raise AppException(status.HTTP_500_INTERNAL_SERVER_ERROR, BizCode.INTERNAL_ERROR, f"用户更新密码失败：{str(e)}")
