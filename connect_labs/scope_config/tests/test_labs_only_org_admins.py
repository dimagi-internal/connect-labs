"""A labs-only organisation's settings belong to its administrators.

Before a labs-only organisation was a registry row, it was every synthetic
opportunity anyone gave the same name, so nobody could be its member and only
Dimagi staff wrote its layer (#2424). Now it is a `LabsOrg`, and
`marketplace.OrgMembership` says who administers it.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

import pytest

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgMembership
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope, chain_for
from connect_labs.scope_config.service import Forbidden
from connect_labs.scope_config.tests.conftest import caller
from connect_labs.users.models import User

pytestmark = pytest.mark.django_db

# Two organisations with the SAME display name: once one bucket, now two rows.
ONE = "labs-synthetic-labs-synthetic"
TWO = "labs-synthetic-labs-synthetic-2"

TWINS = {
    "organizations": [
        {"id": ONE, "slug": ONE, "name": "Labs Synthetic", "labs_only": True},
        {"id": TWO, "slug": TWO, "name": "Labs Synthetic", "labs_only": True},
    ],
    "programs": [
        {"id": 10001, "name": "Mine", "organization": ONE, "labs_only": True},
        {"id": 10002, "name": "Theirs", "organization": TWO, "labs_only": True},
    ],
    "opportunities": [],
}


@pytest.fixture(autouse=True)
def twins(monkeypatch):
    monkeypatch.setattr(service, "tree_for", lambda caller: TWINS)
    one = LabsOrg.objects.create(slug=ONE, name="Labs Synthetic", labs_only=True)
    two = LabsOrg.objects.create(slug=TWO, name="Labs Synthetic", labs_only=True)
    return one, two


def _admin_of(org, username):
    user = User.objects.create_user(username=username, email=f"{username}@dimagi.com")
    OrgMembership.objects.create(org=org, user=user, role="admin")


def _sees_both(username):
    return caller(username, holds=(f"organization:{ONE}", f"organization:{TWO}", "program:10001", "program:10002"))


def test_an_administrator_writes_their_organisations_layer(twins):
    one, _ = twins
    _admin_of(one, "ana")

    got = service.update("t", Scope.of("organization", ONE), {"a": 5}, _sees_both("ana"))

    assert got["value"]["a"] == 5


def test_seeing_an_organisation_is_not_administering_it(twins):
    with pytest.raises(Forbidden, match="administrators"):
        service.update("t", Scope.of("organization", ONE), {"a": 5}, _sees_both("viewer"))


def test_a_plain_member_is_not_an_administrator(twins):
    one, _ = twins
    user = User.objects.create_user(username="mo", email="mo@dimagi.com")
    OrgMembership.objects.create(org=one, user=user, role="member")

    with pytest.raises(Forbidden, match="administrators"):
        service.update("t", Scope.of("organization", ONE), {"a": 5}, _sees_both("mo"))


def test_administering_one_twin_does_not_reach_the_other(twins):
    """Same display name, different organisations: no shared or overwritten config."""
    one, _ = twins
    _admin_of(one, "ana")
    ana = _sees_both("ana")

    with pytest.raises(Forbidden, match="administrators"):
        service.update("t", Scope.of("organization", TWO), {"a": 9}, ana)
    service.update("t", Scope.of("organization", ONE), {"a": 5}, ana)

    # Each programme reads its own organisation's layer, and only that one.
    assert chain_for(Scope.of("program", 10002), TWINS)[0] == Scope.of("organization", TWO)
    theirs = service.get("t", Scope.of("program", 10002), _sees_both("ben"))
    assert theirs["value"]["a"] == 0
    mine = service.get("t", Scope.of("program", 10001), _sees_both("ben"))
    assert mine["value"]["a"] == 5


def test_a_home_page_set_on_one_twin_is_not_the_others(twins):
    one, _ = twins
    _admin_of(one, "ana")
    service.update(
        "labs",
        Scope.of("organization", ONE),
        {"home": {"fill": {"workflow": 8, "program_id": 10001}}},
        _sees_both("ana"),
    )

    other = service.get("labs", Scope.of("organization", TWO), _sees_both("ben"))

    assert not (other["value"].get("home") or {}).get("fill")


def test_an_administrator_of_a_real_organisations_row_gains_nothing_here(twins):
    """Only an unlinked labs-only row answers for a labs-only key."""
    real = LabsOrg.objects.create(slug="real-ngo", name="Real", connect_organization_id=77)
    _admin_of(real, "ana")

    with pytest.raises(Forbidden, match="administrators"):
        service.update("t", Scope.of("organization", ONE), {"a": 5}, _sees_both("ana"))


def test_staff_still_write_any_labs_only_layer(twins):
    staff = caller("staff", is_staff=True)

    assert service.update("t", Scope.of("organization", TWO), {"a": 3}, staff)["value"]["a"] == 3
