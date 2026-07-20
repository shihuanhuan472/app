from enum import IntEnum
from typing import Iterable, Optional, Set

from sqlalchemy import inspect
from sqlalchemy.orm.attributes import NO_VALUE


class UserRole(IntEnum):
    ADMIN = 0
    TECHNICIAN = 1
    REVIEWER = 2
    MAINTENANCE = 3


class UserPerm(IntEnum):
    ADMIN = 0
    READ_WRITE = 1
    REVIEW = 2
    READ_ONLY = 3


class PermissionCode:
    ADMIN = "admin"
    READ_WRITE = "read_write"
    REVIEW = "review"
    READ_ONLY = "read_only"


ROLE_NAME_TO_VALUE = {
    "admin": UserRole.ADMIN,
    "technician": UserRole.TECHNICIAN,
    "technician_rw": UserRole.TECHNICIAN,
    "reviewer": UserRole.REVIEWER,
    "maintenance": UserRole.MAINTENANCE,
    "maintenance_staff": UserRole.MAINTENANCE,
    "readonly": UserRole.MAINTENANCE,
    "管理员": UserRole.ADMIN,
    "系统管理员": UserRole.ADMIN,
    "技术人员": UserRole.TECHNICIAN,
    "维修工程师": UserRole.TECHNICIAN,
    "审核人员": UserRole.REVIEWER,
    "维修人员": UserRole.MAINTENANCE,
}


PERM_NAME_TO_VALUE = {
    "0": UserPerm.ADMIN,
    "1": UserPerm.READ_WRITE,
    "2": UserPerm.REVIEW,
    "3": UserPerm.READ_ONLY,
    "admin": UserPerm.ADMIN,
    "read_write": UserPerm.READ_WRITE,
    "readwrite": UserPerm.READ_WRITE,
    "review": UserPerm.REVIEW,
    "readonly": UserPerm.READ_ONLY,
    "read_only": UserPerm.READ_ONLY,
}


ROLE_TO_EXPECTED_PERM = {
    int(UserRole.ADMIN): int(UserPerm.ADMIN),
    int(UserRole.TECHNICIAN): int(UserPerm.READ_WRITE),
    int(UserRole.REVIEWER): int(UserPerm.REVIEW),
    int(UserRole.MAINTENANCE): int(UserPerm.READ_ONLY),
}


ROLE_TO_PERMISSION = {
    int(UserRole.ADMIN): PermissionCode.ADMIN,
    int(UserRole.TECHNICIAN): PermissionCode.READ_WRITE,
    int(UserRole.REVIEWER): PermissionCode.REVIEW,
    int(UserRole.MAINTENANCE): PermissionCode.READ_ONLY,
}


PERM_TO_PERMISSION = {
    int(UserPerm.ADMIN): PermissionCode.ADMIN,
    int(UserPerm.READ_WRITE): PermissionCode.READ_WRITE,
    int(UserPerm.REVIEW): PermissionCode.REVIEW,
    int(UserPerm.READ_ONLY): PermissionCode.READ_ONLY,
}


ROLE_NAME_TO_PERMISSION = {
    "admin": PermissionCode.ADMIN,
    "technician": PermissionCode.READ_WRITE,
    "technician_rw": PermissionCode.READ_WRITE,
    "reviewer": PermissionCode.REVIEW,
    "maintenance": PermissionCode.READ_ONLY,
    "maintenance_staff": PermissionCode.READ_ONLY,
    "readonly": PermissionCode.READ_ONLY,
}


VALID_PERMISSIONS = {
    PermissionCode.ADMIN,
    PermissionCode.READ_WRITE,
    PermissionCode.REVIEW,
    PermissionCode.READ_ONLY,
}


DEFAULT_ROLE_GROUPS = [
    {
        "code": "admin",
        "name": "系统管理员",
        "description": "拥有系统管理、读写、审核和只读权限",
        "permissions": [
            PermissionCode.ADMIN,
            PermissionCode.READ_WRITE,
            PermissionCode.REVIEW,
            PermissionCode.READ_ONLY,
        ],
        "legacy_role": int(UserRole.ADMIN),
        "legacy_perm": int(UserPerm.ADMIN),
    },
    {
        "code": "technician",
        "name": "技术人员",
        "description": "可查看和维护知识文档",
        "permissions": [PermissionCode.READ_WRITE, PermissionCode.READ_ONLY],
        "legacy_role": int(UserRole.TECHNICIAN),
        "legacy_perm": int(UserPerm.READ_WRITE),
    },
    {
        "code": "reviewer",
        "name": "审核人员",
        "description": "可查看并审核文档提交",
        "permissions": [PermissionCode.REVIEW, PermissionCode.READ_ONLY],
        "legacy_role": int(UserRole.REVIEWER),
        "legacy_perm": int(UserPerm.REVIEW),
    },
    {
        "code": "maintenance",
        "name": "维修人员",
        "description": "可查看知识库和问答内容",
        "permissions": [PermissionCode.READ_ONLY],
        "legacy_role": int(UserRole.MAINTENANCE),
        "legacy_perm": int(UserPerm.READ_ONLY),
    },
]


def normalize_role_value(role_value) -> Optional[int]:
    if role_value is None:
        return None
    if isinstance(role_value, int):
        return role_value
    role_text = str(role_value).strip().lower()
    if role_text in ROLE_NAME_TO_VALUE:
        return int(ROLE_NAME_TO_VALUE[role_text])
    if role_text.isdigit():
        return int(role_text)
    return None


