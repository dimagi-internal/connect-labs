"""The checks page: facts as labelled words, and what each check's age counts from.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on chlorine_world.
"""

import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import (
    op,
)

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


def _late_delivery(**facts):
    """A `contract_delivery_overdue` check as `checks_list` returns it."""
    check = {
        "kind": "contract_delivery_overdue",
        "category": "threshold",
        "subject": {"type": "contract", "id": 128, "label": "A donor — Chlorine"},
        "audience": "supplier",
        "since": "2026-06-26",
        "days_open": 90,
        "facts": {
            "days_late": 90,
            "expected_on": "2026-06-26",
            "signed_on": "2026-04-27",
            "promised_lead_time_days": 60,
            "supplier": {"id": 4, "name": "A donor"},
            "status": "placed",
            "outstanding": "400",
            "unit": "jerry_can",
        },
    }
    check["facts"].update(facts)
    return check


class TestCheckFactsReadAsWords:
    """Each check card dumped its facts as raw keys -- "promised lead time days",
    "contract id 128", "unit jerry can" on a row of its own -- and repeated the
    header's "90 days since 2026-06-26" as "days late 90" and "expected on"."""

    def test_a_quantity_reads_with_its_unit_and_a_lead_time_in_days(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        rows = dict(check_facts(_late_delivery()))
        assert rows["Promised lead time"] == "60 days"
        assert rows["Outstanding"] == "400 jerry cans"
        assert rows["Signed"] == "2026-04-27"
        assert rows["Status"] == "placed"

    def test_what_the_header_already_says_is_not_said_again(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        labels = {label for label, _ in check_facts(_late_delivery())}
        # The age and its date are the header's; the supplier is in the label.
        assert not {"Days late", "Expected on", "Supplier", "Unit"} & labels
        assert all("_" not in label and label[0].isupper() for label in labels)

    def test_a_supplier_the_label_does_not_name_is_kept(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        check = _late_delivery()
        check["subject"]["label"] = "DON-1 — Chlorine"
        assert dict(check_facts(check))["Supplier"] == "A donor"

    def test_ids_the_card_already_links_are_dropped(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        check = {
            "kind": "shipment_overdue",
            "subject": {"type": "shipment", "id": 9, "label": "SHIP-1 — Chlorine — A donor"},
            "since": "2026-06-26",
            "days_open": 90,
            "facts": {
                "days_late": 90,
                "expected_on": "2026-06-26",
                "supplier": {"id": 4, "name": "A donor"},
                "status": "in_transit",
                "contract_id": 128,
            },
        }
        assert check_facts(check) == [("Status", "in transit")]

    def test_money_reads_with_its_currency_and_the_payment_date_is_the_header(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        check = {
            "kind": "payment_unconfirmed",
            "subject": {"type": "payment", "id": 3, "label": "A donor — PAY-1"},
            "since": "2026-08-01",
            "days_open": 54,
            "facts": {
                "amount": "1000",
                "currency": "USD",
                "paid_on": "2026-08-01",
                "invoice_id": 7,
                "contract_id": 128,
                "supplier": {"id": 4, "name": "A donor"},
            },
        }
        assert check_facts(check) == [("Amount", "USD 1,000.00")]

    def test_a_count_and_a_ledger_each_carry_their_unit_and_a_yes_or_no_reads_as_one(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_facts

        variance = {
            "kind": "stock_variance",
            "subject": {"type": "supply_point", "id": 2, "label": "Kano store"},
            "since": "2026-09-20",
            "days_open": 4,
            "facts": {
                "ledger": "120",
                "reported": "100",
                "variance": "20",
                "unit": "carton",
                "reconcilable": True,
                "reported_kind": "self_reported",
            },
        }
        rows = dict(check_facts(variance))
        assert rows["Ledger"] == "120 cartons"
        assert rows["Counted"] == "100 cartons"
        assert rows["Difference"] == "20 cartons"
        assert rows["Can be reconciled"] == "yes"
        assert rows["How it was counted"] == "self-reported"


class TestTheChecksPageReadsAsSentences:
    def _page(self, client_in_programme, da):
        op(da, "commodity_upsert", data={"slug": "chlorine", "name": "Chlorine", "base_unit": "L"})
        donor = op(da, "supplier_create", data={"name": "A donor"})
        us = op(da, "org_upsert", data={"slug": "us", "name": "The programme"})
        op(
            da,
            "contract_create",
            data={
                "commodity_slug": "chlorine",
                "supplier_id": donor["id"],
                "buyer_of_record": "programme_org",
                "buyer_org_id": us["id"],
                "consideration": "in_kind",
                "source": "we_recorded",
                "reference": "DON-1",
                "status": "placed",
                "quantity": "400",
                "quantity_unit": "jerry_can",
                "signed_on": "2026-01-01",
                "promised_lead_time_days": 60,
            },
        )
        return client_in_programme.get(reverse("supply_chain:checks")).content.decode()

    def test_a_late_delivery_reads_as_labelled_facts(self, client_in_programme, da):
        body = self._page(client_in_programme, da)
        text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body))
        assert "Promised lead time: 60 days" in text
        assert "Outstanding: 400 jerry cans" in text
        for raw in ("promised lead time days", "days late", "expected on", "contract id"):
            assert raw not in text


class TestACheckSaysWhatItsAgeCountsFrom:
    """The checks list read "40 days since 2026-08-15" and "19 days since
    2026-09-05": two ages, two dates, and nothing to say that one is the
    dispatch and the other the date the consignment was expected. And a
    documents check owed entirely by other organisations read "Ours to
    answer" -- which is true only in the sense that we are the ones chasing."""

    def _check(self, kind, audience="internal", days=19, since="2026-09-05"):
        return {"kind": kind, "audience": audience, "days_open": days, "since": since, "facts": {}}

    def test_a_late_shipment_is_aged_past_its_expected_date(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_age

        assert check_age(self._check("shipment_overdue")) == "19 days past the expected date, 2026-09-05"

    def test_outstanding_documents_are_aged_from_the_dispatch(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_age

        check = self._check("shipment_documents_outstanding", days=1, since="2026-08-15")
        assert check_age(check) == "1 day since dispatch, 2026-08-15"

    def test_a_kind_without_a_phrase_still_reads(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_age

        assert check_age(self._check("stock_variance", days=3)) == "3 days since 2026-09-05"
        assert check_age({"kind": "stock_variance", "days_open": None}) == ""

    def test_documents_owed_by_others_are_ours_to_chase(self):
        from connect_labs.supply_chain.templatetags.supply_chain_extras import check_audience

        assert check_audience(self._check("shipment_documents_outstanding")) == "Ours to chase"
        assert check_audience(self._check("award_not_contracted")) == "Ours to answer"
        assert check_audience(self._check("shipment_overdue")) == "Ours to chase"
        assert check_audience(self._check("shipment_overdue", audience="supplier")) == ("Only the supplier can answer")
