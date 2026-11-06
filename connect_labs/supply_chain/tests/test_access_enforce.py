"""Phase 2: read enforcement for a partner, per programme, behind `acl_enforce_reads` (default off).

THIS REPOSITORY IS PUBLIC. Every name and id is invented; see access_relations_world.py.

    X  runs A, bids on B (invited), receives on C.    Y  runs B.    Z  runs C.
    W  competing supplier, payee.    N  nothing.
"""

import logging

import pytest
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.scope_config.models import ScopeConfig
from connect_labs.supply_chain import access_enforce, access_shadow, identity, models
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests import access_relations_world as worlds

pytestmark = [pytest.mark.django_db, pytest.mark.real_standing]

CONNECT = {"X": 501, "Y": 502, "Z": 503, "W": 504, "N": 505}


@pytest.fixture
def w():
    world = worlds.build()
    tender_b = world.rows["tender_b"]
    world.rows["outreach_bx"] = models.Outreach.objects.create(tender=tender_b, supplier=world.rows["supplier_bx"])
    world.rows["outreach_bw"] = models.Outreach.objects.create(tender=tender_b, supplier=world.rows["supplier_bw"])
    return world


@pytest.fixture(autouse=True)
def connect_orgs_come_from_the_viewer(monkeypatch):
    monkeypatch.setattr(identity, "_caller_organizations", lambda access: getattr(access, "connect_orgs", None))


def turn_on(*programmes, value=True):
    for program_id in programmes:
        ScopeConfig.objects.update_or_create(
            scope_type="program",
            scope_key=str(program_id),
            namespace="supply",
            defaults={"data": {"acl_enforce_reads": value}},
        )


def viewer(program_id, who):
    """A real SupplyDataAccess for a caller who is neither the system nor, unless `who` runs it, the programme."""
    da = SupplyDataAccess(program_id=program_id, caller=SYSTEM)
    da.caller = None
    da.user = None
    da.connect_orgs = [{"id": CONNECT[who], "slug": f"conn-{CONNECT[who]}"}] if who else None
    return da


def ids(name, da, **payload):
    result = call_operation(name, da, payload)
    if result is None:
        return None
    if isinstance(result, dict):
        return (result.get("quote") or result)["id"]
    return {row["id"] for row in result}


def keyed(w, *keys):
    return {w.rows[k].pk for k in keys}


class TestTheSwitchDefaultsOff:
    def test_nothing_is_set_so_nothing_changes(self, w):
        assert not access_enforce.switch_on(worlds.B)
        assert ids("quote_list", viewer(worlds.B, "X")) == keyed(w, "quote_bx", "quote_bw")

    def test_explicitly_false_is_off(self, w):
        turn_on(worlds.B, value=False)
        assert ids("quote_list", viewer(worlds.B, "X")) == keyed(w, "quote_bx", "quote_bw")

    def test_it_is_per_programme(self, w):
        turn_on(worlds.B)
        assert ids("quote_list", viewer(worlds.B, "X")) == keyed(w, "quote_bx")
        assert ids("contract_list", viewer(worlds.C, "X")) == keyed(w, "contract_cx", "contract_cz")

    def test_only_a_real_true_turns_it_on(self, w):
        for odd in ("true", 1, "yes", None):
            ScopeConfig.objects.update_or_create(
                scope_type="program",
                scope_key=str(worlds.B),
                namespace="supply",
                defaults={"data": {"acl_enforce_reads": odd}},
            )
            assert not access_enforce.switch_on(worlds.B), odd

    def test_an_organisation_layer_value_does_not_switch_a_programme_on(self, w):
        ScopeConfig.objects.create(
            scope_type="organization", scope_key="acl-y", namespace="supply", data={"acl_enforce_reads": True}
        )
        assert not access_enforce.switch_on(worlds.B)

    def test_the_setting_is_in_the_supply_schema(self):
        from connect_labs.supply_chain.config import SCHEMA

        assert SCHEMA["properties"]["acl_enforce_reads"] == {"type": "boolean"}

    def test_the_setting_is_accepted_by_the_settings_service(self):
        from jsonschema import Draft202012Validator

        from connect_labs.supply_chain.config import SCHEMA

        Draft202012Validator(SCHEMA).validate({"acl_enforce_reads": True})

    def test_an_unreadable_switch_is_off_not_an_empty_programme(self, w, monkeypatch):
        def boom(*args, **kwargs):
            raise RuntimeError("settings store is down")

        monkeypatch.setattr(ScopeConfig.objects, "filter", boom)
        assert access_enforce.switch_on(worlds.B) is False

    def test_off_is_byte_identical_for_a_partner_and_logs_the_shadow_as_before(self, w, settings, caplog):
        settings.SUPPLY_ACL_SHADOW = True
        settings.SUPPLY_ACL_SHADOW_SAMPLE = 1
        caplog.set_level(logging.INFO, logger=access_shadow.logger.name)
        da = viewer(worlds.B, "X")
        call_operation("quote_list", da, {})
        lines = [r.getMessage() for r in caplog.records if r.name == access_shadow.logger.name]
        assert lines == [
            f"supply acl shadow: op=quote_list model=Quote standing=partner would_hide=1 program={worlds.B}"
        ]


