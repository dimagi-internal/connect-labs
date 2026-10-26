"""Tests for labs-only (synthetic) opportunity access control.

Covers the security boundary that isolates one tenant's labs-only records from
another's: ``SyntheticOpportunity.is_accessible_to`` and the shared scope gate
``labs_only_scope_denied_reason`` used at the web + MCP chokepoints.
"""

from __future__ import annotations

import pytest

from connect_labs.labs.synthetic.access import labs_only_scope_denied_reason, user_can_access_labs_only_opp
from connect_labs.labs.synthetic.models import LABS_ONLY_OPP_ID_FLOOR, SyntheticOpportunity
from connect_labs.users.models import User


def _make_opp(opp_id, *, allowed_domains=None, created_by=None, labs_only=True):
    return SyntheticOpportunity.objects.create(
        opportunity_id=opp_id,
        gdrive_folder_id=f"folder-{opp_id}",
        labs_only=labs_only,
        enabled=True,
        allowed_domains=allowed_domains or [],
        created_by=created_by,
    )


def _user(email, username):
    return User.objects.create_user(username=username, email=email)


@pytest.mark.django_db
class TestIsAccessibleTo:
    def test_partner_matching_domain_allowed(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 1, allowed_domains=["@partner-y.com"])
        assert opp.is_accessible_to(_user("w@partner-y.com", "u1")) is True

    def test_partner_wrong_domain_denied(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 2, allowed_domains=["@partner-y.com"])
        assert opp.is_accessible_to(_user("w@partner-x.com", "u2")) is False

    def test_empty_allowed_domains_is_open(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 3, allowed_domains=[])
        assert opp.is_accessible_to(_user("anyone@wherever.com", "u3")) is True

    def test_dimagi_internal_operator_always_allowed(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 4, allowed_domains=["@partner-y.com"])
        assert opp.is_accessible_to(_user("staff@dimagi.com", "u4")) is True
        assert opp.is_accessible_to(_user("ai@dimagi-ai.com", "u5")) is True

    def test_creator_always_allowed(self):
        creator = _user("creator@partner-x.com", "u6")
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 5, allowed_domains=["@partner-y.com"], created_by=creator)
        assert opp.is_accessible_to(creator) is True

    def test_non_labs_only_never_accessible_here(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 6, allowed_domains=[], labs_only=False)
        assert opp.is_accessible_to(_user("staff@dimagi.com", "u7")) is False


@pytest.mark.django_db
class TestADimagiAssociate:
    """A @dimagi-associate.com address is a Dimagi sign-in, so it gets the same
    labs-only access and visibility as @dimagi.com and @dimagi-ai.com."""

    def _associate(self, username, *, toggle=True):
        return User.objects.create_user(
            username=username, email="helper@dimagi-associate.com", view_synthetic_opps=toggle
        )

    def test_may_open_an_opp_allowlisted_for_dimagi(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 30, allowed_domains=["@dimagi.com"])
        assert opp.is_accessible_to(self._associate("a1")) is True

    def test_may_open_a_partner_allowlisted_opp_as_dimagi_staff_may(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 31, allowed_domains=["@partner-y.com"])
        assert opp.is_accessible_to(_user("staff@dimagi.com", "a2-staff")) is True
        assert opp.is_accessible_to(self._associate("a2")) is True
        assert opp.is_accessible_to(_user("w@partner-x.com", "a2-outsider")) is False

    def test_sees_an_opp_allowlisted_for_dimagi(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 32, allowed_domains=["@dimagi.com"])
        assert opp.is_visible_to(self._associate("a3")) is True

    def test_an_allowlist_naming_associates_admits_dimagi_staff(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 33, allowed_domains=["@dimagi-associate.com"])
        staff = User.objects.create_user(username="a4-staff", email="s@dimagi.com", view_synthetic_opps=True)
        assert opp.is_visible_to(staff) is True

    def test_does_not_see_a_partner_allowlisted_opp_as_dimagi_staff_does_not(self):
        # is_visible_to's Dimagi rule applies only when the allowlist names a Dimagi
        # domain; a partner-only allowlist hides the opp from the picker for staff
        # too, though both may still open it (is_accessible_to above).
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 34, allowed_domains=["@partner-y.com"])
        staff = User.objects.create_user(username="a5-staff", email="s5@dimagi.com", view_synthetic_opps=True)
        partner = User.objects.create_user(username="a5-partner", email="p@partner-y.com", view_synthetic_opps=True)
        assert opp.is_visible_to(partner) is True
        assert opp.is_visible_to(staff) is False
        assert opp.is_visible_to(self._associate("a5")) is False

    def test_still_needs_the_picker_toggle_to_see_one(self):
        opp = _make_opp(LABS_ONLY_OPP_ID_FLOOR + 35, allowed_domains=["@dimagi.com"])
        assert opp.is_visible_to(self._associate("a6", toggle=False)) is False


@pytest.mark.django_db
class TestScopeGate:
    def test_real_opp_id_is_ignored(self):
        # id below the labs-only floor is a real Connect opp — this gate must not
        # touch it (production API enforces it downstream).
        assert labs_only_scope_denied_reason(_user("w@partner-x.com", "r1"), opportunity_id=42) is None

    def test_labs_only_denied_for_outside_partner(self):
        _make_opp(LABS_ONLY_OPP_ID_FLOOR + 10, allowed_domains=["@partner-y.com"])
        reason = labs_only_scope_denied_reason(
            _user("w@partner-x.com", "r2"), opportunity_id=LABS_ONLY_OPP_ID_FLOOR + 10
        )
        assert reason and "not accessible" in reason

    def test_labs_only_allowed_for_matching_partner(self):
        _make_opp(LABS_ONLY_OPP_ID_FLOOR + 11, allowed_domains=["@partner-y.com"])
        reason = labs_only_scope_denied_reason(
            _user("w@partner-y.com", "r3"), opportunity_id=LABS_ONLY_OPP_ID_FLOOR + 11
        )
        assert reason is None

    def test_program_scope_denied_for_outside_partner(self):
        _make_opp(LABS_ONLY_OPP_ID_FLOOR + 12, allowed_domains=["@partner-y.com"])
        # program_id == opportunity_id (unset program_id → self-program)
        reason = labs_only_scope_denied_reason(_user("w@partner-x.com", "r4"), program_id=LABS_ONLY_OPP_ID_FLOOR + 12)
        assert reason and "not accessible" in reason

    def test_unknown_labs_only_id_denied(self):
        # An id in the labs-only range with no registered row grants nothing.
        assert user_can_access_labs_only_opp(_user("staff@dimagi.com", "r5"), LABS_ONLY_OPP_ID_FLOOR + 999) is False
