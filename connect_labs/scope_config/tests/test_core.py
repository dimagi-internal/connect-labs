"""The config core: layering, who may change what, versions, history and undo."""

import pytest

from connect_labs.scope_config import service
from connect_labs.scope_config.merge import merge_patch, resolve_layers
from connect_labs.scope_config.models import ScopeConfig, ScopeConfigChange
from connect_labs.scope_config.scopes import Scope, chain_for
from connect_labs.scope_config.service import Forbidden, Invalid, VersionConflict
from connect_labs.scope_config.tests.conftest import TREE, caller

pytestmark = pytest.mark.django_db

PROGRAMME = Scope.of("program", 7)
OPP = Scope.of("opportunity", 70)
OWNER = Scope.of("organization", "owner-org")


def test_merge_patch_maps_merge_lists_replace_null_removes():
    target = {"a": {"x": 1, "y": 2}, "l": [1, 2]}
    assert merge_patch(target, {"a": {"y": None, "z": 3}, "l": [9]}) == {"a": {"x": 1, "z": 3}, "l": [9]}
    assert target == {"a": {"x": 1, "y": 2}, "l": [1, 2]}


def test_provenance_names_the_layer_that_set_each_leaf():
    value, provenance = resolve_layers(
        {"a": 0, "m": {"x": "d", "y": "d"}},
        [("organisation O", {"m": {"x": "org"}}), ("programme P", {"a": 5, "m": {"y": None}})],
    )
    assert value == {"a": 5, "m": {"x": "org"}}
    assert provenance == {"a": "programme P", "m.x": "organisation O"}


def test_an_opportunity_in_another_orgs_programme_reads_the_owners_org_layer():
    """Decision A: the delivering org (llo-org) never restyles a programme it does not run."""
    assert [str(s) for s in chain_for(OPP, TREE)] == ["organization:owner-org", "program:7", "opportunity:70"]


def test_an_opportunity_with_no_programme_reads_its_own_org():
    assert [str(s) for s in chain_for(Scope.of("opportunity", 80), TREE)] == [
        "organization:llo-org",
        "opportunity:80",
    ]


def test_a_value_resolves_through_the_chain_with_its_provenance():
    org_member = caller("o", holds=("organization:owner-org",))
    member = caller("m", holds=("program:7", "opportunity:70"))
    service.update("t", OWNER, {"m": {"x": "from owner"}}, org_member)
    service.update("t", PROGRAMME, {"a": 1}, member)

    got = service.get("t", OPP, member)

    assert got["value"] == {"a": 1, "m": {"x": "from owner"}}
    assert got["provenance"] == {"a": "programme Programme Seven", "m.x": "organisation Owner Org"}
    assert got["data"] == {} and got["version"] == 0


def test_a_programme_member_outside_the_owning_org_changes_the_programme_not_the_org():
    member = caller("m", holds=("program:7",))
    service.update("t", PROGRAMME, {"a": 2}, member)
    with pytest.raises(Forbidden):
        service.update("t", OWNER, {"a": 3}, member)


def test_a_non_member_neither_reads_nor_writes():
    stranger = caller("s", holds=())
    with pytest.raises(Forbidden):
        service.get("t", PROGRAMME, stranger)
    with pytest.raises(Forbidden):
        service.update("t", PROGRAMME, {"a": 1}, stranger)
    assert not ScopeConfig.objects.exists()


def test_staff_change_a_programme_they_do_not_hold():
    staff = caller("staff", holds=(), is_staff=True)
    assert service.update("t", PROGRAMME, {"a": 4}, staff)["value"]["a"] == 4


def test_unknowable_holdings_refuse_with_their_own_message():
    blip = caller("b", holds=None)
    with pytest.raises(Forbidden, match="cannot establish"):
        service.get("t", PROGRAMME, blip)


def test_a_person_changes_only_their_own_layer():
    register_user_namespace()
    alice = caller("alice")
    service.update("u", Scope.of("user", "alice"), {"a": 1}, alice)
    with pytest.raises(Forbidden):
        service.update("u", Scope.of("user", "bob"), {"a": 1}, alice)


def register_user_namespace():
    from connect_labs.scope_config.namespaces import Namespace, register_namespace

    register_namespace(Namespace(key="u", label="U", description="", schema={"type": "object"}, layers={"user"}))


def test_a_layer_the_namespace_does_not_allow_is_refused():
    member = caller("m", holds=("program:7",))
    with pytest.raises(Invalid, match="cannot be set for a user"):
        service.update("t", Scope.of("user", "m"), {"a": 1}, member)


def test_a_stale_version_is_refused_and_nothing_is_written():
    member = caller("m", holds=("program:7",))
    service.update("t", PROGRAMME, {"a": 1}, member)
    with pytest.raises(VersionConflict):
        service.update("t", PROGRAMME, {"a": 2}, member, expected_version=0)
    assert ScopeConfig.objects.get().data == {"a": 1}
    assert ScopeConfigChange.objects.count() == 1


def test_an_invalid_layer_is_refused_with_the_schema_message():
    from connect_labs.scope_config.namespaces import Namespace, register_namespace

    register_namespace(
        Namespace(
            key="typed",
            label="Typed",
            description="",
            schema={"type": "object", "properties": {"n": {"type": "integer"}}},
            layers={"program"},
        )
    )
    with pytest.raises(Invalid, match="n:"):
        service.update("typed", PROGRAMME, {"n": "seven"}, caller("m", holds=("program:7",)))


def test_one_change_row_per_write_and_none_for_a_no_op():
    member = caller("m", holds=("program:7",))
    service.update("t", PROGRAMME, {"a": 1}, member, via="mcp:labs_config_set")
    service.update("t", PROGRAMME, {"a": 1}, member)
    service.update("t", PROGRAMME, {"a": 2}, member)
    history = service.history("t", PROGRAMME, member)
    assert [(h["version"], h["before"], h["after"]) for h in history] == [(2, {"a": 1}, {"a": 2}), (1, {}, {"a": 1})]
    assert history[-1]["via"] == "mcp:labs_config_set" and history[-1]["changed_by"] == "m"


def test_undo_restores_before_as_a_new_change_and_can_itself_be_undone():
    member = caller("m", holds=("program:7",))
    service.update("t", PROGRAMME, {"a": 1}, member)
    service.update("t", PROGRAMME, {"a": 2}, member)

    assert service.undo("t", PROGRAMME, member)["data"] == {"a": 1}
    assert service.undo("t", PROGRAMME, member)["data"] == {"a": 2}
    assert ScopeConfig.objects.get().version == 4


def test_undo_with_nothing_to_undo_says_so():
    with pytest.raises(Invalid, match="no change to undo"):
        service.undo("t", PROGRAMME, caller("m", holds=("program:7",)))


def test_the_labs_home_slot_names_exactly_one_owner():
    member = caller("m", holds=("program:7",))
    with pytest.raises(Invalid, match="exactly one"):
        service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5}}}, member)
    got = service.update("labs", PROGRAMME, {"home": {"fill": {"workflow": 5, "program_id": 7}}}, member)
    assert got["value"]["home"]["fill"] == {"workflow": 5, "program_id": 7}
