import hashlib
from datetime import datetime
import logging
from fastapi import APIRouter, HTTPException, status, Depends
from sqlalchemy.orm import Session
from database import get_db
from schemas import UserLogin, Result
from utils.JwtUtils import jwt_utils
from models import User

router = APIRouter(prefix="/auth", tags=["认证"])
logger = logging.getLogger(__name__)

@router.post("/login", summary="用户登录")
async def login(login_data: UserLogin, db: Session = Depends(get_db)):
    """
    用户登录接口

    - username: 用户名
    - password: 密码

    返回 access_token 和用户信息
    """
    print("用户登录")
    # 验证用户名
    user = db.query(User).filter(User.username == login_data.username).first()
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误"
        )

    # 验证账户可用
    if user.status == 0:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="用户名或密码错误"
        )

    # 验证密码
    hashed_password = hashlib.md5(login_data.password.encode()).hexdigest()
    if user.password != hashed_password:
        print(hashed_password)
        raise HTTPException(status_code=401, detail="密码错误")

    if user.role == 1 and login_data.role == "admin":
        return Result.error("技术员工无法登录管理员")

    try:
        user.last_login = datetime.now()
        db.commit()
        db.refresh(user)
        print(f"{user.id}用户登录成功！")
    except Exception as e:
        db.rollback()
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail=f"更新登录时间失败: {str(e)}"
        )

    # 生成 JWT token
    payload = {
        "username": user.username,
        "phone": user.phone
    }

    access_token = jwt_utils.create_access_token(
        subject=user.id,
        payload=payload
    )
    print(f"{access_token}")
    return Result.success_with_data(access_token)


@router.post("/refresh", summary="刷新 Token")
async def refresh_token(refresh_token: str):
    """
    使用 refresh_token 获取新的 access_token

    注意：你的 JwtUtils 中的 create_refresh_token 方法引用了
    REFRESH_TOKEN_EXPIRE_DAYS，需要在 JWTConfig 中添加这个配置
    """
    try:
        new_access_token = jwt_utils.refresh_access_token(refresh_token)
        return Result.success_with_data(new_access_token)
    except HTTPException:
        raise
    except Exception as e:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Invalid refresh token: {str(e)}"
        )