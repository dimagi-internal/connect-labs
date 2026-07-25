"""Supply test fixtures.

Deliberately self-contained: no labs fixtures, no labs test scaffolding
(satellite-site convention — the suite must travel with the app).
"""
import pytest
from django.test import Client

from . import factories as f


@pytest.fixture
def supplier_client(db):
    member = f.SupplierMemberFactory(user__username="acme-user")
    member.user.set_password("pw")
    member.user.save()
    c = Client()
    c.force_login(member.user)
    return c, member


@pytest.fixture
def admin_client(db):
    role = f.StaffRoleFactory(role="procurement_admin", user__username="oes-admin")
    c = Client()
    c.force_login(role.user)
    return c, role.user


@pytest.fixture
def reviewer_client(db):
    role = f.StaffRoleFactory(role="reviewer", user__username="oes-reviewer")
    c = Client()
    c.force_login(role.user)
    return c, role.user
