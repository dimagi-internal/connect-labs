"""supply_chain serves real-scoped data from local Postgres, so nothing
downstream authorises its scope. The caller must be checked here."""

from unittest.mock import MagicMock

import pytest
from django.core.exceptions import PermissionDenied

from connect_labs.labs.access.scopes import SYSTEM, Caller
from connect_labs.supply_chain.data_access import SupplyDataAccess

ORG_DATA = {
    "organizations": [{"id": "dimagi", "slug": "dimagi"}],
    "programs": [{"id": 176}],
    "opportunities": [{"id": 523}],
}


def _request():
    request = MagicMock()
    request.session = {"labs_oauth": {"organization_data": ORG_DATA}}
    request.user = MagicMock(is_authenticated=True, view_synthetic_opps=False)
    return request


def test_a_caller_may_use_a_programme_they_hold():
    access = SupplyDataAccess(program_id=176, caller=Caller(request=_request()))
    assert access.program_id == 176


def test_a_caller_may_not_use_a_programme_they_do_not_hold():
    """The other side of the same fixture -- so the gate cannot pass as always-deny."""
    with pytest.raises(PermissionDenied):
        SupplyDataAccess(program_id=999999, caller=Caller(request=_request()))


def test_an_organisation_the_caller_does_not_hold_is_not_authorised_here():
    """Deliberate, not forgotten: `organization_id` selects nothing.

    mcp_tools.py's schema says it outright -- "carried as context. It does NOT
    select a registry: commodities, trade items and suppliers are scoped to the
    programme". Authorising a value that reaches no query would protect nothing
    and only add a way to refuse a legitimate caller. This pins the decision so
    a later tidy-up cannot quietly start checking it.
    """
    access = SupplyDataAccess(
        program_id=176,
        organization_id="someone-else",
        caller=Caller(request=_request()),
    )
    assert access.organization_id == "someone-else"


def test_no_caller_is_refused():
    with pytest.raises(PermissionDenied):
        SupplyDataAccess(program_id=176)


def test_the_system_caller_is_allowed():
    """Seeders and management commands, explicitly."""
    access = SupplyDataAccess(program_id=176, caller=SYSTEM)
    assert access.program_id == 176


def test_a_scope_inherited_from_labs_context_is_authorised_too():
    """The constructor merges request.labs_context, so the merged value -- not
    just the argument -- is what has to pass. A scope that arrives by
    inheritance is exactly as unchecked as one that arrives by argument."""
    request = _request()
    request.labs_context = {"program_id": 999999}
    with pytest.raises(PermissionDenied):
        SupplyDataAccess(request=request, caller=Caller(request=request))

    request.labs_context = {"program_id": 176}
    assert SupplyDataAccess(request=request, caller=Caller(request=request)).program_id == 176


def _mcp_call(monkeypatch, org_data, user=None, **scope):
    """Drive the generated MCP handler -- the shared construction all ~70 tools use.

    A fake operation is registered so the call exercises the real handler and
    the real registry lookup without depending on any one capability.
    """
    from unittest.mock import patch

    from connect_labs.supply_chain import operations as operations_module
    from connect_labs.supply_chain.mcp_tools import _make_handler

    monkeypatch.setattr(
        "connect_labs.labs.access.scopes.fetch_user_organization_data",
        lambda token, owner=None: org_data,
    )

    operation = operations_module.Operation(
        name="scope_probe",
        summary="test probe for scope authorisation, not a real capability",
        input_schema=operations_module.obj({}),
        handler=lambda access, **payload: {"program_id": access.program_id},
    )

    class _Ref:
        name = "scope_probe"

    with patch.dict(operations_module._REGISTRY, {"scope_probe": operation}):
        handler = _make_handler(_Ref)
        with patch("connect_labs.supply_chain.mcp_tools.require_connect_token", return_value="tok"):
            return handler(user=user or MagicMock(username="someone"), **scope)


def test_the_mcp_surface_serves_a_programme_the_caller_holds(monkeypatch):
    assert _mcp_call(monkeypatch, ORG_DATA, program_id=176) == {"program_id": 176}


def test_the_mcp_surface_refuses_a_programme_the_caller_does_not_hold(monkeypatch):
    """The MCP route is where scope is most freely caller-supplied: the tool
    schema invites organization_id and program_id as plain arguments, and the
    labs database answers them with no Connect membership check behind it."""
    with pytest.raises(PermissionDenied):
        _mcp_call(monkeypatch, ORG_DATA, program_id=999999)


def test_the_mcp_surface_refuses_when_connect_cannot_be_reached(monkeypatch):
    """A fetch that returns None means unknowable, which must deny."""
    with pytest.raises(PermissionDenied):
        _mcp_call(monkeypatch, None, program_id=176)


@pytest.mark.django_db
def test_the_mcp_surface_serves_a_labs_only_programme(monkeypatch):
    """The demo surface this catalogue exists for.

    Production Connect has never heard of programme 10501, so the org tree its
    fetch returns cannot contain it. Resolving a token caller from that fetch
    ALONE therefore refused every labs-only programme -- 10501, 10063, every OES
    demo scope -- and all ~70 generated tools with it, while the web pages over
    the same data worked because `get_org_data` merges the labs-only tree in.
    The policy merges both now, so this asserts the surface, not the helper.
    """
    from django.contrib.auth import get_user_model

    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(
        opportunity_id=10501,
        program_id=10501,
        labs_only=True,
        enabled=True,
        org_name="OES",
        allowed_domains=[],
        gdrive_folder_id="x",
    )
    entitled = get_user_model().objects.create(
        username="entitled", email="entitled@example.org", view_synthetic_opps=True
    )
    assert _mcp_call(monkeypatch, ORG_DATA, user=entitled, program_id=10501) == {"program_id": 10501}

    # The other side of the same fixture: the registry decides who sees it, so a
    # caller who is not entitled to the synthetic opp is still refused.
    not_entitled = get_user_model().objects.create(
        username="stranger", email="stranger@example.org", view_synthetic_opps=False
    )
    with pytest.raises(PermissionDenied):
        _mcp_call(monkeypatch, ORG_DATA, user=not_entitled, program_id=10501)


def test_the_system_escape_hatch_is_greppable():
    """Widening the set of unauthenticated entry points must be a reviewed act."""
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[2]
    # Keyed by PATH, not by basename. `p.name` exempted any file called
    # scopes.py anywhere in the tree -- including `supply_chain/scopes.py`,
    # which is a different module about synthetic programmes and has nothing to
    # do with the escape hatch. An allow-list that matches on a name this
    # common is not an allow-list.
    allowed = {
        "supply_chain/management/commands/supply_dev_seed.py",
        "supply_chain/management/commands/supply_ingest_stock_reports.py",
        "supply_chain/management/commands/supply_load_bootstrap.py",
        "labs/access/scopes.py",
    }
    offenders = sorted(
        p.relative_to(root).as_posix()
        for p in root.rglob("*.py")
        # Match the IMPORT or USE, not the bare word: "SYSTEM" already appears
        # in audit_trail/service.py, audit_trail/models.py, audit/prior_audit_models.py
        # and pulse.css for unrelated reasons, and a bare-string grep fails on those.
        if "tests" not in p.parts
        and p.relative_to(root).as_posix() not in allowed
        and ("caller=SYSTEM" in p.read_text() or "import SYSTEM" in p.read_text())
    )
    assert offenders == [], f"unexpected SYSTEM callers: {offenders}"