def normalize_perm_value(perm_value) -> Optional[int]:
    if perm_value is None:
        return None
    if isinstance(perm_value, int):
        return perm_value
    perm_text = str(perm_value).strip().lower()
    if perm_text in PERM_NAME_TO_VALUE:
        return int(PERM_NAME_TO_VALUE[perm_text])
    if perm_text.isdigit():
        return int(perm_text)
    return None


def normalize_permission_code(value) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip().lower().replace("-", "_")
    aliases = {
        "0": PermissionCode.ADMIN,
        "1": PermissionCode.READ_WRITE,
        "2": PermissionCode.REVIEW,
        "3": PermissionCode.READ_ONLY,
        "admin": PermissionCode.ADMIN,
        "readwrite": PermissionCode.READ_WRITE,
        "read_write": PermissionCode.READ_WRITE,
        "rw": PermissionCode.READ_WRITE,
        "review": PermissionCode.REVIEW,
        "readonly": PermissionCode.READ_ONLY,
        "read_only": PermissionCode.READ_ONLY,
        "ro": PermissionCode.READ_ONLY,
    }
    return aliases.get(text, text if text in VALID_PERMISSIONS else None)


def normalize_permission_codes(values: Iterable) -> list[str]:
    result = []
    seen = set()
    for value in values or []:
        code = normalize_permission_code(value)
        if code and code not in seen:
            result.append(code)
            seen.add(code)
    return result


def get_expected_perm_for_role(role_value) -> Optional[int]:
    role_normalized = normalize_role_value(role_value)
    if role_normalized is None:
        return None
    return ROLE_TO_EXPECTED_PERM.get(role_normalized)


def is_role_perm_consistent(role_value, perm_value) -> bool:
    role_normalized = normalize_role_value(role_value)
    perm_normalized = normalize_perm_value(perm_value)
    if role_normalized is None or perm_normalized is None:
        return False
    expected_perm = get_expected_perm_for_role(role_normalized)
    return expected_perm == perm_normalized


def _loaded_relationship(obj, attr_name: str):
    if obj is None:
        return None
    try:
        state = inspect(obj)
        if attr_name not in state.attrs:
            return None
        attr_state = state.attrs[attr_name]
        if attr_state is None or attr_state.loaded_value is NO_VALUE:
            return None
        return attr_state.loaded_value
    except Exception:
        return None


def permissions_from_legacy(role_value=None, perm_value=None) -> Set[str]:
    permissions = set()
    role_normalized = normalize_role_value(role_value)
    perm_normalized = normalize_perm_value(perm_value)
    if role_normalized in ROLE_TO_PERMISSION:
        permissions.add(ROLE_TO_PERMISSION[role_normalized])
    if perm_normalized in PERM_TO_PERMISSION:
        permissions.add(PERM_TO_PERMISSION[perm_normalized])
    if PermissionCode.ADMIN in permissions:
        permissions.update(VALID_PERMISSIONS)
    if PermissionCode.READ_WRITE in permissions:
        permissions.add(PermissionCode.READ_ONLY)
    if PermissionCode.REVIEW in permissions:
        permissions.add(PermissionCode.READ_ONLY)
    return permissions


def get_user_permissions(user) -> Set[str]:
    role_group = _loaded_relationship(user, "role_group")
    role_permissions = _loaded_relationship(role_group, "permissions") if role_group else None
    if role_permissions is not None:
        permissions = {
            normalize_permission_code(getattr(permission, "permission_code", None))
            for permission in role_permissions
        }
        permissions = {permission for permission in permissions if permission}
    else:
        permissions = permissions_from_legacy(
            getattr(user, "role", None),
            getattr(user, "perm", None),
        )

    if PermissionCode.ADMIN in permissions:
        permissions.update(VALID_PERMISSIONS)
    if PermissionCode.READ_WRITE in permissions:
        permissions.add(PermissionCode.READ_ONLY)
    if PermissionCode.REVIEW in permissions:
        permissions.add(PermissionCode.READ_ONLY)
    return permissions


def has_permission(user, *permissions: str) -> bool:
    required = {normalize_permission_code(permission) for permission in permissions}
    required = {permission for permission in required if permission}
    if not required:
        required = {PermissionCode.ADMIN}
    user_permissions = get_user_permissions(user)
    return PermissionCode.ADMIN in user_permissions or bool(user_permissions.intersection(required))


def has_role(user, *roles: UserRole) -> bool:
    needed_permissions = []
    for role in roles:
        role_value = normalize_role_value(role)
        permission = ROLE_TO_PERMISSION.get(role_value)
        if permission:
            needed_permissions.append(permission)
    return has_permission(user, *needed_permissions)


def legacy_role_perm_for_permissions(permissions: Iterable[str]) -> tuple[int, int]:
    normalized = set(normalize_permission_codes(permissions))
    if PermissionCode.ADMIN in normalized:
        return int(UserRole.ADMIN), int(UserPerm.ADMIN)
    if PermissionCode.READ_WRITE in normalized:
        return int(UserRole.TECHNICIAN), int(UserPerm.READ_WRITE)
    if PermissionCode.REVIEW in normalized:
        return int(UserRole.REVIEWER), int(UserPerm.REVIEW)
    return int(UserRole.MAINTENANCE), int(UserPerm.READ_ONLY)
