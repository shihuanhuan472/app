from enum import IntEnum
from typing import Optional


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


ROLE_NAME_TO_VALUE = {
    "admin": UserRole.ADMIN,
    "technician": UserRole.TECHNICIAN,
    "technician_rw": UserRole.TECHNICIAN,
    "reviewer": UserRole.REVIEWER,
    "maintenance": UserRole.MAINTENANCE,
    "maintenance_staff": UserRole.MAINTENANCE,
    "readonly": UserRole.MAINTENANCE,
    "管理员": UserRole.ADMIN,
    "技术人员": UserRole.TECHNICIAN,
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
}


ROLE_TO_EXPECTED_PERM = {
    int(UserRole.ADMIN): int(UserPerm.ADMIN),
    int(UserRole.TECHNICIAN): int(UserPerm.READ_WRITE),
    int(UserRole.REVIEWER): int(UserPerm.REVIEW),
    int(UserRole.MAINTENANCE): int(UserPerm.READ_ONLY),
}


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


def has_role(user, *roles: UserRole) -> bool:
    user_role = normalize_role_value(getattr(user, "role", None))
    return user_role in {int(role) for role in roles}
