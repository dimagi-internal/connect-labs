"""Dispensing rules: what a visit gives out, editable without a deploy.

THIS REPOSITORY IS PUBLIC. Every name, path and figure here is invented or
copied from an app definition (form paths only; no submission was read).
"""

import json

import jsonschema
import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import IntegrityError, transaction
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.history.models import Revision
from connect_labs.supply_chain.models import DispensingRule
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10512
OPP = 10512

RUTF_LINES = [
    {
        "kind": "stated",
        "paths": [
            "form.rutf_dispensing.rutf_sachets_dispensed",
            "form.visit_1.rutf_dispensing.rutf_sachets_dispensed",
        ],
        "unit": "sachet",
    },
    {
        "kind": "stated",
        "paths": ["form.screening_outcome.rutf_stock_deduction", "form.var.appetite_test_stock_deduction"],
        "unit": "sachet",
    },
]


@pytest.fixture(autouse=True)
def synthetic_opportunity():
    """The rule's opportunity is a registered labs-only one of this programme."""
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    return SyntheticOpportunity.objects.create(
        opportunity_id=OPP, labs_only=True, enabled=True, label="rule tests", gdrive_folder_id="folder-test"
    )


@pytest.fixture
def da():
    return SupplyDataAccess(program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def world(da):
    op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )
    item = op(
        da,
        "item_upsert",
        data={
            "sku": "rutf-150",
            "name": "RUTF 150",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 150,
        },
    )
    store = op(
        da,
        "supply_point_upsert",
        data={"slug": "partner-store", "name": "Partner store", "kind": "regional_store", "source": "we_recorded"},
    )
    worker = op(
        da,
        "supply_point_upsert",
        data={
            "slug": "user-10512-worker-acacia",
            "name": "worker-acacia",
            "kind": "user_held",
            "opportunity_id": OPP,
            "connect_username": "worker-acacia",
            "source": "we_recorded",
        },
    )
    return {"item": item, "store": store, "worker": worker}


def upsert(da, world, **overrides):
    data = {
        "opportunity_id": OPP,
        "item_id": world["item"]["id"],
        "resupply_point_id": world["store"]["id"],
        "active_from": "2026-08-01",
        "lines": RUTF_LINES,
        **overrides,
    }
    return op(da, "dispensing_rule_upsert", data=data)


def test_a_stated_rule_is_kept_and_read_back(da, world):
    rule = upsert(da, world)

    got = op(da, "dispensing_rule_get", rule_id=rule["id"])

    assert got["lines"] == RUTF_LINES
    assert got["unit"] == "sachet"
    assert got["estimated"] is False
    assert got["active_from"] == "2026-08-01"
    assert [r["id"] for r in op(da, "dispensing_rule_list")] == [rule["id"]]


def test_upserting_again_edits_the_same_rule(da, world):
    first = upsert(da, world)
    second = upsert(da, world, active_from="2026-09-01", status="inactive")

    assert second["id"] == first["id"]
    assert DispensingRule.objects.count() == 1
    assert second["status"] == "inactive"
    assert op(da, "dispensing_rule_list") == []
    assert len(op(da, "dispensing_rule_list", include_inactive=True)) == 1


def test_leaving_status_out_of_an_edit_keeps_it(da, world):
    upsert(da, world, status="inactive")
    assert upsert(da, world, active_from="2026-09-01")["status"] == "inactive"


def test_a_protocol_line_marks_the_rule_estimated(da, world):
    rule = upsert(
        da,
        world,
        lines=[
            {
                "kind": "protocol",
                "given_paths": ["form.ors_group.ors_given"],
                "given_values": ["yes"],
                "quantity": 4,
                "unit": "sachet",
            }
        ],
    )
    assert rule["estimated"] is True
    assert rule["lines"][0]["quantity"] == "4"


def test_a_protocol_line_needs_a_quantity(da, world):
    line = {"kind": "protocol", "given_paths": ["form.g"], "given_values": ["yes"], "unit": "sachet"}
    with pytest.raises(jsonschema.ValidationError):
        upsert(da, world, lines=[line])


def test_by_age_is_not_a_line_kind_or_field(da, world):
    line = {
        "kind": "protocol",
        "given_paths": ["form.g"],
        "by_age": [{"from_months": 6, "to_months": 59, "quantity": 1}],
        "age_paths": ["form.age"],
        "unit": "sachet",
    }
    with pytest.raises(jsonschema.ValidationError):
        upsert(da, world, lines=[line])


