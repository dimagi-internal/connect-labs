"""May this caller use this scope?

The gap this closes: labs/synthetic/access.py gates the labs-only namespace at
two request chokepoints, and delegates REAL scopes to Connect's LabsRecord API.
Apps that serve real-scoped data from local Postgres (benchmarks, supply_chain)
fall between: the labs-only gate correctly skips them, and Connect never sees
the request.
"""

from unittest.mock import MagicMock

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
