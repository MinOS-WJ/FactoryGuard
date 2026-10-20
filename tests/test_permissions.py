from factoryguard.domain.permissions import Permission, UserRole, can, role_from_value


def test_roles_have_expected_capabilities():
    assert can(UserRole.ADMIN, Permission.MANAGE_SYSTEM)
    assert can(UserRole.ARMING_ADMIN, Permission.MANAGE_RULES)
    assert can(UserRole.ARMING_ADMIN, Permission.MANAGE_ARMING)
    assert not can(UserRole.ARMING_ADMIN, Permission.MANAGE_SYSTEM)
    assert can(UserRole.OPERATOR, Permission.REVIEW_EVENTS)
    assert not can(UserRole.OPERATOR, Permission.MANAGE_TOPOLOGY)
    assert can(UserRole.AUDITOR, Permission.VIEW)
    assert not can(UserRole.AUDITOR, Permission.REVIEW_EVENTS)


def test_unknown_role_values_fall_back_to_local_admin():
    assert role_from_value("unknown") is UserRole.ADMIN