AMOX_MAP = {"1 tablet every 12 hours (total 10 tablets)": 10, "2 tablets every 12 hours(total 20 tablets)": "20"}


def _tablet_world(da):
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "amox",
            "name": "Amoxicillin DT",
            "base_unit": "tablet",
            "pack_unit": "blister",
            "base_per_pack": 10,
        },
    )
    return op(
        da,
        "item_upsert",
        data={
            "sku": "amox-dt",
            "name": "Amox DT",
            "commodity_slug": "amox",
            "base_unit": "tablet",
            "pack_unit": "blister",
            "base_per_pack": 10,
        },
    )


def test_a_value_map_line_is_kept_estimated_and_normalised(da, world):
    item = _tablet_world(da)
    rule = upsert(
        da,
        world,
        item_id=item["id"],
        lines=[
            {
                "kind": "value_map",
                "paths": ["form.visit_1.dosage_pneumonia", "form.visit_2.dosage_pneumonia"],
                "map": AMOX_MAP,
                "unit": "tablet",
            }
        ],
    )
    assert rule["estimated"] is True
    assert rule["lines"][0]["map"] == {
        "1 tablet every 12 hours (total 10 tablets)": "10",
        "2 tablets every 12 hours(total 20 tablets)": "20",
    }


def test_a_value_map_needs_answers_and_a_path(da, world):
    base = {"kind": "value_map", "paths": ["form.x"], "map": {"a": 1}, "unit": "sachet"}
    with pytest.raises(jsonschema.ValidationError):
        upsert(da, world, lines=[{**base, "map": {}}])
    with pytest.raises(ValueError, match="must start with 'form.'"):
        upsert(da, world, lines=[{**base, "paths": ["x"]}])
    with pytest.raises(jsonschema.ValidationError):
        upsert(da, world, lines=[{**base, "map": {"a": 0}}])


def test_a_line_can_be_limited_to_forms_and_a_protocol_can_require_answers(da, world):
    rule = upsert(
        da,
        world,
        lines=[
            {"kind": "stated", "paths": ["form.a"], "unit": "sachet", "forms": ["Screening"]},
            {
                "kind": "protocol",
                "given_paths": ["form.vita.va_delivered"],
                "given_values": ["child_fine"],
                "requires_paths": ["form.vita.dose_answered"],
                "quantity": 1,
                "unit": "sachet",
                "forms": ["http://openrosa.org/formdesigner/abc", "Visit Form"],
            },
        ],
    )
    assert rule["lines"][0]["forms"] == ["Screening"]
    assert rule["lines"][1]["requires_paths"] == ["form.vita.dose_answered"]
    assert rule["lines"][1]["forms"] == ["http://openrosa.org/formdesigner/abc", "Visit Form"]
    with pytest.raises(ValueError, match="must start with 'form.'"):
        upsert(da, world, lines=[{"kind": "stated", "paths": ["form.a"], "unit": "sachet", "requires_paths": ["a"]}])


def test_the_database_allows_one_rule_per_opportunity_and_item(da, world):
    rule = upsert(da, world)
    stored = DispensingRule.objects.get(pk=rule["id"])
    with pytest.raises(IntegrityError), transaction.atomic():
        DispensingRule.objects.create(
            program_id=stored.program_id,
            opportunity_id=stored.opportunity_id,
            item=stored.item,
            lines=[],
            resupply_point=stored.resupply_point,
            active_from="2026-08-01",
        )


def test_quantities_are_written_without_the_storage_scale(da, world):
    rule = upsert(
        da,
        world,
        lines=[{"kind": "protocol", "given_paths": ["form.g"], "quantity": "100.0000", "unit": "sachet"}],
    )
    assert rule["lines"][0]["quantity"] == "100"


def test_rule_payloads_need_an_opportunity(da, world):
    with pytest.raises(jsonschema.ValidationError):
        op(
            da,
            "dispensing_rule_upsert",
            data={
                "item_id": world["item"]["id"],
                "resupply_point_id": world["store"]["id"],
                "active_from": "2026-08-01",
                "lines": RUTF_LINES,
            },
        )