class TestOnForAPartnerIsScoped:
    @pytest.fixture(autouse=True)
    def on(self, w):
        turn_on(worlds.A, worlds.B, worlds.C)

    def test_a_bidder_sees_only_its_own_bid_never_a_competitors(self, w):
        assert ids("quote_list", viewer(worlds.B, "X")) == keyed(w, "quote_bx")
        assert ids("quote_list", viewer(worlds.B, "W")) == keyed(w, "quote_bw")

    def test_a_competitors_quote_is_not_there_by_id_either(self, w):
        assert ids("quote_get", viewer(worlds.B, "X"), quote_id=w.rows["quote_bw"].pk) is None
        assert ids("quote_get", viewer(worlds.B, "X"), quote_id=w.rows["quote_bx"].pk) == w.rows["quote_bx"].pk

    def test_a_bidder_sees_the_tender_it_was_invited_to_its_own_supplier_row_and_award(self, w):
        x = viewer(worlds.B, "X")
        assert ids("tender_list", x) == keyed(w, "tender_b")
        assert ids("supplier_list", x) == keyed(w, "supplier_bx")
        assert ids("award_list", x) == keyed(w, "award_b")
        assert ids("contract_list", x) == keyed(w, "contract_b")
        assert ids("outreach_list", x) == keyed(w, "outreach_bx")

    def test_the_competitor_sees_none_of_that(self, w):
        wv = viewer(worlds.B, "W")
        assert ids("tender_list", wv) == set()
        assert ids("supplier_list", wv) == keyed(w, "supplier_bw")
        assert ids("award_list", wv) == set()
        assert ids("contract_list", wv) == set()
        assert ids("outreach_list", wv) == keyed(w, "outreach_bw")

    def test_an_unrelated_organisation_sees_nothing_anywhere(self, w):
        for program in worlds.PROGRAMMES:
            n = viewer(program, "N")
            for name in access_shadow.READ_OPERATION_MODELS:
                if name.endswith("_get"):
                    continue
                assert ids(name, n) == set(), (program, name)

    def test_the_approver_sees_the_approval_and_not_the_prices_around_it(self, w):
        z = viewer(worlds.B, "Z")
        assert ids("approval_list", z) == keyed(w, "approval_b")
        assert ids("quote_list", z) == set()

    def test_the_receiver_sees_its_order_its_store_and_its_ledger_but_not_the_other_order(self, w):
        x = viewer(worlds.C, "X")
        assert ids("contract_list", x) == keyed(w, "contract_cx")
        assert ids("shipment_list", x) == keyed(w, "shipment_cx")
        assert ids("receipt_list", x) == keyed(w, "receipt_cx")
        assert ids("supply_point_list", x) == keyed(w, "store_x")
        assert ids("movement_list", x) == keyed(w, "movement_cx")
        assert ids("charge_list", x) == set()
        assert ids("invoice_list", x) == set()
        assert ids("commitment_list", x) == set()
        assert ids("contract_get", x, contract_id=w.rows["contract_cz"].pk) is None
        assert ids("supply_point_get", x, supply_point_id=w.rows["store_z"].pk) is None
        assert ids("shipment_get", x, shipment_id=w.rows["shipment_cz"].pk) is None

    def test_the_payee_sees_its_charge_and_the_commitment_owed_to_it_and_the_supplier_its_invoice(self, w):
        wv = viewer(worlds.C, "W")
        assert ids("charge_list", wv) == keyed(w, "charge_cz")
        assert ids("commitment_list", wv) == keyed(w, "commitment_cw")
        assert ids("invoice_list", wv) == keyed(w, "invoice_cz")

    def test_the_supplier_does_not_see_the_charge_of_an_order_it_supplies_unless_it_is_the_payee(self, w):
        # W is the supplier of C and also the payee of charge_cz; a charge to somebody else would be hidden from it
        other = models.Charge.objects.create(
            shipment=w.rows["shipment_cz"], kind="freight", payee_org=w.org("N"), amount=1, source="we_recorded"
        )
        assert other.pk not in ids("charge_list", viewer(worlds.C, "W"))

    def test_an_organisation_that_runs_one_programme_bids_on_another_and_receives_on_a_third(self, w):
        assert ids("quote_list", viewer(worlds.A, "X")) == keyed(w, "quote_a")  # runs A: all of it
        assert ids("tender_list", viewer(worlds.A, "X")) == keyed(w, "tender_a")
        assert ids("quote_list", viewer(worlds.B, "X")) == keyed(w, "quote_bx")  # bidder in B: own bid only
        assert ids("contract_list", viewer(worlds.C, "X")) == keyed(w, "contract_cx")  # receiver in C: its order only
        assert ids("quote_list", viewer(worlds.C, "X")) == set()

    def test_the_partner_a_named_order_is_for_sees_it(self, w):
        contract = w.rows["contract_cz"]
        contract.partner_org = w.org("N")
        contract.save(update_fields=["partner_org"])
        n = viewer(worlds.C, "N")
        assert ids("contract_list", n) == {contract.pk}
        assert ids("shipment_list", n) == keyed(w, "shipment_cz")

    def test_a_workers_point_below_the_partners_store_is_the_partners(self, w):
        worker = models.SupplyPoint.objects.create(
            program_id=worlds.C, name="W1", slug="w1", kind="user_held", parent=w.rows["store_x"], source="we_recorded"
        )
        assert worker.pk in ids("supply_point_list", viewer(worlds.C, "X"))
        assert worker.pk not in ids("supply_point_list", viewer(worlds.C, "W"))

    def test_the_runner_of_the_programme_sees_everything_even_through_enforcement(self, w):
        y = viewer(worlds.B, "Y")
        assert ids("quote_list", y) == keyed(w, "quote_bx", "quote_bw")
        assert ids("tender_list", y) == keyed(w, "tender_b")

    def test_an_unknown_viewer_gets_nothing(self, w):
        nobody = viewer(worlds.B, None)
        for name in ("tender_list", "quote_list", "contract_list", "supply_point_list", "movement_list"):
            assert ids(name, nobody) == set(), name

    def test_a_viewer_whose_organisations_cannot_be_read_gets_nothing(self, w, monkeypatch):
        def boom(access):
            raise RuntimeError("connect is down")

        monkeypatch.setattr(identity, "_caller_organizations", boom)
        assert ids("quote_list", viewer(worlds.B, "X")) == set()

    def test_a_standing_that_cannot_be_read_is_a_partners(self, w, monkeypatch):
        from connect_labs.supply_chain import runner

        def boom(access):
            raise RuntimeError("no")

        monkeypatch.setattr(runner, "standing", boom)
        monkeypatch.setattr(runner, "is_system", lambda access: False)
        da = viewer(worlds.B, "X")
        assert access_enforce.enforcing(da) is True

    def test_only_the_operations_own_table_is_scoped_so_a_handler_never_loses_what_it_looks_up(self, w):
        # quote_get dereferences the quote's tender; X sees its quote and (invited) the tender, but a
        # bidder that is NOT invited sees its own quote and must still get an answer, not a crash.
        w.rows["tender_b"].invited_orgs.remove(w.org("X"))
        detail = call_operation("quote_get", viewer(worlds.B, "X"), {"quote_id": w.rows["quote_bx"].pk})
        assert detail["quote"]["id"] == w.rows["quote_bx"].pk

    def test_the_write_path_is_untouched(self, w):
        da = viewer(worlds.B, "X")
        with pytest.raises(Exception) as caught:
            call_operation("tender_close", da, {"tender_id": w.rows["tender_b"].pk})
        assert "can change it" in str(caught.value)


