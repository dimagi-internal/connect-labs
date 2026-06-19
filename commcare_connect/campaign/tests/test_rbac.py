from commcare_connect.campaign.services import rbac


def test_admin_has_full_access_everywhere():
    for module in rbac.MODULES:
        for verb in rbac.VERBS:
            assert rbac.can("campaign_admin", module, verb) is True


def test_payment_admin_can_approve_payments_but_not_kyc():
    assert rbac.can("payment_admin", "payments", "approve") is True
    assert rbac.can("payment_admin", "payments", "view") is True
    assert rbac.can("payment_admin", "kyc", "view") is False
    assert rbac.can("payment_admin", "payments", "delete") is False


def test_compliance_admin_kyc_but_not_payments():
    assert rbac.can("compliance_admin", "kyc", "approve") is True
    assert rbac.can("compliance_admin", "kyc", "edit") is True
    assert rbac.can("compliance_admin", "payments", "view") is False


def test_operations_manager_manages_activities_views_planning():
    assert rbac.can("operations_manager", "activities", "manage") is True
    assert rbac.can("operations_manager", "activities", "create") is True
    assert rbac.can("operations_manager", "planning", "view") is True
    assert rbac.can("operations_manager", "planning", "edit") is False


def test_reporting_user_is_view_and_export_only():
    assert rbac.can("reporting_user", "reporting", "view") is True
    assert rbac.can("reporting_user", "reporting", "export") is True
    assert rbac.can("reporting_user", "reporting", "edit") is False
    assert rbac.can("reporting_user", "overview", "view") is True


def test_only_admin_manages_users():
    assert rbac.can("campaign_admin", "users", "manage") is True
    for role in ["payment_admin", "compliance_admin", "operations_manager", "reporting_user"]:
        assert rbac.can(role, "users", "view") is False


def test_unknown_role_or_module_is_denied():
    assert rbac.can("nope", "payments", "view") is False
    assert rbac.can("campaign_admin", "nope", "view") is False


def test_access_label():
    assert rbac.access_label("campaign_admin", "payments") == "Full Access"
    assert rbac.access_label("payment_admin", "payments") == "View, Approve"
    assert rbac.access_label("payment_admin", "kyc") == "No Access"
