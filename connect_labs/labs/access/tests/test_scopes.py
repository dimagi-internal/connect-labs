"""May this caller use this scope?

The gap this closes: labs/synthetic/access.py gates the labs-only namespace at
two request chokepoints, and delegates REAL scopes to Connect's LabsRecord API.
Apps that serve real-scoped data from local Postgres (benchmarks, supply_chain)
fall between: the labs-only gate correctly skips them, and Connect never sees
the request.
"""

from unittest.mock import MagicMock

import pytest

from connect_labs.labs.access import scopes
from connect_labs.labs.access.scopes import SYSTEM, Caller, may_use

ORG_DATA = {
    "organizations": [{"id": "dimagi-kmc", "slug": "dimagi-kmc"}],
    "opportunities": [{"id": 523}, {"id": 874}],
    "programs": [{"id": 46}],
}


def _request(org_data=ORG_DATA):
    request = MagicMock()
    request.session = {"labs_oauth": {"organization_data": org_data}}
    request.user = MagicMock(is_authenticated=True, view_synthetic_opps=False)
    return request


def test_no_caller_is_denied():
    """Absence must never read as permission -- that is the bug being fixed."""
    assert may_use(Caller(), organization_id="dimagi-kmc") is not None
    assert may_use(None, organization_id="dimagi-kmc") is not None


def test_system_caller_is_permitted():
    """Seeders and management commands run unauthenticated, explicitly."""
    assert may_use(SYSTEM, organization_id="anything", opportunity_id=99999) is None


def test_a_web_caller_is_resolved_from_the_session():
    caller = Caller(request=_request())
    assert may_use(caller, organization_id="dimagi-kmc") is None
    assert may_use(caller, opportunity_id=523) is None


def test_a_web_caller_is_refused_another_organisation():
    caller = Caller(request=_request())
    denied = may_use(caller, organization_id="someone-else")
    assert denied and "someone-else" in denied


def test_an_organisation_authorises_by_either_of_its_names():
    """Labs carries both, and the codebase names orgs by both: `registry_source`
    identifies one as `{"organization_id": 179}` while `benchmarks` uses the
    slug. Taking only one convention refuses a caller using the other -- a
    permission failure with no permission problem behind it."""
    caller = Caller(request=_request({"organizations": [{"id": 179, "slug": "dimagi-kmc"}]}))
    assert may_use(caller, organization_id=179) is None
    assert may_use(caller, organization_id="dimagi-kmc") is None
    # The other side, so accepting both names is not accepting anything.
    assert may_use(caller, organization_id=999) is not None


def test_a_web_caller_is_refused_an_opportunity_they_do_not_hold():
    caller = Caller(request=_request())
    assert may_use(caller, opportunity_id=999999) is not None


def test_every_supplied_scope_must_pass_not_just_one():
    """A caller holding the org but not the opportunity is refused."""
    caller = Caller(request=_request())
    assert may_use(caller, organization_id="dimagi-kmc", opportunity_id=999999) is not None


def test_no_scope_supplied_is_permitted():
    """Nothing to authorise -- a call with no scope args is not a denial."""
    assert may_use(Caller(request=_request())) is None


def test_an_mcp_caller_is_resolved_from_the_token(monkeypatch):
    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: ORG_DATA,
    )
    caller = Caller(user=MagicMock(username="u"), access_token="t")
    assert may_use(caller, organization_id="dimagi-kmc") is None
    assert may_use(caller, organization_id="someone-else") is not None


def test_an_unreachable_org_fetch_fails_closed(monkeypatch):
    """fetch_user_organization_data returns None when it cannot REACH Connect.
    That must deny, not permit."""
    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: None,
    )
    caller = Caller(user=MagicMock(username="u"), access_token="t")
    assert may_use(caller, organization_id="dimagi-kmc") is not None


# ─── labs-only scopes: both surfaces must see the same set ──────────────────
#
# Production Connect has never heard of a labs-only opportunity or programme,
# so a policy built on its fetch alone refuses every synthetic scope. The web
# surface never noticed because `get_org_data` merges the labs-only tree in;
# the token surface refused programme 10501 and every other demo scope, which
# is the entire supply-chain MCP catalogue against the programmes it exists to
# drive.