def test_a_path_outside_form_json_is_refused(da, world):
    with pytest.raises(ValueError, match="must start with 'form.'"):
        upsert(da, world, lines=[{"kind": "stated", "paths": ["rutf_sachets_dispensed"], "unit": "sachet"}])


def test_a_unit_the_item_cannot_count_in_is_refused(da, world):
    with pytest.raises(ValueError, match="no way to count"):
        upsert(da, world, lines=[{"kind": "stated", "paths": ["form.x"], "unit": "bottle"}])


def test_a_line_in_packs_is_accepted_when_the_item_states_its_pack(da, world):
    rule = upsert(da, world, lines=[{"kind": "stated", "paths": ["form.cartons"], "unit": "carton"}])
    assert rule["lines"][0]["unit"] == "carton"


def test_a_worker_cannot_be_the_resupply_point(da, world):
    with pytest.raises(ValueError, match="a store"):
        upsert(da, world, resupply_point_id=world["worker"]["id"])


def test_an_unknown_line_kind_fails_the_schema(da, world):
    with pytest.raises(jsonschema.ValidationError):
        upsert(da, world, lines=[{"kind": "guessed", "paths": ["form.x"], "unit": "sachet"}])


def test_reports_paths_are_validated(da, world):
    rule = upsert(
        da,
        world,
        reports={
            "balance_paths": ["form.var.new_stock_balance"],
            "receipt": {
                "quantity_paths": ["form.current_stock.sachets_received"],
                "date_paths": ["form.current_stock.date_received"],
            },
        },
    )
    assert rule["reports"]["receipt"]["date_paths"] == ["form.current_stock.date_received"]
    with pytest.raises(ValueError, match="must start with 'form.'"):
        upsert(da, world, reports={"balance_paths": ["new_stock_balance"]})


def test_a_rule_is_revisioned(da, world):
    rule = upsert(da, world)
    assert Revision.objects.filter(
        content_type=ContentType.objects.get_for_model(DispensingRule), object_id=str(rule["id"]), action="create"
    ).exists()


# ---- the screen -----------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch, da):
    from connect_labs.supply_chain import form_views, views  # noqa: F401  -- bind before patching
    from connect_labs.supply_chain.stock import visit_views  # noqa: F401

    for module in ("form_views", "views", "stock.visit_views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", lambda request: da)
    client.force_login(django_user_model.objects.create_user(username="grace", password="x"))
    return client


def test_the_rules_page_lists_a_rule(scoped, da, world):
    upsert(da, world)
    body = scoped.get(reverse("supply_chain:dispensing_rules")).content.decode()
    assert "RUTF 150" in body
    assert "form.rutf_dispensing.rutf_sachets_dispensed" in body
    assert "Stated" in body


def test_a_rule_is_created_on_the_form(scoped, world):
    response = scoped.post(
        reverse("supply_chain:dispensing_rule_create"),
        {
            "opportunity_id": str(OPP),
            "item": str(world["item"]["id"]),
            "resupply_point": str(world["store"]["id"]),
            "active_from": "2026-08-01",
            "lines": json.dumps(RUTF_LINES),
            "form_names": "Screening\nVisit Form",
            "reports": "",
            "status": "active",
        },
    )
    assert response.status_code == 302
    rule = DispensingRule.objects.get()
    assert rule.forms == ["Screening", "Visit Form"]


def test_a_refused_rule_comes_back_as_an_error_on_the_form(scoped, world):
    response = scoped.post(
        reverse("supply_chain:dispensing_rule_create"),
        {
            "opportunity_id": str(OPP),
            "item": str(world["item"]["id"]),
            "resupply_point": str(world["store"]["id"]),
            "active_from": "2026-08-01",
            "lines": json.dumps([{"kind": "stated", "paths": ["rutf"], "unit": "sachet"}]),
            "form_names": "",
            "reports": "",
            "status": "active",
        },
    )
    assert response.status_code == 200
    assert "must start with" in response.content.decode()
    assert not DispensingRule.objects.exists()


