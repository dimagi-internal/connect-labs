"""The domain's checks.

Much of what is tested here is what the module REFUSES to do: rank, phrase,
guess who can answer, or report a state that one list call already shows.
Those absences are the contract a client on top of this surface depends on --
if the product started ranking, a client's own ordering would silently fight
it, and if it re-reported states there would be two paths to the same fact.
"""

from datetime import date, timedelta

import pytest

from connect_labs.supply_chain.checks import CATEGORIES, KIND_CATEGORIES, KINDS
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10501
OPP = 10501
TODAY = date.today()


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


@pytest.fixture
def rutf_without_course(da):
    return op(
        da,
        "commodity_upsert",
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton", "base_per_pack": 150},
    )


def _read(da, **payload):
    return op(da, "checks_list", **payload)


class TestShape:
    def test_the_contract_declares_every_kind_and_its_category(self, da, rutf_without_course):
        result = _read(da)
        assert result["kinds"] == KIND_CATEGORIES
        assert set(result["by_kind"]) == set(KINDS), "a kind can be produced but not declared"
        assert set(result["kinds"].values()) <= set(CATEGORIES)

    def test_every_check_carries_a_category_a_subject_an_audience_and_its_age(self, da, rutf_without_course):
        for item in _read(da)["checks"]:
            assert item["kind"] in KINDS
            assert item["category"] in CATEGORIES
            assert item["subject"]["type"] and item["subject"]["id"]
            assert item["audience"] in ("supplier", "partner", "internal")
            assert "days_open" in item

    def test_nothing_is_ranked_or_worded(self, da, rutf_without_course):
        """No priority, no severity, no drafted message. Prioritising and
        phrasing are judgements about what matters today; a client makes
        them, and a hardcoded page fighting the client is worse than
        neither."""
        for item in _read(da)["checks"]:
            assert not {"priority", "severity", "rank", "urgency", "message", "draft"} & set(item)

    def test_no_check_merely_restates_a_state_one_list_call_would_show(self):
        """The boundary that keeps this narrow. "Who has not replied" and
        "what is at customs" are single columns that outreach_list and
        shipment_list already report; a check re-reading them would be a
        second path to the same fact, and would assert a problem where a
        shipment dispatched yesterday is not one."""
        assert "round_awaiting_response" not in KINDS
        assert "shipment_stalled" not in KINDS
        assert "shipment_in_transit" not in KINDS
        assert "supplier_never_approached" not in KINDS

    def test_the_order_is_deterministic_so_two_reads_can_be_diffed(self, da, rutf_without_course):
        first = [(e["kind"], e["subject"]["id"]) for e in _read(da)["checks"]]
        second = [(e["kind"], e["subject"]["id"]) for e in _read(da)["checks"]]
        assert first == second == sorted(first)


class TestOurGapsAreOurs:
    def test_a_commodity_with_no_ration_table_is_an_internal_exception(self, da, rutf_without_course):
        found = [e for e in _read(da)["checks"] if e["kind"] == "commodity_course_undefined"]
        assert len(found) == 1
        assert found[0]["audience"] == "internal", "a supplier was asked about our ration table"
        assert "cost_per_course" in found[0]["facts"]["blocks"]

    def test_setting_the_ration_table_clears_it(self, da, rutf_without_course):
        op(
            da,
            "commodity_upsert",
            data={
                "slug": "rutf",
                "course_definition": {
                    "base_units_per_day": "2",
                    "days_per_course": 75,
                    "base_units_per_course": 150,
                    "source": "programme protocol",
                },
            },
        )
        assert _read(da)["by_kind"]["commodity_course_undefined"] == 0


class TestSourcing:
    def test_silence_and_an_unapproached_supplier_are_not_checks(self, da, rutf_without_course):
        """Both are one column on one row, which a list call already reports.
        A check re-reading them would be a second path to the same fact --
        and "never approached" was a judgement about intent: a register of
        nine suppliers of whom four were ever meant to be contacted is a
        perfectly good register, and the database holds nothing that tells
        that apart from an oversight."""
        supplier = op(da, "supplier_create", data={"name": "Nutri K", "status": "identified"})
        round_ = op(
            da,
            "round_create",
            data={
                "label": "Round 2",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": "rutf", "quantity": "2000", "quantity_unit": "carton"}],
            },
        )
        op(da, "round_open", round_id=round_["id"])
        op(
            da,
            "outreach_log",
            data={
                "round_id": round_["id"],
                "supplier_id": supplier["id"],
                "channel": "manual",
                "sent_on": (TODAY - timedelta(days=3)).isoformat(),
            },
        )

        kinds = {c["kind"] for c in _read(da)["checks"]}
        assert kinds == {"commodity_course_undefined"}

        # The underlying facts are still readable -- just not as checks.
        assert op(da, "outreach_list", round_id=round_["id"])[0]["responded"] is False
        assert op(da, "supplier_list")[0]["status"] == "identified"


