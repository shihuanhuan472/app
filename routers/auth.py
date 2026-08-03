import hashlib
import logging
import re
from datetime import datetime

from fastapi import APIRouter, BackgroundTasks, Body, Depends, status
from sqlalchemy import or_, select, text, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from database import AsyncSessionLocal, get_db
from models import RoleGroup, User
from schemas import Result, UserLogin, UserRegister
from utils.app_exceptions import AppException
from utils.error_codes import BizCode
from utils.JwtUtils import jwt_utils
from utils.roles import get_user_permissions

router = APIRouter(prefix="/auth", tags=["认证"])
logger = logging.getLogger(__name__)

LAST_LOGIN_LOCK_WAIT_TIMEOUT = 2
REGISTRATION_PENDING = "pending"
REGISTRATION_APPROVED = "approved"
REGISTRATION_REJECTED = "rejected"
PHONE_PATTERN = re.compile(r"^1[3-9]\d{9}$")


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


@router.post("/register", summary="用户注册")
async def register(
    registration: UserRegister,
    db: AsyncSession = Depends(get_db),
):
    username = registration.username.strip()
    password = registration.password
    phone = registration.phone.strip()
    email = (registration.email or "").strip() or None
    full_name = registration.full_name.strip()
    department = (registration.department or "").strip() or None

    if not username or not phone or not full_name:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "请完整填写必填信息")
    if len(username) < 3:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "用户名至少需要 3 个字符")
    if not PHONE_PATTERN.fullmatch(phone):
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "手机号格式不正确")
    if password != registration.confirm_password:
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "两次输入的密码不一致")
    if email and ("@" not in email or " " in email):
        raise AppException(status.HTTP_400_BAD_REQUEST, BizCode.BAD_REQUEST, "邮箱格式不正确")

    duplicate_conditions = [User.username == username, User.phone == phone]
    if email:
        duplicate_conditions.append(User.email == email)
    duplicate_result = await db.execute(select(User).where(or_(*duplicate_conditions)))
    duplicate_user = duplicate_result.scalars().first()
    if duplicate_user:
        if duplicate_user.username == username:
            message = "用户名已存在"
            biz_code = BizCode.USER_DUPLICATE_USERNAME
        elif duplicate_user.phone == phone:
            message = "手机号已被使用"
            biz_code = BizCode.USER_DUPLICATE_PHONE
        else:
            message = "邮箱已被使用"
            biz_code = BizCode.USER_DUPLICATE_EMAIL
        raise AppException(status.HTTP_400_BAD_REQUEST, biz_code, message)

    new_user = User(
        username=username,
        password=hashlib.md5(password.encode()).hexdigest(),
        phone=phone,
        email=email,
        full_name=full_name,
        department=department,
        status=0,
        registration_status=REGISTRATION_PENDING,
        role=3,
        perm=3,
        role_group_id=None,
        api_key=None,
        created_time=datetime.now(),
        last_login=None,
    )
    db.add(new_user)
    try:
        await db.commit()
        await db.refresh(new_user)
    except IntegrityError:
        await db.rollback()
        raise AppException(
            status.HTTP_400_BAD_REQUEST,
            BizCode.BAD_REQUEST,
            "注册信息已存在，请检查用户名、手机号或邮箱",
        )

    return Result(
        code=1,
        msg="注册申请已提交，请等待管理员审核",
        data={
            "user_id": new_user.id,
            "registration_status": new_user.registration_status,
        },
    )


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

    hashed_password = hashlib.md5(login_data.password.encode()).hexdigest()
    if user.password != hashed_password:
        raise AppException(status.HTTP_401_UNAUTHORIZED, BizCode.AUTH_INVALID_CREDENTIALS, "用户名或密码错误")

    registration_status = getattr(user, "registration_status", REGISTRATION_APPROVED) or REGISTRATION_APPROVED
    if registration_status == REGISTRATION_PENDING:
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "注册申请正在审核中")
    if registration_status == REGISTRATION_REJECTED:
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.FORBIDDEN, "注册申请未通过，请联系管理员")

    if user.status == 0:
        raise AppException(status.HTTP_403_FORBIDDEN, BizCode.AUTH_ACCOUNT_DISABLED, "账户不可用")

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
