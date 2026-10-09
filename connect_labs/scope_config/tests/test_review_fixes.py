"""Fixes from the independent review of #2418/#2419/#2422 (2026-10-09)."""

import pytest

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config import service
from connect_labs.scope_config.scopes import Scope
from connect_labs.scope_config.service import Forbidden, Invalid
from connect_labs.scope_config.tests.conftest import caller

pytestmark = pytest.mark.django_db

PROGRAMME = Scope.of("program", 7)
OWNER = Scope.of("organization", "owner-org")
SHARED = Scope.of("organization", "labs-synthetic-shared")


def test_a_labs_only_orgs_layer_is_written_only_by_staff():
    """S1-3: anyone who can see one synthetic opp in the bucket would otherwise restyle every programme in it."""
    member = caller("m", holds=("organization:labs-synthetic-shared", "program:10007"))
    with pytest.raises(Forbidden, match="only Dimagi staff"):
        service.update("t", SHARED, {"a": 1}, member)
    assert service.get("t", SHARED, member)["value"]["a"] == 0  # reading is unchanged
    staff = caller("staff", is_staff=True)
    assert service.update("t", SHARED, {"a": 1}, staff)["value"]["a"] == 1
    with pytest.raises(Forbidden, match="only Dimagi staff"):
        service.undo("t", SHARED, member)


def test_a_labs_only_org_unknown_to_the_tree_is_still_labs_only():
    member = caller("m", holds=("organization:labs-synthetic-elsewhere",))
    with pytest.raises(Forbidden, match="only Dimagi staff"):
        service.update("t", Scope.of("organization", "labs-synthetic-elsewhere"), {"a": 1}, member)


def test_a_real_orgs_members_still_write_its_layer():
    assert service.update("t", OWNER, {"a": 2}, caller("o", holds=("organization:owner-org",)))["value"]["a"] == 2


def test_a_labs_only_orgs_home_page_can_still_be_set_by_staff():
    staff = caller("staff", is_staff=True)
    got = service.update("labs", SHARED, {"home": {"fill": {"workflow": 8, "program_id": 10007}}}, staff)
    assert got["value"]["home"]["fill"]["program_id"] == 10007


def test_a_page_owner_the_writer_cannot_use_is_refused():
    """S1-2 (write side): every viewer of the scope would be served a definition from someone else's programme."""
    member = caller("m", holds=("program:7",))
    with pytest.raises(Forbidden, match="programme 99"):
        service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5, "program_id": 99}}}, member)
    assert service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5, "program_id": 7}}}, member)


def test_an_owner_someone_else_set_does_not_block_unrelated_edits():
    service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5, "program_id": 99}}}, SYSTEM)
    member = caller("m", holds=("program:7",))
    assert service.update("labs", PROGRAMME, {"home": {"label": "Home"}}, member)["value"]["home"]["label"] == "Home"


def test_an_undo_that_would_restore_an_owner_the_undoer_cannot_use_is_refused():
    service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5, "program_id": 99}}}, SYSTEM)
    service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5, "program_id": 7}}}, SYSTEM)
    with pytest.raises(Forbidden):
        service.undo("labs", PROGRAMME, caller("m", holds=("program:7",)))


def test_an_organisation_is_named_by_its_slug_not_its_id():
    with pytest.raises(ValueError, match="slug"):
        Scope.of("organization", "386")
    member = caller("m", holds=("program:7",))
    with pytest.raises(Invalid):
        service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5, "organization_id": "386"}}}, member)
    assert service.scope_in_view({"organization_id": 386}) is None
    assert service.scope_in_view({"organization_id": 386, "organization_slug": "owner-org"}) == OWNER


def test_a_layer_the_caller_cannot_read_shows_in_effect_but_not_its_data_or_author():
    service.update("t", OWNER, {"m": {"x": "from owner"}}, caller("o", holds=("organization:owner-org",)))
    outsider = caller("m", holds=("program:7",))

    got = service.get("t", PROGRAMME, outsider)

    assert got["value"]["m"]["x"] == "from owner" and got["provenance"]["m.x"] == "organisation Owner Org"
    org_layer = next(layer for layer in got["layers"] if layer["scope"]["type"] == "organization")
    assert org_layer["readable"] is False and "data" not in org_layer and "updated_by" not in org_layer
    own = next(layer for layer in got["layers"] if layer["scope"]["type"] == "program")
    assert own["readable"] is True and "data" in own
