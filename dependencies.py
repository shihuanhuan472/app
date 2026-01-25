from fastapi import Depends, HTTPException, status
from fastapi.security import HTTPBearer, HTTPAuthorizationCredentials
from typing import Optional
from database import get_db
from sqlalchemy.orm import Session

from models import User
from utils.JwtUtils import jwt_utils

# 定义 HTTP Bearer 认证方案
security = HTTPBearer()


def get_current_user(
        credentials: HTTPAuthorizationCredentials = Depends(security),
        db: Session = Depends(get_db)
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
    token = credentials.credentials

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
        user = db.query(User).filter(User.id == user_id).first()
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


def get_current_active_user(
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

    def role_checker(current_user: User = Depends(get_current_user)) -> dict:
        if current_user.role != 0:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Insufficient permissions"
            )
        return current_user

    return role_checker


# 可选的：不强制要求认证的依赖
def get_optional_user(
        credentials: Optional[HTTPAuthorizationCredentials] = Depends(HTTPBearer(auto_error=False))
) -> Optional[dict]:
    """
    可选的用户认证依赖
    如果提供了 token 则验证，没提供则返回 None

    适用于某些接口可以匿名访问，但登录后有额外功能的场景
    """
    if credentials is None:
        return None

    try:
        token = credentials.credentials
        payload = jwt_utils.verify_token(token, token_type="access")
        return payload
    except:
        return None