class TestFulfilment:
    @pytest.fixture
    def contracted(self, da, rutf_without_course):
        supplier = op(da, "supplier_create", data={"name": "DABS"})
        partner = op(da, "party_upsert", data={"slug": "llo", "name": "Kano partner", "kind": "partner_org"})
        contract = op(
            da,
            "contract_create",
            data={
                "commodity_slug": "rutf",
                "supplier_id": supplier["id"],
                "buyer_of_record": "partner_org",
                "buyer_party_id": partner["id"],
                "source": "partner_reported",
                "quantity": "500",
                "quantity_unit": "carton",
                "unit_price": "52.42",
                "unit_price_unit": "per_pack",
                "duty_relief_claimed": True,
                "duties_basis": "excluded",
                "signed_on": (TODAY - timedelta(days=20)).isoformat(),
            },
        )
        return contract

    def test_a_relief_claimed_without_evidence_is_the_partners_to_answer(self, da, contracted):
        found = [e for e in _read(da)["checks"] if e["kind"] == "duty_relief_unevidenced"]
        assert len(found) == 1
        assert found[0]["audience"] == "partner", "we were asked for the partner's certificate"
        assert found[0]["days_open"] == 20

    def test_a_contract_with_no_purchase_order_reference_is_flagged(self, da, contracted):
        found = [e for e in _read(da)["checks"] if e["kind"] == "contract_reference_unknown"]
        assert len(found) == 1
        assert found[0]["audience"] == "partner"

    def test_a_cost_that_cannot_be_computed_says_why(self, da, contracted):
        found = [e for e in _read(da)["checks"] if e["kind"] == "contract_cost_unconfirmed"]
        assert len(found) == 1
        assert any("evidenced" in reason for reason in found[0]["facts"]["reasons"])

    def test_an_award_with_no_contract_is_flagged_against_its_award_date(self, da, rutf_without_course):
        supplier = op(da, "supplier_create", data={"name": "DABS"})
        round_ = op(
            da,
            "round_create",
            data={
                "label": "Round 1",
                "delivery_point": {"city": "Kano"},
                "lines": [{"commodity_slug": "rutf", "quantity": "500", "quantity_unit": "carton"}],
            },
        )
        quote = op(
            da,
            "quote_record",
            data={
                "round_id": round_["id"],
                "commodity_slug": "rutf",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "52.42",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "500",
                "quantity_basis_unit": "carton",
            },
        )
        op(da, "award_create", round_id=round_["id"], quote_id=quote["id"], rationale="only comparable offer")

        found = [e for e in _read(da)["checks"] if e["kind"] == "award_not_contracted"]
        assert len(found) == 1
        assert found[0]["audience"] == "internal"
        assert found[0]["days_open"] == 0


