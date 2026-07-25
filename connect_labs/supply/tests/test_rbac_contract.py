"""The RBAC contract: server matrix and client mirror must be identical.

Campaign's equivalent test caught two real drifts; the same technique is used
here — parse the JS object literal and compare it to the Python dict.
"""
import json
import re
from pathlib import Path

import pytest
from django.http import JsonResponse
from django.test import RequestFactory

from connect_labs.supply import roles
from connect_labs.supply.decorators import current_actor, require_perm
from connect_labs.supply.rbac import ROLE_PERMS, can

from . import factories as f

PERMS_JS = Path(__file__).resolve().parents[2] / "static" / "supply" / "perms.js"


def _parse_perms_literal(src):
    """Turn the SUPPLY_PERMS JS object literal into Python data.

    Tolerant of the formatting prettier applies (single quotes, multi-line
    objects, trailing commas) so the contract only fails on real drift.
    """
    match = re.search(r"const SUPPLY_PERMS = (\{.*?\});", src, re.DOTALL)
    assert match, "SUPPLY_PERMS literal not found in perms.js"
    js = match.group(1)
    js = js.replace("'", '"')  # single- → double-quoted strings
    js = re.sub(r"(\w+):", r'"\1":', js)  # quote bare keys
    js = re.sub(r",(\s*[}\]])", r"\1", js)  # strip trailing commas
    return json.loads(js)


def test_perms_js_matches_rbac_py():
    assert _parse_perms_literal(PERMS_JS.read_text()) == ROLE_PERMS


def test_contract_parser_detects_drift():
    # Guard the guard: a parser that silently accepted anything would make the
    # contract test worthless.
    src = PERMS_JS.read_text()
    drifted = re.sub(r"""(['"])award\1""", r"\g<0>, \1sneak\1", src, count=1)
    assert drifted != src, "expected to find an 'award' verb to perturb"
    assert _parse_perms_literal(drifted) != ROLE_PERMS


def test_can_matrix_basics():
    assert can("supplier", "org", "edit")
    assert not can("supplier", "rounds", "manage")
    assert can("procurement_admin", "rfps", "award")
    assert not can("reviewer", "rfps", "award")
    assert not can(None, "org", "view")


@pytest.mark.django_db
def test_resolve_role_supplier_and_staff():
    member = f.SupplierMemberFactory()
    assert roles.resolve_role(member.user) == "supplier"

    staff = f.StaffRoleFactory(role="reviewer")
    assert roles.resolve_role(staff.user) == "reviewer"

    # staff role wins when a user somehow has both
    both = f.SupplierMemberFactory()
    f.StaffRoleFactory(user=both.user, role="procurement_admin")
    both.user.refresh_from_db()
    assert roles.resolve_role(both.user) == "procurement_admin"


@require_perm("rounds", "manage")
def _dummy_view(request):
    return JsonResponse({"ok": True, "role": request.supply_actor.role})


@pytest.mark.django_db
def test_require_perm_401_403_and_pass():
    rf = RequestFactory()

    from django.contrib.auth.models import AnonymousUser

    anon = rf.get("/x/")
    anon.user = AnonymousUser()
    assert _dummy_view(anon).status_code == 401

    supplier = rf.get("/x/")
    supplier.user = f.SupplierMemberFactory().user
    assert _dummy_view(supplier).status_code == 403

    admin = rf.get("/x/")
    admin.user = f.StaffRoleFactory(role="procurement_admin").user
    resp = _dummy_view(admin)
    assert resp.status_code == 200
    assert json.loads(resp.content)["role"] == "procurement_admin"


@pytest.mark.django_db
def test_current_actor_carries_org_for_suppliers_only():
    rf = RequestFactory()
    member = f.SupplierMemberFactory()
    req = rf.get("/x/")
    req.user = member.user
    assert current_actor(req).org == member.org

    staff_req = rf.get("/x/")
    staff_req.user = f.StaffRoleFactory(role="reviewer").user
    assert current_actor(staff_req).org is None
