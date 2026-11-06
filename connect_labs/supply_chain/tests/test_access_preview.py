"""'Preview as partner': what an organisation would see once read enforcement is on, before it is.

THIS REPOSITORY IS PUBLIC. Every name and id is invented; see access_relations_world.py.
"""

import pytest
from django.core.exceptions import PermissionDenied

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config.models import ScopeConfig
from connect_labs.supply_chain import access_preview, identity
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import agent_operations, call_operation
from connect_labs.supply_chain.tests import access_relations_world as worlds

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]


@pytest.fixture
def w():
    return worlds.build()


@pytest.fixture(autouse=True)
def connect_orgs_come_from_the_viewer(monkeypatch):
    monkeypatch.setattr(identity, "_caller_organizations", lambda access: getattr(access, "connect_orgs", None))


def counts(result):
    return {row["model"]: (row["visible"], row["total"]) for row in result["models"]}


def test_it_counts_what_an_organisation_would_see_against_the_programme_total(w):
    result = access_preview.preview(worlds.B, w.org("X").pk)
    assert counts(result)["Quote"] == (1, 2)
    assert counts(result)["Tender"] == (1, 1)
    assert counts(result)["Supplier"] == (1, 2)
    assert counts(result)["Contract"] == (1, 1)
    assert result["org_name"] == "Org X"
    assert result["program_id"] == worlds.B
    assert result["visible"] == sum(v for v, _ in counts(result).values())
    assert result["total"] == sum(t for _, t in counts(result).values())


def test_a_competing_bidder_would_see_only_its_own_quote(w):
    assert counts(access_preview.preview(worlds.B, w.org("W").pk))["Quote"] == (1, 2)


def test_the_organisation_that_runs_the_programme_would_see_all_of_it(w):
    result = access_preview.preview(worlds.B, w.org("Y").pk)
    assert all(visible == total for visible, total in counts(result).values())


def test_an_unrelated_organisation_would_see_none_of_it(w):
    result = access_preview.preview(worlds.C, w.org("N").pk)
    assert result["visible"] == 0 and result["total"] > 0


def test_a_receiver_sees_its_slice_of_the_programme_it_receives_in(w):
    result = counts(access_preview.preview(worlds.C, w.org("X").pk))
    assert result["Contract"] == (1, 2)
    assert result["SupplyPoint"] == (1, 2)
    assert result["Charge"] == (0, 1)


def test_it_says_whether_enforcement_is_on(w):
    assert access_preview.preview(worlds.B, w.org("X").pk)["enforcement_on"] is False
    ScopeConfig.objects.create(
        scope_type="program", scope_key=str(worlds.B), namespace="supply", data={"acl_enforce_reads": True}
    )
    assert access_preview.preview(worlds.B, w.org("X").pk)["enforcement_on"] is True


def test_it_does_not_depend_on_the_switch_for_the_counts(w):
    before = counts(access_preview.preview(worlds.B, w.org("X").pk))
    ScopeConfig.objects.create(
        scope_type="program", scope_key=str(worlds.B), namespace="supply", data={"acl_enforce_reads": True}
    )
    assert counts(access_preview.preview(worlds.B, w.org("X").pk)) == before


def test_an_organisation_that_does_not_exist_is_refused_by_name(w):
    with pytest.raises(ValueError, match="organisation 99999999 does not exist"):
        access_preview.preview(worlds.B, 99999999)


def test_it_changes_nothing(w):
    from connect_labs.supply_chain.models import Quote

    before = list(Quote.objects.values_list("pk", "updated_at"))
    access_preview.preview(worlds.B, w.org("X").pk)
    assert list(Quote.objects.values_list("pk", "updated_at")) == before


def _viewer(program_id, connect_id):
    da = SupplyDataAccess(program_id=program_id, caller=SYSTEM)
    da.caller = None
    da.user = None
    da.connect_orgs = [{"id": connect_id, "slug": f"conn-{connect_id}"}]
    return da


class TestTheMcpReadOperation:
    def test_it_is_offered_as_a_read_tool(self):
        operation = agent_operations()["access_preview"]
        assert not operation.is_write
        assert operation.input_schema["required"] == ["org_id"]

    def test_the_runner_can_run_it(self, w):
        result = call_operation("access_preview", _viewer(worlds.B, 502), {"org_id": w.org("X").pk})
        assert counts(result)["Quote"] == (1, 2)

    def test_a_partner_cannot_ask_what_another_organisation_would_see(self, w):
        with pytest.raises(PermissionDenied):
            call_operation("access_preview", _viewer(worlds.B, 501), {"org_id": w.org("W").pk})

    def test_support_can_run_it(self, w, django_user_model):
        staff = django_user_model.objects.create_user(username="s", email="s@example.org", is_staff=True, password="x")
        da = _viewer(worlds.B, 505)
        da.user = staff
        assert call_operation("access_preview", da, {"org_id": w.org("X").pk})["total"] > 0
