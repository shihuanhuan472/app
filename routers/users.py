import hashlib

from fastapi import APIRouter, HTTPException, status, Depends
import logging
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user, get_current_active_user, require_roles, get_optional_user
from typing import Optional
from models import User
from schemas import Result, UserUpdate, UserResponse, UserChangePassword

router = APIRouter(prefix="/user", tags=["用户"])
logger = logging.getLogger(__name__)

@router.get("/me", summary="获取当前用户信息")
async def get_user_info(current_user: dict = Depends(get_current_user)):
    """
    获取当前登录用户的信息

    需要在请求头中携带：Authorization: Bearer <token>
    """
    return Result.success_with_data(current_user)


@router.get("/profile", summary="获取用户详细资料")
async def get_user_profile(current_user: User = Depends(get_current_active_user)):
    """
    获取用户详细资料（使用 get_current_active_user 进行额外检查）
    """
    data = {
        "id": current_user.id,
        "username": current_user.username,
        "phone": current_user.phone,
        "email": current_user.email,
        "full_name": current_user.full_name,
        "department": current_user.department,
        "last_login": current_user.last_login,
        "created_time": current_user.created_time,
        "role": current_user.role
    }
    return Result.success_with_data(data)

@router.patch("/update", summary="更新用户信息")
async def update_user(new_user: UserUpdate,
                      current_user: User = Depends(get_current_active_user),
                      db: Session = Depends(get_db)):
    """
    更新当前登录用户的信息

    - 可以更新手机号、邮箱、姓名、部门
    - 手机号和邮箱需要验证唯一性
    """
    try:
        user = db.query(User).filter(User.id == current_user.id).first()
        if not user:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="用户不存在"
            )

        new_user = new_user.model_dump(exclude_unset=True)

        # 如果没有传入任何字段
        if not new_user:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="请提供需要更新的字段"
            )
        print(f"用户 {user.username} 要更新的字段: {new_user.keys()}")
        # 检查一下手机号唯一性
        if "phone" in new_user and new_user["phone"] != user.phone:
            exist_phone = db.query(User).filter(User.phone == new_user["phone"],
                                                User.id != user.id,
                                                User.status == 1).first()
            if exist_phone:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="手机号已被其他用户使用"
                )

        # 检查一下邮箱的唯一性
        if "email" in new_user and new_user["email"] and new_user["email"] != user.email:
            if new_user["email"]:
                exist_email = db.query(User).filter(User.email == new_user["email"],
                                                    User.id != user.id,
                                                    User.status == 1).first()
                if exist_email:
                    raise HTTPException(
                        status_code=status.HTTP_400_BAD_REQUEST,
                        detail="邮箱已被其他用户使用"
                    )

        for field, value in new_user.items():
            if value is not None:
                setattr(user, field, value)

        db.commit()
        db.refresh(user)

        data = UserResponse.from_orm(user)

        return Result.success_with_data(data)
    except HTTPException:
        raise
    except Exception as e:
        db.rollback()
        logger.error(f"用户更新异常: {str(e)}", exc_info=True)
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="服务器内部错误，请稍后重试"
        )

@router.put("/change_password", summary="修改密码")
async def change_password(password: UserChangePassword,
                          current_user: User = Depends(get_current_active_user),
                          db: Session = Depends(get_db)):
    """
    用户修改密码，确认密码由前端完成
    后端实现旧密码校验和新密码更新
    """
    # 检查一下旧密码
    old_password = password.old_password
    new_password = password.new_password
    hashed_old_password = hashlib.md5(old_password.encode()).hexdigest()
    user = db.query(User).filter(User.id == current_user.id).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="用户不存在"
        )
    if hashed_old_password != user.password:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="旧密码错误"
        )
    hashed_new_password = hashlib.md5(new_password.encode()).hexdigest()
    user.password = hashed_new_password
    db.commit()
    db.refresh(user)
    return Result.success()

@router.get("/admin-only", summary="仅管理员可访问")
async def admin_only_route(current_user: dict = Depends(require_roles("admin"))):
    """
    仅管理员可以访问的接口

    会检查用户的 role 字段是否为 "admin"
    """
    return {
        "code": 200,
        "message": "欢迎，管理员！",
        "data": current_user
    }


@router.get("/public", summary="公开接口（可选认证）")
async def public_route(current_user: Optional[dict] = Depends(get_optional_user)):
    """
    公开接口，可以匿名访问

    如果提供了 token，会返回用户信息；否则返回匿名提示
    """
    if current_user:
        return {
            "code": 200,
            "message": f"欢迎，{current_user.get('username')}！",
            "is_authenticated": True,
            "user": current_user
        }
    else:
        return {
            "code": 200,
            "message": "欢迎，游客！",
            "is_authenticated": False
        }