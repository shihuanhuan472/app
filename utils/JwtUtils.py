from http.client import HTTPException

import jwt
import datetime
from datetime import datetime, timezone, timedelta
import os
from typing import Optional, Dict, Any
from dotenv import load_dotenv
from pydantic import BaseModel
from fastapi import HTTPException, status
# 加载环境变量
load_dotenv()


# JWT配置模型
class JWTConfig(BaseModel):
    SECRET_KEY: str = os.getenv("JWT_SECRET_KEY", "your-secret-key-change-in-production")
    ALGORITHM: str = os.getenv("JWT_ALGORITHM", "HS256")
    ACCESS_TOKEN_EXPIRE_MINUTES: int = int(os.getenv("ACCESS_TOKEN_EXPIRE_MINUTES", 120))
    REFRESH_TOKEN_EXPIRE_DAYS: int = int(os.getenv("REFRESH_TOKEN_EXPIRE_DAYS", 7))
    ISSUER: int = os.getenv("JWT_ISS", None)


class JwtUtils:
    def __init__(self, config: Optional[JWTConfig] = None):
        """
        初始化JWT工具类

        Args:
            config: JWT配置，如果为None则使用默认配置
        """
        self.config = config or JWTConfig()

        # 验证密钥
        if self.config.SECRET_KEY == "your-secret-key-change-in-production":
            import warnings
            warnings.warn(
                "Using default JWT secret key. Please set JWT_SECRET_KEY in environment variables for production!",
                UserWarning
            )

    def create_access_token(
            self,
            subject: str,
            payload: Optional[Dict[str, Any]] = None,
            expires_delta: Optional[timedelta] = None
    ) -> str:
        """
        创建访问令牌

        Args:
            subject: 令牌主题（通常是用户ID）
            payload: 额外的载荷数据
            expires_delta: 过期时间增量

        Returns:
            JWT token字符串
        """
        if payload is None:
            payload = {}

        # 设置过期时间
        if expires_delta:
            expire = datetime.utcnow() + expires_delta
        else:
            expire = datetime.utcnow() + timedelta(
                minutes=self.config.ACCESS_TOKEN_EXPIRE_MINUTES
            )

        # 构建标准声明
        to_encode = {
            "sub": str(subject),  # 主题
            "exp": expire,  # 过期时间
            "iat": datetime.utcnow(),  # 签发时间
            "type": "access"  # 令牌类型
        }

        # 添加额外载荷
        to_encode.update(payload)

        # 添加可选声明
        if self.config.ISSUER:
            to_encode["iss"] = self.config.ISSUER

        # 编码生成JWT
        encoded_jwt = jwt.encode(
            to_encode,
            self.config.SECRET_KEY,
            algorithm=self.config.ALGORITHM
        )

        return encoded_jwt

    def create_refresh_token(
            self,
            subject: str,
            payload: Optional[Dict[str, Any]] = None
    ) -> str:
        """
        创建刷新令牌

        Args:
            subject: 令牌主题
            payload: 额外的载荷数据

        Returns:
            JWT token字符串
        """
        if payload is None:
            payload = {}

        expire = datetime.utcnow() + timedelta(days=self.config.REFRESH_TOKEN_EXPIRE_DAYS)

        to_encode = {
            "sub": str(subject),
            "exp": expire,
            "iat": datetime.utcnow(),
            "type": "refresh"  # 标记为刷新令牌
        }

        to_encode.update(payload)

        if self.config.ISSUER:
            to_encode["iss"] = self.config.ISSUER

        return jwt.encode(
            to_encode,
            self.config.SECRET_KEY,
            algorithm=self.config.ALGORITHM
        )

    def verify_token(
            self,
            token: str,
            token_type: str = "access",
            leeway: int = 0
    ) -> Dict[str, Any]:
        """
        验证并解析JWT令牌

        Args:
            token: JWT token字符串
            token_type: 令牌类型（"access" 或 "refresh"）
            leeway: 过期时间宽容度（秒）

        Returns:
            解析后的载荷数据

        Raises:
            HTTPException: 令牌无效时抛出
        """
        credentials_exception = HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Could not validate credentials",
            headers={"WWW-Authenticate": "Bearer"},
        )

        try:
            # 解码JWT
            payload = jwt.decode(
                token,
                self.config.SECRET_KEY,
                algorithms=[self.config.ALGORITHM],
                options={
                    "verify_exp": True,
                    "verify_iss": bool(self.config.ISSUER),
                    "leeway": leeway
                },
                issuer=self.config.ISSUER if self.config.ISSUER else None
            )

            # 验证令牌类型
            if payload.get("type") != token_type:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail=f"Invalid token type. Expected {token_type}",
                    headers={"WWW-Authenticate": "Bearer"},
                )

            return payload

        except jwt.ExpiredSignatureError:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="Token has expired",
                headers={"WWW-Authenticate": "Bearer"},
            )
        except jwt.InvalidTokenError as e:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=f"Invalid token: {str(e)}",
                headers={"WWW-Authenticate": "Bearer"},
            )

    def get_subject(self, token: str) -> str:
        """
        从令牌中获取主题（用户ID）

        Args:
            token: JWT token字符串

        Returns:
            用户ID字符串
        """
        payload = self.verify_token(token)
        return payload.get("sub")

    def refresh_access_token(self, refresh_token: str) -> str:
        """
        使用刷新令牌获取新的访问令牌

        Args:
            refresh_token: 刷新令牌

        Returns:
            新的访问令牌
        """
        # 验证刷新令牌
        payload = self.verify_token(refresh_token, token_type="refresh")

        # 提取原始载荷（排除标准声明）
        original_payload = {k: v for k, v in payload.items()
                            if k not in ["exp", "iat", "type"]}

        # 创建新的访问令牌
        return self.create_access_token(
            subject=payload.get("sub"),
            payload=original_payload
        )

    def decode_token_without_verification(self, token: str) -> Dict[str, Any]:
        """
        解码令牌但不验证签名（仅用于调试或特定场景）

        Warning: 不要在正式验证中使用此方法

        Args:
            token: JWT token字符串

        Returns:
            解码后的载荷数据
        """
        import warnings
        warnings.warn(
            "This method does not verify the token signature. Use for debugging only.",
            UserWarning
        )
        return jwt.decode(token, options={"verify_signature": False})


# 创建全局实例（推荐方式）
jwt_utils = JwtUtils()

if __name__ == "__main__":
    user_id = "001"
    payload = {
        "name": "admin",
        "phone": "10086"
    }
    generate_jwt = jwt_utils.create_access_token(user_id, payload)
    print(generate_jwt)
    verify_jwt = jwt_utils.verify_token(generate_jwt)
    print(verify_jwt)