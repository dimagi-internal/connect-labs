"""Buying chlorine test kits: what the supply-test-kits walkthrough found.

THIS REPOSITORY IS PUBLIC. Every organisation, product and figure here is
invented.

A technical partner specifies free-chlorine test kits (50 tests to the kit, a
measurement range that has to cover the dispenser dose). One distributor
quotes two kits of the same composition; the cheaper one fails the range.
Walking that procurement end to end in the live screens turned up each of the
things pinned below:

- the comparison could not tell two offers from ONE supplier apart -- both
  rows read "Harmattan Health Supplies" -- and did not say which one fails the
  specification, so the cheaper, failing kit sat at the top of the ranking
  with nothing against it;
- it asked for a treatment protocol and priced "per course" and "per child
  treated" for a diagnostic, which has no course -- the checks feed and the
  product page already knew that, the comparison did not;
- an order placed from an award dropped the quote's freight, duties, Incoterm
  and lead time, so the order read as costed on terms nobody agreed;
- an order nothing had arrived against read "part received";
- stock of a kit read only in kits, never in the tests it holds.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation

pytestmark = pytest.mark.django_db

PROGRAM = 10615


@pytest.fixture
def da():
    return SupplyDataAccess(access_token="unused", program_id=PROGRAM, caller=SYSTEM)


def op(da, name, **payload):
    return call_operation(name, da, payload)


COMPONENTS = [
    {"commodity_slug": "dpd1-reagent", "quantity": "50", "base_unit": "tablet"},
    {"commodity_slug": "colour-comparator", "quantity": "1", "base_unit": "unit"},
]


@pytest.fixture
def world(da):
    op(da, "commodity_upsert", data={"slug": "dpd1-reagent", "name": "DPD No. 1 tablet", "base_unit": "tablet"})
    op(da, "commodity_upsert", data={"slug": "colour-comparator", "name": "Comparator", "base_unit": "unit"})
    op(
        da,
        "commodity_upsert",
        data={
            "slug": "chlorine-test-kit",
            "name": "Free chlorine test kit",
            "category": "diagnostic",
            "base_unit": "test",
            "pack_unit": "kit",
            "base_per_pack": 50,
            "spec_requirements": [
                {"field": "tests_per_kit", "operator": ">=", "value": 50, "unit": "tests"},
                {
                    "field": "range_max_mg_per_l",
                    "operator": ">=",
                    "value": 2.0,
                    "unit": "mg/L",
                    "rationale": "must read the 2 mg/L dose",
                },
            ],
        },
    )

    def kit(sku, name, range_max):
        return op(
            da,
            "item_upsert",
            data={
                "sku": sku,
                "name": name,
                "commodity_slug": "chlorine-test-kit",
                "base_unit": "test",
                "pack_unit": "kit",
                "base_per_pack": 50,
                "components": COMPONENTS,
                "spec_attributes": {"tests_per_kit": 50, "range_max_mg_per_l": range_max},
            },
        )

    good = kit("lumen-fc50", "Lumen FC-50 kit", 3.5)
    bad = kit("brightwell-pc50", "Brightwell PoolCheck-50 kit", 1.5)
    supplier = op(da, "supplier_create", data={"name": "Harmattan Health Supplies", "type": "distributor"})
    point = op(
        da,
        "supply_point_upsert",
        data={"slug": "hhs-kano", "name": "Harmattan warehouse", "kind": "central_store", "source": "we_recorded"},
    )
    tender = op(
        da,
        "tender_create",
        data={
            "label": "Chlorine test kits — Q4",
            "delivery_point": {"city": "Kano"},
            "lines": [{"commodity_slug": "chlorine-test-kit", "quantity": "20", "quantity_unit": "kit"}],
        },
    )

    def quote(item, price):
        return op(
            da,
            "quote_record",
            data={
                "tender_id": tender["id"],
                "commodity_slug": "chlorine-test-kit",
                "supplier_id": supplier["id"],
                "item_id": item["id"],
                "as_quoted_amount": price,
                "as_quoted_unit": "per_pack",
                "quantity_basis": "20",
                "quantity_basis_unit": "kit",
                "pack_spec_source": "trade_item_confirmed",
                "freight_basis": "included",
                "duties_basis": "included",
                "incoterm": "DAP",
                "lead_time_days": 21,
            },
        )

    programme = op(da, "org_upsert", data={"slug": "test-kit-programme", "name": "Test-kit programme"})
    return {
        "programme": programme,
        "tender": tender,
        "supplier": supplier,
        "point": point,
        "good": good,
        "bad": bad,
        "good_quote": quote(good, "38.00"),
        "bad_quote": quote(bad, "29.50"),
    }


def _compare(da, world):
    return op(da, "tender_compare", tender_id=world["tender"]["id"], commodity_slug="chlorine-test-kit")


class TestTwoOffersFromOneSupplier:
    def test_each_row_names_the_trade_item_it_offers(self, da, world):
        rows = {row["quote_id"]: row for row in _compare(da, world)["comparable"]}
        assert rows[world["good_quote"]["id"]]["item_name"] == "Lumen FC-50 kit"
        assert rows[world["bad_quote"]["id"]]["item_name"] == "Brightwell PoolCheck-50 kit"

    def test_each_row_says_whether_it_meets_the_specification_and_which_requirement_fails(self, da, world):
        rows = {row["quote_id"]: row for row in _compare(da, world)["comparable"]}
        good = rows[world["good_quote"]["id"]]["specification"]
        bad = rows[world["bad_quote"]["id"]]["specification"]
        assert good == {"outcome": "pass", "summary": "Meets all 2", "failures": []}
        assert bad["outcome"] == "fail"
        assert bad["summary"] == "1 of 2 fail"
        assert bad["failures"] == [
            "Range maximum 1.5 mg/L fails: it must be at least 2.0 mg/L (per the item specification)"
            " — must read the 2 mg/L dose"
        ]

    def test_failing_the_specification_does_not_reorder_the_ranking(self, da, world):
        """The product derives; it does not recommend. The cheaper kit still
        ranks first on price -- the page says it fails, the buyer decides."""
        comparable = _compare(da, world)["comparable"]
        assert [row["quote_id"] for row in comparable] == [world["bad_quote"]["id"], world["good_quote"]["id"]]


class TestADiagnosticHasNoCourse:
    def test_no_per_course_or_per_child_columns(self, da, world):
        comparison = _compare(da, world)
        keys = [column["key"] for column in comparison["columns"]]
        assert "usd_per_course" not in keys
        assert "usd_per_child_treated" not in keys
        assert comparison["unavailable"] == {}

    def test_no_question_asking_for_a_treatment_protocol(self, da, world):
        for row in _compare(da, world)["comparable"]:
            assert "course_definition" not in [q["key"] for q in row["questions"]]

    def test_a_treatment_still_gets_its_course_columns(self, da, world):
        op(da, "commodity_upsert", data={"slug": "chlorine-test-kit", "category": "micronutrient"})
        keys = [column["key"] for column in _compare(da, world)["columns"]]
        assert "usd_per_course" in keys


def _award(da, world, quote):
    return op(
        da,
        "award_create",
        tender_id=world["tender"]["id"],
        quote_id=quote["id"],
        rationale="Reads the dispenser dose",
    )


def _order(da, world, award, **terms):
    return op(
        da,
        "contract_create",
        data={
            "award_id": award["id"],
            "tender_id": world["tender"]["id"],
            "supplier_id": world["supplier"]["id"],
            "commodity_slug": "chlorine-test-kit",
            "item_id": world["good"]["id"],
            "buyer_of_record": "programme_org",
            "buyer_org_id": world["programme"]["id"],
            "status": "placed",
            "quantity": "20",
            "quantity_unit": "kit",
            "unit_price": "38.00",
            "unit_price_unit": "per_pack",
            "source": "we_recorded",
            **terms,
        },
    )


class TestTheApprovalRefusalReadsAsWords:
    def test_it_names_the_supplier_and_the_approver_not_row_ids(self, da, world):
        award = _award(da, world, world["good_quote"])
        partner = op(da, "org_upsert", data={"slug": "aqualytic-test", "name": "Aqualytic"})
        op(
            da,
            "approval_request",
            data={"award_id": award["id"], "approver_org_id": partner["id"], "role": "technical"},
        )
        with pytest.raises(ValueError) as refused:
            _order(da, world, award)
        message = str(refused.value)
        assert "Harmattan Health Supplies" in message
        assert "Aqualytic's technical approval" in message
        assert f"award {award['id']}" not in message


class TestTheOrderPageAndStock:
    def test_an_order_nothing_has_arrived_against_is_not_received(self, da, world):
        order = _order(da, world, _award(da, world, world["good_quote"]))
        match = op(da, "contract_match", contract_id=order["id"])
        assert match["status"] == "not_received"

    def test_a_partial_arrival_is_still_part_received(self, da, world):
        order = _order(da, world, _award(da, world, world["good_quote"]))
        op(
            da,
            "receipt_record",
            data={
                "contract_id": order["id"],
                "supply_point_id": world["point"]["id"],
                "received_on": "2026-10-08",
                "source": "supplier_reported",
                "lines": [{"item_id": world["good"]["id"], "quantity_accepted": "5", "quantity_unit": "kit"}],
            },
        )
        assert op(da, "contract_match", contract_id=order["id"])["status"] == "part_received"

    def test_network_stock_restates_kits_in_the_tests_they_hold(self, da, world):
        order = _order(da, world, _award(da, world, world["good_quote"]))
        op(
            da,
            "receipt_record",
            data={
                "contract_id": order["id"],
                "supply_point_id": world["point"]["id"],
                "received_on": "2026-10-08",
                "source": "supplier_reported",
                "lines": [{"item_id": world["good"]["id"], "quantity_accepted": "20", "quantity_unit": "kit"}],
            },
        )
        point = op(da, "network_stock", item_id=world["good"]["id"])["points"][0]
        assert point["on_hand"] == {"amount": "20", "unit": "kit"}
        assert point["on_hand_in_base"] == {"amount": "1000", "unit": "test"}


# ---- screens --------------------------------------------------------------


@pytest.fixture
def scoped(client, django_user_model, monkeypatch):
    from connect_labs.supply_chain import form_views, fulfilment_views, views  # noqa: F401
    from connect_labs.supply_chain.api_views import _access as real_access
    from connect_labs.supply_chain.procurement import views as procurement_views  # noqa: F401

    account = django_user_model.objects.create_user(username="kits", password="x", email="kits@dimagi.com")
    client.force_login(account)

    def _scoped(request):
        access = real_access(request)
        access.program_id = PROGRAM
        return access

    for module in ("form_views", "views", "fulfilment_views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}._access", _scoped)
    for module in ("form_views", "views", "procurement.views"):
        monkeypatch.setattr(f"connect_labs.supply_chain.{module}.has_program_context", lambda request: True)
    return client


class TestTheScreens:
    def test_the_comparison_names_each_kit_and_says_which_fails(self, scoped, da, world):
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]])
        body = scoped.get(url + "?commodity=chlorine-test-kit").content.decode()
        assert "Lumen FC-50 kit" in body
        assert "Brightwell PoolCheck-50 kit" in body
        assert "Range maximum 1.5 mg/L fails: it must be at least 2.0 mg/L" in body
        assert "range_max_mg_per_l" not in body
        assert "Meets all 2" in body
        assert "treatment protocol" not in body
        assert "USD per course" not in body

    def test_the_comparison_is_titled_by_tender_and_product_not_ids(self, scoped, da, world):
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]])
        body = scoped.get(url + "?commodity=chlorine-test-kit").content.decode()
        assert "Chlorine test kits — Q4" in body
        assert "Free chlorine test kit" in body

    def test_an_order_from_an_award_opens_on_the_quotes_terms(self, scoped, da, world):
        award = _award(da, world, world["good_quote"])
        body = scoped.get(reverse("supply_chain:contract_create") + f"?award={award['id']}").content.decode()
        assert 'name="incoterm" value="DAP"' in body
        assert 'name="promised_lead_time_days" value="21"' in body
        for field in ("freight_basis", "duties_basis"):
            select = body.split(f'name="{field}"', 1)[1].split("</select>", 1)[0]
            assert re.search(r'<option value="included"\s+selected', select), field

    def test_the_stock_page_shows_kits_and_the_tests_they_hold(self, scoped, da, world):
        order = _order(da, world, _award(da, world, world["good_quote"]))
        op(
            da,
            "receipt_record",
            data={
                "contract_id": order["id"],
                "supply_point_id": world["point"]["id"],
                "received_on": "2026-10-08",
                "source": "supplier_reported",
                "lines": [{"item_id": world["good"]["id"], "quantity_accepted": "20", "quantity_unit": "kit"}],
            },
        )
        body = scoped.get(reverse("supply_chain:stock") + f"?item_id={world['good']['id']}").content.decode()
        assert "20 kit" in body
        assert "1000 test" in body or "1,000 tests" in body

    def test_the_award_page_names_the_kit_that_won(self, scoped, da, world):
        award = _award(da, world, world["good_quote"])
        body = scoped.get(reverse("supply_chain:award_detail", args=[award["id"]])).content.decode()
        assert "Lumen FC-50 kit" in body

    def test_an_order_billed_for_exactly_what_arrived_shows_no_over_billing_row(self, scoped, da, world):
        """The red "billed beyond what arrived" row read "0 kit" on every order
        that had nothing over-billed -- a warning colour on a zero."""
        order = _order(da, world, _award(da, world, world["good_quote"]))
        op(
            da,
            "invoice_record",
            data={
                "contract_id": order["id"],
                "amount": "760.00",
                "currency": "USD",
                "quantity_billed": "20",
                "quantity_unit": "kit",
                "source": "supplier_reported",
            },
        )
        page = reverse("supply_chain:order_detail", args=[order["id"]])
        # Billed for twenty, nothing arrived yet: that IS over-billing, and says so.
        assert "Billed beyond what arrived" in scoped.get(page).content.decode()
        op(
            da,
            "receipt_record",
            data={
                "contract_id": order["id"],
                "supply_point_id": world["point"]["id"],
                "received_on": "2026-10-08",
                "source": "supplier_reported",
                "lines": [{"item_id": world["good"]["id"], "quantity_accepted": "20", "quantity_unit": "kit"}],
            },
        )
        assert "Billed beyond what arrived" not in scoped.get(page).content.decode()


# ---- iteration 1 of the walkthrough (2026-09-24) ---------------------------


class TestRequirementsReadAsWords:
    """Every screen printed a requirement as its stored rule --
    "range max mg per l >= 2.0" -- and the checks page dumped the whole
    requirement list as data without saying which one failed."""

    def test_a_requirement_reads_as_a_sentence(self):
        from connect_labs.supply_chain.procurement.services.compliance import requirement_text

        rule = {"field": "range_max_mg_per_l", "operator": ">=", "value": 2.0, "unit": "mg/L"}
        assert requirement_text(rule) == "Range maximum at least 2.0 mg/L"
        assert requirement_text({"field": "tests_per_kit", "operator": ">=", "value": 50, "unit": "tests"}) == (
            "Tests per kit at least 50 tests"
        )

    def test_the_check_names_the_failing_requirement_and_nothing_else(self, da, world):
        check = next(
            c
            for c in op(da, "checks_list")["checks"]
            if c["kind"] == "item_fails_specification" and c["subject"]["id"] == world["bad"]["id"]
        )
        assert check["facts"]["fails"] == [
            "Range maximum 1.5 mg/L; it must be at least 2.0 mg/L — must read the 2 mg/L dose"
        ]
        assert "requirements" not in check["facts"]

    def test_the_product_page_says_each_requirement_in_words(self, scoped, da, world):
        body = scoped.get(reverse("supply_chain:product_detail", args=["chlorine-test-kit"])).content.decode()
        assert "Range maximum at least 2.0 mg/L" in body
        assert "&gt;=" not in body


class TestAPaidInvoice:
    def test_a_paid_invoice_offers_no_second_payment(self, scoped, da, world):
        order = _order(da, world, _award(da, world, world["good_quote"]), payment_terms="advance")
        invoice = op(
            da,
            "invoice_record",
            data={
                "contract_id": order["id"],
                "amount": "760.00",
                "currency": "USD",
                "quantity_billed": "20",
                "quantity_unit": "kit",
                "source": "supplier_reported",
            },
        )
        pay = reverse("supply_chain:payment_record", args=[invoice["id"]])
        page = reverse("supply_chain:order_detail", args=[order["id"]])
        assert pay in scoped.get(page).content.decode()
        op(
            da,
            "payment_record",
            data={
                "invoice_id": invoice["id"],
                "paid_on": "2026-09-24",
                "amount": "760.00",
                "currency": "USD",
                "source": "we_recorded",
            },
        )
        body = scoped.get(page).content.decode()
        assert pay not in body
        assert "Paid in advance" in body
