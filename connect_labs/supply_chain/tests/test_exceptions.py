"""The structured exceptions read.

What is tested here is mostly what the module REFUSES to do: rank, phrase, or
guess who can answer. Those absences are the contract an agent on top of this
surface depends on -- if the product started ranking, a client's ordering
would silently fight it.
"""

from datetime import date, timedelta

import pytest

from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.exceptions import KINDS
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
    return op(da, "exceptions_list", **payload)


class TestShape:
    def test_the_contract_declares_every_kind_it_can_produce(self, da, rutf_without_course):
        result = _read(da)
        assert set(result["kinds"]) == set(KINDS)
        assert set(result["by_kind"]) == set(KINDS), "a kind can be produced but not declared"

    def test_every_exception_carries_a_subject_an_audience_and_its_age(self, da, rutf_without_course):
        for item in _read(da)["exceptions"]:
            assert item["kind"] in KINDS
            assert item["subject"]["type"] and item["subject"]["id"]
            assert item["audience"] in ("supplier", "partner", "internal")
            assert "days_open" in item

    def test_nothing_is_ranked_or_worded(self, da, rutf_without_course):
        """No priority, no severity, no drafted message. Prioritising and
        phrasing are judgements about what matters today; a client makes
        them, and a hardcoded page fighting the client is worse than
        neither."""
        for item in _read(da)["exceptions"]:
            assert not {"priority", "severity", "rank", "urgency", "message", "draft"} & set(item)

    def test_the_order_is_deterministic_so_two_reads_can_be_diffed(self, da, rutf_without_course):
        first = [(e["kind"], e["subject"]["id"]) for e in _read(da)["exceptions"]]
        second = [(e["kind"], e["subject"]["id"]) for e in _read(da)["exceptions"]]
        assert first == second == sorted(first)


class TestOurGapsAreOurs:
    def test_a_commodity_with_no_ration_table_is_an_internal_exception(self, da, rutf_without_course):
        found = [e for e in _read(da)["exceptions"] if e["kind"] == "commodity_course_undefined"]
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
    def test_a_silent_supplier_is_an_exception_with_its_age(self, da, rutf_without_course):
        supplier = op(da, "supplier_create", data={"name": "Nutri K"})
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

        found = [e for e in _read(da)["exceptions"] if e["kind"] == "round_awaiting_response"]
        assert len(found) == 1
        assert found[0]["audience"] == "supplier"
        assert found[0]["days_open"] == 3

    def test_a_supplier_nobody_ever_approached_is_flagged_as_ours(self, da, rutf_without_course):
        op(da, "supplier_create", data={"name": "Nutriset", "status": "identified"})
        found = [e for e in _read(da)["exceptions"] if e["kind"] == "supplier_never_approached"]
        assert [e["subject"]["label"] for e in found] == ["Nutriset"]
        assert found[0]["audience"] == "internal"


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
        found = [e for e in _read(da)["exceptions"] if e["kind"] == "duty_relief_unevidenced"]
        assert len(found) == 1
        assert found[0]["audience"] == "partner", "we were asked for the partner's certificate"
        assert found[0]["days_open"] == 20

    def test_a_contract_with_no_purchase_order_reference_is_flagged(self, da, contracted):
        found = [e for e in _read(da)["exceptions"] if e["kind"] == "contract_reference_unknown"]
        assert len(found) == 1
        assert found[0]["audience"] == "partner"

    def test_a_cost_that_cannot_be_computed_says_why(self, da, contracted):
        found = [e for e in _read(da)["exceptions"] if e["kind"] == "contract_cost_unconfirmed"]
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

        found = [e for e in _read(da)["exceptions"] if e["kind"] == "award_not_contracted"]
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
        found = [e for e in _read(da, opportunity_id=OPP)["exceptions"] if e["kind"] == "stock_never_reported"]
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
        assert {e["kind"] for e in result["exceptions"]} == {"stock_never_reported"}
        assert result["count"] == 1