class TestStock:
    def test_a_worker_who_has_never_reported_is_flagged(self, da, rutf_without_course):
        op(
            da,
            "supply_point_upsert",
            data={
                "slug": "flw-a",
                "name": "Worker A",
                "kind": "user_held",
                "opportunity_id": OPP,
                "connect_username": "flw-a",
                "source": "we_recorded",
            },
        )
        found = [e for e in _read(da, opportunity_id=OPP)["checks"] if e["kind"] == "stock_never_reported"]
        assert len(found) == 1
        assert found[0]["facts"]["connect_username"] == "flw-a"
        assert found[0]["facts"]["holds_stock"] is False

    def test_filtering_by_kind_returns_only_that_kind(self, da, rutf_without_course):
        op(
            da,
            "supply_point_upsert",
            data={
                "slug": "flw-a",
                "name": "Worker A",
                "kind": "user_held",
                "opportunity_id": OPP,
                "connect_username": "flw-a",
                "source": "we_recorded",
            },
        )
        result = _read(da, kinds=["stock_never_reported"])
        assert {e["kind"] for e in result["checks"]} == {"stock_never_reported"}
        assert result["count"] == 1

    def test_a_ledger_and_a_count_that_disagree_is_a_conflict(self, da, rutf_without_course):
        """The disagreement the whole stock design exists to surface. Neither
        side is automatically right, so the gap is reported rather than one
        silently winning."""
        item = op(
            da,
            "item_upsert",
            data={
                "sku": "harmattan",
                "name": "Harmattan RUTF",
                "commodity_slug": "rutf",
                "base_unit": "sachet",
                "pack_unit": "carton",
                "base_per_pack": 144,
            },
        )
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "central", "name": "Central", "kind": "central_store", "source": "we_recorded"},
        )
        worker = op(
            da,
            "supply_point_upsert",
            data={
                "slug": "flw-a",
                "name": "Worker A",
                "kind": "user_held",
                "opportunity_id": OPP,
                "connect_username": "flw-a",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "movement_record",
            data={
                "kind": "receipt",
                "occurred_on": TODAY.isoformat(),
                "commodity_slug": "rutf",
                "item_id": item["id"],
                "to_supply_point_id": store["id"],
                "quantity": "50",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item["id"],
                        "quantity": "12",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        op(
            da,
            "stock_count_record",
            data={
                "supply_point_id": worker["id"],
                "commodity_slug": "rutf",
                "item_id": item["id"],
                "kind": "self_reported",
                "counted_on": TODAY.isoformat(),
                "quantity": "8",
                "quantity_unit": "carton",
                "source": "commcare_form",
            },
        )

        found = [c for c in _read(da, opportunity_id=OPP)["checks"] if c["kind"] == "stock_variance"]
        assert len(found) == 1
        assert found[0]["category"] == "conflict"
        assert found[0]["facts"]["ledger"] == "12"
        assert found[0]["facts"]["reported"] == "8"
        assert found[0]["facts"]["variance"] == "-4"
        assert found[0]["facts"]["reconcilable"] is True

    def test_a_variance_that_cannot_be_reconciled_says_so_rather_than_computing_one(self, da, rutf_without_course):
        """The store counts cartons, the worker counted sachets, and the
        supplier never stated how many sachets are in a carton."""
        item = op(
            da,
            "item_upsert",
            data={
                "sku": "dabs",
                "name": "DABS RUTF",
                "commodity_slug": "rutf",
                "base_unit": "sachet",
                "pack_unit": "carton",
            },
        )
        store = op(
            da,
            "supply_point_upsert",
            data={"slug": "central", "name": "Central", "kind": "central_store", "source": "we_recorded"},
        )
        worker = op(
            da,
            "supply_point_upsert",
            data={
                "slug": "flw-a",
                "name": "Worker A",
                "kind": "user_held",
                "opportunity_id": OPP,
                "connect_username": "flw-a",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "movement_record",
            data={
                "kind": "receipt",
                "occurred_on": TODAY.isoformat(),
                "commodity_slug": "rutf",
                "item_id": item["id"],
                "to_supply_point_id": store["id"],
                "quantity": "50",
                "quantity_unit": "carton",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "distribution_record",
            data={
                "supply_point_id": store["id"],
                "opportunity_id": OPP,
                "commodity_slug": "rutf",
                "distributed_on": TODAY.isoformat(),
                "source": "we_recorded",
                "lines": [
                    {
                        "to_supply_point_id": worker["id"],
                        "item_id": item["id"],
                        "quantity": "12",
                        "quantity_unit": "carton",
                    }
                ],
            },
        )
        op(
            da,
            "stock_count_record",
            data={
                "supply_point_id": worker["id"],
                "commodity_slug": "rutf",
                "item_id": item["id"],
                "kind": "self_reported",
                "counted_on": TODAY.isoformat(),
                "quantity": "1700",
                "quantity_unit": "sachet",
                "source": "commcare_form",
            },
        )

        found = [c for c in _read(da, opportunity_id=OPP)["checks"] if c["kind"] == "stock_variance"]
        assert len(found) == 1
        assert found[0]["facts"]["reconcilable"] is False
        assert "variance" not in found[0]["facts"], "a variance was computed without the pack spec"
        assert any("carton" in r for r in found[0]["facts"]["reasons"])

    def test_filtering_by_category_narrows_to_that_kind_of_finding(self, da, rutf_without_course):
        op(
            da,
            "supply_point_upsert",
            data={
                "slug": "flw-a",
                "name": "Worker A",
                "kind": "user_held",
                "opportunity_id": OPP,
                "connect_username": "flw-a",
                "source": "we_recorded",
            },
        )
        result = _read(da, opportunity_id=OPP, categories=["missing"])
        assert result["count"] > 0
        assert {c["category"] for c in result["checks"]} == {"missing"}

    def test_dispensing_stock_never_issued_is_a_conflict_not_a_stockout(self, da, rutf_without_course):
        """More has left the point than ever arrived, which is not a level to
        replenish -- it is a movement nobody recorded."""
        item = op(
            da,
            "item_upsert",
            data={
                "sku": "harmattan",
                "name": "Harmattan RUTF",
                "commodity_slug": "rutf",
                "base_unit": "sachet",
                "pack_unit": "carton",
                "base_per_pack": 144,
            },
        )
        worker = op(
            da,
            "supply_point_upsert",
            data={
                "slug": "flw-kumbotso",
                "name": "Worker, Kumbotso",
                "kind": "user_held",
                "opportunity_id": OPP,
                "connect_username": "flw-kumbotso",
                "source": "we_recorded",
            },
        )
        op(
            da,
            "movement_record",
            data={
                "kind": "consumption",
                "occurred_on": TODAY.isoformat(),
                "commodity_slug": "rutf",
                "item_id": item["id"],
                "from_supply_point_id": worker["id"],
                "quantity": "460",
                "quantity_unit": "sachet",
                "source": "connect_visit",
            },
        )

        checks = _read(da, opportunity_id=OPP)["checks"]
        kinds = [c["kind"] for c in checks]
        assert "stock_negative" in kinds
        assert "stock_stockout" not in kinds, "a negative balance was reported as a stockout"
        negative = next(c for c in checks if c["kind"] == "stock_negative")
        assert negative["category"] == "conflict"
        assert negative["facts"]["balance"].startswith("-")
