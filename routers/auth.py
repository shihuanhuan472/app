import hashlib
from datetime import datetime
import logging
from fastapi import APIRouter, HTTPException, status, Depends, Body
from sqlalchemy.orm import Session
from database import get_db
from schemas import UserLogin, Result
from utils.JwtUtils import jwt_utils
from models import User

router = APIRouter(prefix="/auth", tags=["认证"])
logger = logging.getLogger(__name__)


@router.post("/login", summary="用户登录")
async def login(login_data: UserLogin, db: Session = Depends(get_db)):
    print("用户登录")
    # 验证用户名
    user = db.query(User).filter(User.username == login_data.username).first()
    if not user:
        return Result.error("用户名或密码错误")

    # 验证账户可用
    if user.status == 0:
        return Result.error("用户名或密码错误")

    # 验证密码
    hashed_password = hashlib.md5(login_data.password.encode()).hexdigest()
    if user.password != hashed_password:
        return Result.error("用户名或密码错误")

    if user.role == 1 and login_data.role == "admin":
        return Result.error("技术员工无法登录管理员")

    try:
        user.last_login = datetime.now()
        db.commit()
        db.refresh(user)
        print(f"{user.username}用户登录成功！")
    except Exception as e:
        db.rollback()
        return Result.error(f"更新登录时间失败：{e}")

    # 生成 JWT tokens
    payload = {
        "username": user.username,
        "phone": user.phone,
        "role": user.role,
        "user_id": user.id  # 添加用户ID
    }

    # 生成访问令牌
    access_token = jwt_utils.create_access_token(
        subject=user.id,
        payload=payload
    )

    # 生成刷新令牌
    refresh_token = jwt_utils.create_refresh_token(
        subject=user.id,
        payload=payload
    )

    # 返回两种token
    return Result.success_with_data({
        "access_token": access_token,
        "refresh_token": refresh_token,
        "token_type": "bearer",
        "expires_in": jwt_utils.config.ACCESS_TOKEN_EXPIRE_MINUTES * 60,  # 转换为秒
        "user": {
            "id": user.id,
            "username": user.username,
            "phone": user.phone,
            "role": user.role,
            "last_login": user.last_login
        }
    })


@router.post("/refresh", summary="刷新 Token")
async def refresh_token(
        token_data: dict = Body(..., description="刷新令牌请求体，包含refresh_token")
):

    refresh_token = token_data.get("refresh_token")
    # print("刷新token！！！")
    if not refresh_token:
        return Result.error("缺少refresh_token参数")

    try:
        # 使用JwtUtils刷新access token
        new_access_token = jwt_utils.refresh_access_token(refresh_token)

        return Result.success_with_data({
            "access_token": new_access_token,
            "token_type": "bearer",
            "expires_in": jwt_utils.config.ACCESS_TOKEN_EXPIRE_MINUTES * 60
        })
    except HTTPException as e:
        # 重新抛出JwtUtils抛出的HTTP异常
        raise e
    except Exception as e:
        # 捕获其他异常
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"刷新令牌失败: {str(e)}"
        )