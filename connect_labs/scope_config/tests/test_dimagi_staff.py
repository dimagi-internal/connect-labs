"""Dimagi staff, for Settings and pages, is `is_staff` OR a Dimagi sign-in.

Settings used Django's `is_staff` alone -- a flag set by hand that no OAuth-created
labs user carries -- so a Dimagi person opening a labs-only organisation's Settings
was told "Dimagi staff may change this" and refused (2026-10-12).

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope
from connect_labs.scope_config.service import Forbidden
from connect_labs.scope_config.tests.conftest import caller
from connect_labs.utils.dimagi_user import is_dimagi_staff

pytestmark = pytest.mark.django_db

SHARED = Scope.of("organization", "labs-synthetic-shared")


@pytest.fixture(autouse=True)
def labs_only_org():
    LabsOrg.objects.create(slug="labs-synthetic-shared", name="Shared", labs_only=True)


def test_a_dimagi_sign_in_writes_a_labs_only_organisations_layer_it_does_not_administer():
    dimagi = caller("jo", holds=("organization:labs-synthetic-shared",), email="jo@dimagi.com")

    assert service.write_refusal(dimagi, SHARED, {}) is None
    assert service.update("t", SHARED, {"a": 4}, dimagi)["value"]["a"] == 4


def test_a_dimagi_ai_sign_in_counts_too():
    agent = caller("bot", holds=("organization:labs-synthetic-shared",), email="bot@dimagi-ai.com")

    assert service.write_refusal(agent, SHARED, {}) is None


def test_another_domain_is_still_refused_and_told_the_real_rule():
    outsider = caller("lee", holds=("organization:labs-synthetic-shared",), email="lee@dimagi.org")

    with pytest.raises(Forbidden) as refused:
        service.update("t", SHARED, {"a": 4}, outsider)
    assert "@dimagi.com" in str(refused.value) and "program instead" in str(refused.value)


def test_a_lookalike_domain_is_not_dimagi():
    class U:
        is_staff = False
        email = "x@notdimagi.com.evil"

    assert not is_dimagi_staff(U())
    assert not is_dimagi_staff(None)


def test_is_staff_alone_still_counts():
    assert service.write_refusal(caller("admin", is_staff=True), SHARED, {}) is None