class TestTheProgrammeAndSupportGetByteIdenticalQuerysets:
    @pytest.fixture(autouse=True)
    def on(self, w):
        turn_on(worlds.A, worlds.B, worlds.C)

    MODELS = [
        models.Tender,
        models.Supplier,
        models.Quote,
        models.Award,
        models.AwardApproval,
        models.Contract,
        models.Shipment,
        models.Charge,
        models.Receipt,
        models.Invoice,
        models.Commitment,
        models.Outreach,
        models.SupplyPoint,
        models.Movement,
    ]

    @pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
    def test_the_runner_gets_the_very_queryset_it_asked_for(self, w, model):
        da = viewer(worlds.B, "Y")
        base = model.objects.all()
        with access_enforce.scoped_reads(da, model):
            assert access_enforce.restrict(da, base, model) is base
            assert str(access_enforce.restrict(da, base, model).query) == str(base.query)

    @pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
    def test_support_gets_the_very_queryset_it_asked_for(self, w, django_user_model, model):
        staff = django_user_model.objects.create_user(
            username="support", email="s@example.org", is_staff=True, password="x"
        )
        da = viewer(worlds.B, None)
        da.user = staff
        base = model.objects.all()
        with access_enforce.scoped_reads(da, model):
            assert access_enforce.restrict(da, base, model) is base

    @pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
    def test_the_system_caller_gets_the_very_queryset_it_asked_for(self, w, model):
        da = SupplyDataAccess(program_id=worlds.B, caller=SYSTEM)
        base = model.objects.all()
        with access_enforce.scoped_reads(da, model):
            assert access_enforce.restrict(da, base, model) is base

    @pytest.mark.parametrize("model", MODELS, ids=lambda m: m.__name__)
    def test_a_partner_is_restricted_and_only_inside_a_scoped_read(self, w, model):
        da = viewer(worlds.B, "X")
        base = model.objects.all()
        assert access_enforce.restrict(da, base, model) is base  # no scope open: untouched
        with access_enforce.scoped_reads(da, model):
            restricted = access_enforce.restrict(da, base, model)
        assert restricted is not base and "IN (SELECT" in str(restricted.query)

    def test_a_scope_open_for_one_table_leaves_the_others_alone(self, w):
        da = viewer(worlds.B, "X")
        base = models.Tender.objects.all()
        with access_enforce.scoped_reads(da, models.Quote):
            assert access_enforce.restrict(da, base, models.Tender) is base

    def test_the_runners_operations_run_the_same_sql_with_the_switch_on_or_off(self, w):
        y = viewer(worlds.B, "Y")
        turn_on(worlds.B, value=False)
        with CaptureQueriesContext(connection) as off:
            call_operation("quote_list", y, {})
        y2 = viewer(worlds.B, "Y")
        turn_on(worlds.B)
        with CaptureQueriesContext(connection) as on:
            call_operation("quote_list", y2, {})

        def quote_sql(ctx):
            return [q["sql"] for q in ctx.captured_queries if 'FROM "supply_chain_quote"' in q["sql"]]

        assert quote_sql(on) == quote_sql(off) and quote_sql(on)

    def test_every_programme_standing_caller_gets_results_identical_to_switch_off(self, w):
        for name in access_shadow.READ_OPERATION_MODELS:
            if name.endswith("_get"):
                continue
            turn_on(worlds.B, value=False)
            off = call_operation(name, viewer(worlds.B, "Y"), {})
            turn_on(worlds.B)
            on = call_operation(name, viewer(worlds.B, "Y"), {})
            assert on == off, name