def test_editing_a_rule_on_the_screen_updates_it_and_never_adds_a_second(scoped, da, world):
    rule = upsert(da, world)
    url = reverse("supply_chain:dispensing_rule_edit", args=[rule["id"]])

    assert scoped.get(url).status_code == 200
    response = scoped.post(
        url,
        {
            "opportunity_id": str(OPP),
            "item": str(world["item"]["id"]),
            "resupply_point": str(world["store"]["id"]),
            "active_from": "2026-09-15",
            "lines": json.dumps(RUTF_LINES[:1]),
            "form_names": "",
            "reports": "",
            "status": "inactive",
        },
    )

    assert response.status_code == 302
    assert DispensingRule.objects.count() == 1
    edited = DispensingRule.objects.get()
    assert edited.pk == rule["id"]
    assert edited.status == "inactive"
    assert len(edited.lines) == 1


def test_the_edit_screen_cannot_move_a_rule_to_another_item(scoped, da, world):
    rule = upsert(da, world)
    other = op(
        da,
        "item_upsert",
        data={
            "sku": "rutf-144",
            "name": "RUTF 144",
            "commodity_slug": "rutf",
            "base_unit": "sachet",
            "pack_unit": "carton",
            "base_per_pack": 144,
        },
    )
    scoped.post(
        reverse("supply_chain:dispensing_rule_edit", args=[rule["id"]]),
        {
            "opportunity_id": "99999",
            "item": str(other["id"]),
            "resupply_point": str(world["store"]["id"]),
            "active_from": "2026-08-01",
            "lines": json.dumps(RUTF_LINES),
            "form_names": "",
            "reports": "",
            "status": "active",
        },
    )
    assert DispensingRule.objects.count() == 1
    assert DispensingRule.objects.get().item_id == world["item"]["id"]
    assert DispensingRule.objects.get().opportunity_id == OPP


# ---- the rule's opportunity is a scope of its own -------------------------


def test_a_rule_naming_a_real_opportunity_is_refused(da, world):
    with pytest.raises(ValueError, match="not a registered labs-only opportunity"):
        upsert(da, world, opportunity_id=2230)
    assert not DispensingRule.objects.exists()


def test_a_rule_naming_another_programmes_labs_only_opportunity_is_refused(da, world):
    from connect_labs.labs.synthetic.models import SyntheticOpportunity

    SyntheticOpportunity.objects.create(
        opportunity_id=10999, labs_only=True, enabled=True, label="someone else's", gdrive_folder_id="f"
    )
    with pytest.raises(ValueError, match="belongs to programme 10999"):
        upsert(da, world, opportunity_id=10999)
    assert not DispensingRule.objects.exists()


def test_a_rule_naming_an_opportunity_the_caller_does_not_hold_is_refused(da, world, monkeypatch):
    from django.core.exceptions import PermissionDenied

    from connect_labs.labs.access import scopes as access_scopes
    from connect_labs.labs.access.scopes import Caller

    monkeypatch.setattr(
        access_scopes,
        "holdings",
        lambda caller: access_scopes.Holdings(
            org_slugs=frozenset(), opportunity_ids=frozenset(), program_ids=frozenset({PROGRAM})
        ),
    )
    da.caller = Caller(access_token="token")
    with pytest.raises(PermissionDenied, match=f"opportunity {OPP} is not accessible"):
        upsert(da, world)
    assert not DispensingRule.objects.exists()


# ---- a protocol line that could never match -------------------------------

EMPTY_GIVEN = [{"kind": "protocol", "given_paths": ["form.g"], "given_values": [], "quantity": "4", "unit": "sachet"}]


def test_an_empty_given_values_fails_the_schema(da, world):
    with pytest.raises(jsonschema.ValidationError):
        upsert(da, world, lines=EMPTY_GIVEN)
    assert not DispensingRule.objects.exists()


def test_an_empty_given_values_is_refused_by_the_line_validator(world):
    from connect_labs.supply_chain.models import Item
    from connect_labs.supply_chain.stock.services.dispensing import validate_lines

    with pytest.raises(ValueError, match="given_values is empty"):
        validate_lines(EMPTY_GIVEN, Item.objects.get(pk=world["item"]["id"]))


def test_an_empty_given_values_comes_back_as_an_error_on_the_form(scoped, world):
    response = scoped.post(
        reverse("supply_chain:dispensing_rule_create"),
        {
            "opportunity_id": str(OPP),
            "item": str(world["item"]["id"]),
            "resupply_point": str(world["store"]["id"]),
            "active_from": "2026-08-01",
            "lines": json.dumps(EMPTY_GIVEN),
            "form_names": "",
            "reports": "",
            "status": "active",
        },
    )
    assert response.status_code == 200
    assert not DispensingRule.objects.exists()
