import hashlib
from datetime import datetime

from fastapi import APIRouter, HTTPException, status, Depends
import logging
from fastapi import APIRouter, Depends
from sqlalchemy import or_, and_
from sqlalchemy.orm import Session
from database import get_db
from dependencies import get_current_user, get_current_active_user, require_roles, get_optional_user
from typing import Optional
from models import User
from schemas import Result, Page, UserResponse, UserCreate, UserUpdateByAdmin, UserQueryByPage

router = APIRouter(prefix="/admin", tags=["管理员"])
logger = logging.getLogger(__name__)

@router.post("/add_user", summary="管理员添加用户")
async def add_user(user: UserCreate,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(require_roles("admin"))):
    try:
        # 检查电话唯一性
        phone = user.phone
        email = user.email
        user_phone_find = db.query(User).filter(User.phone == phone, User.status == 1).first()
        if user_phone_find:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="手机号已被其他用户使用"
            )
        # 检查邮箱唯一性
        if email is not None:
            user_email_find = db.query(User).filter(User.email == email, User.status == 1).first()
            if user_email_find:
                raise HTTPException(
                    status_code=status.HTTP_400_BAD_REQUEST,
                    detail="邮箱已被其他用户使用"
                )
        # 默认密码为123456
        hashed_password = hashlib.md5("123456".encode()).hexdigest()
        user_dict = user.dict(exclude={'password'})

        new_user = User(**user_dict,
                        password=hashed_password,
                        status=1,
                        created_time=datetime.now(),
                        last_login=None)
        db.add(new_user)
        db.commit()
        db.refresh(new_user)

        return Result.success()
    except HTTPException:
        # 重新抛出已知的HTTP异常
        raise

    except Exception as e:
        # 其他异常回滚
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"创建用户失败: {str(e)}"
        )

@router.patch("/update_user", summary="管理员更新用户信息")
async def update_user(new_user: UserUpdateByAdmin,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(require_roles("admin"))):
    """
    更新用户信息，包括实现软删除
    """
    try:
        user = db.query(User).filter(User.id == new_user.id).first()

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
            if value is not None and field != "id":
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


@router.get("/users", summary="管理员查询所有用户数据")
async def get_users(db: Session = Depends(get_db),
                    current_user: User = Depends(require_roles("admin"))):
    try:
        users = db.query(User).filter(User.status == 1).all()
        users_data = [UserResponse.from_orm(user) for user in users]
        return Result.success_with_data(users_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )

@router.get("/user/{id}", summary="管理员查询某个用户信息")
async def get_user_by_id(id, db: Session = Depends(get_db),
                         current_user: User = Depends(require_roles("admin"))):
    try:
        user = db.query(User).filter(User.status == 1, User.id == id).first()
        user_data = UserResponse.from_orm(user)
        return Result.success_with_data(user_data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: 用户不存在"
        )

@router.post("/users/page", summary="管理员分页查询用户信息")
async def get_user_page(page: Page, db: Session = Depends(get_db),
                        current_user: User = Depends(require_roles("admin"))):
    try:
        offset = (page.page - 1) * page.size
        total_count = db.query(User).filter(User.status == 1).count()
        users = db.query(User).filter(User.status == 1).offset(offset).limit(page.size).all()
        total_pages = (total_count + page.size - 1) // page.size
        users_data = [UserResponse.from_orm(user) for user in users]
        data = {
            "total_count": total_count,
            "total_pages": total_pages,
            "users": users_data
        }
        return Result.success_with_data(data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )


@router.post("/query", summary="查询用户信息")
async def query(query: UserQueryByPage,
                db: Session = Depends(get_db),
                current_user: User = Depends(require_roles("admin"))):
    try:
        offset = (query.page - 1) * query.size
        total_count = db.query(User).filter(
            and_(
                User.status == 1,
                or_(
                    User.username.like(f"%{query.data}%"),
                    User.phone.like(f"%{query.data}%"),
                    User.full_name.like(f"%{query.data}%"),
                    User.department.like(f"%{query.data}%"))
            )).count()
        users = db.query(User).filter(
            and_(
                User.status == 1,
                or_(
                    User.username.like(f"%{query.data}%"),
                    User.phone.like(f"%{query.data}%"),
                    User.full_name.like(f"%{query.data}%"),
                    User.department.like(f"%{query.data}%"))
            )).offset(offset).limit(query.size).all()
        total_pages = (total_count + query.size - 1) // query.size
        users_response = [UserResponse.from_orm(user) for user in users]

        data = {
            "total_count": total_count,
            "total_pages": total_pages,
            "users": users_response
        }

        return Result.success_with_data(data)
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"查询失败: {str(e)}"
        )