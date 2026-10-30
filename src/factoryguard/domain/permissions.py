from __future__ import annotations

"""Local desktop roles and capability checks.

The desktop console is intentionally local-first, so this module models the
operator's current role without pretending to be a remote identity provider.
It gives the UI one consistent policy boundary and keeps audit actor values
separate from authorization decisions.
"""

from enum import StrEnum


class UserRole(StrEnum):
    ADMIN = "admin"
    ARMING_ADMIN = "arming_admin"
    OPERATOR = "operator"
    AUDITOR = "auditor"


class Permission(StrEnum):
    VIEW = "view"
    MANAGE_TOPOLOGY = "manage_topology"
    MANAGE_RULES = "manage_rules"
    MANAGE_ARMING = "manage_arming"
    REVIEW_EVENTS = "review_events"
    MANAGE_SYSTEM = "manage_system"
    PUBLISH_RULES = "publish_rules"


ROLE_LABELS: dict[UserRole, str] = {
    UserRole.ADMIN: "系统管理员",
    UserRole.ARMING_ADMIN: "布防管理员",
    UserRole.OPERATOR: "值守人员",
    UserRole.AUDITOR: "审计人员",
}

ROLE_PERMISSIONS: dict[UserRole, frozenset[Permission]] = {
    UserRole.ADMIN: frozenset(Permission),
    UserRole.ARMING_ADMIN: frozenset(
        {
            Permission.VIEW,
            Permission.MANAGE_TOPOLOGY,
            Permission.MANAGE_RULES,
            Permission.MANAGE_ARMING,
            Permission.PUBLISH_RULES,
            Permission.REVIEW_EVENTS,
        }
    ),
    UserRole.OPERATOR: frozenset({Permission.VIEW, Permission.REVIEW_EVENTS}),
    UserRole.AUDITOR: frozenset({Permission.VIEW}),
}


def role_from_value(value: object) -> UserRole:
    try:
        return value if isinstance(value, UserRole) else UserRole(str(value))
    except ValueError:
        return UserRole.ADMIN


def can(role: UserRole | str, permission: Permission | str) -> bool:
    resolved_role = role_from_value(role)
    try:
        resolved_permission = permission if isinstance(permission, Permission) else Permission(str(permission))
    except ValueError:
        return False
    return resolved_permission in ROLE_PERMISSIONS[resolved_role]


__all__ = ["Permission", "ROLE_LABELS", "ROLE_PERMISSIONS", "UserRole", "can", "role_from_value"]