@pytest.fixture
def labs_only_programme(db):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=10501,
        program_id=10501,
        labs_only=True,
        enabled=True,
        org_name="OES",
        program_name="OES demo",
        allowed_domains=[],
        gdrive_folder_id="x",
    )


def _db_user(*, view_synthetic_opps):
    from django.contrib.auth import get_user_model

    return get_user_model().objects.create(
        username=f"u-{view_synthetic_opps}",
        email="someone@example.org",
        view_synthetic_opps=view_synthetic_opps,
    )


def _token_caller(monkeypatch, user):
    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: ORG_DATA,
    )
    return Caller(user=user, access_token="t")


def test_a_token_caller_may_use_a_labs_only_programme_they_can_see(monkeypatch, labs_only_programme):
    caller = _token_caller(monkeypatch, _db_user(view_synthetic_opps=True))
    assert may_use(caller, program_id=10501) is None
    assert may_use(caller, opportunity_id=10501) is None
    assert may_use(caller, organization_id="labs-synthetic-oes") is None


def test_a_token_caller_may_not_use_a_labs_only_programme_they_cannot_see(monkeypatch, labs_only_programme):
    """The other side of the same fixture, so the merge is not blanket permission."""
    caller = _token_caller(monkeypatch, _db_user(view_synthetic_opps=False))
    assert may_use(caller, program_id=10501) is not None
    assert may_use(caller, opportunity_id=10501) is not None


def test_a_real_scope_the_token_caller_does_not_hold_is_still_refused(monkeypatch, labs_only_programme):
    """Merging labs-only scopes in must not widen the real ones."""
    caller = _token_caller(monkeypatch, _db_user(view_synthetic_opps=True))
    assert may_use(caller, program_id=999999) is not None
    assert may_use(caller, organization_id="someone-else") is not None


# ─── unknowable is not "holds nothing" ──────────────────────────────────────


def test_the_helpers_raise_rather_than_return_an_empty_set_when_unreachable(monkeypatch):
    """`opportunity_ids(caller) == set()` reads exactly like a real answer, and a
    direct caller that believed it would report a network blip as a permission
    failure. There is no empty set to misread."""
    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: None,
    )
    caller = Caller(user=MagicMock(username="u"), access_token="t")
    for helper in (scopes.org_slugs, scopes.opportunity_ids, scopes.program_ids):
        with pytest.raises(scopes.ScopesUnavailable):
            helper(caller)


def test_the_helpers_return_a_real_empty_answer_as_an_empty_set(monkeypatch):
    """ "We asked, and you belong to nothing" IS a set -- only "we could not ask" raises."""
    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: {"organizations": [], "opportunities": [], "programs": []},
    )
    caller = Caller(user=MagicMock(username="u", view_synthetic_opps=False), access_token="t")
    assert scopes.opportunity_ids(caller) == set()


# ─── a half-built session is not a permission fact ──────────────────────────


def test_an_empty_session_falls_through_to_the_token(monkeypatch):
    """`supply_chain/identity.py` does exactly this and documents why: an expired
    or half-built session looks identical to "belongs to nothing". Diverging made
    one stale session a hard 403 on every supply page and a working MCP call."""
    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: ORG_DATA,
    )
    request = MagicMock()
    request.session = {}
    request.user = MagicMock(is_authenticated=True, view_synthetic_opps=False)
    caller = Caller(request=request, user=request.user, access_token="t")
    assert may_use(caller, organization_id="dimagi-kmc") is None
    # ...and the fall-through is a fetch, not a waiver.
    assert may_use(caller, organization_id="someone-else") is not None


def test_an_empty_session_with_nothing_to_fall_through_to_still_denies():
    """A bare request and no identity: there is nobody to ask, so it denies."""
    request = MagicMock()
    request.session = {}
    request.user = MagicMock(is_authenticated=True, view_synthetic_opps=False)
    assert may_use(Caller(request=request), organization_id="dimagi-kmc") is not None