class TestTheShadowKeepsRunningForWhatIsNotEnforced:
    def test_nothing_is_logged_for_a_read_that_is_enforced_for_real(self, w, settings, caplog):
        settings.SUPPLY_ACL_SHADOW = True
        settings.SUPPLY_ACL_SHADOW_SAMPLE = 1
        caplog.set_level(logging.INFO, logger=access_shadow.logger.name)
        turn_on(worlds.B)
        call_operation("quote_list", viewer(worlds.B, "X"), {})
        assert [r for r in caplog.records if r.name == access_shadow.logger.name] == []

    def test_a_programme_that_has_not_switched_on_is_still_observed(self, w, settings, caplog):
        settings.SUPPLY_ACL_SHADOW = True
        settings.SUPPLY_ACL_SHADOW_SAMPLE = 1
        caplog.set_level(logging.INFO, logger=access_shadow.logger.name)
        turn_on(worlds.A)
        call_operation("quote_list", viewer(worlds.B, "X"), {})
        assert len([r for r in caplog.records if r.name == access_shadow.logger.name]) == 1


def test_every_enforced_operation_is_a_read_of_a_modelled_table():
    from connect_labs.supply_chain import access_relations
    from connect_labs.supply_chain.operations import all_operations

    for name in access_shadow.READ_OPERATION_MODELS:
        assert not all_operations()[name].is_write, name
        assert access_relations.modelled(access_enforce.enforced_model(name)), name
