"""Award and approval: who decided, Place order only once approved, the order naming its award.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented. Builds on chlorine_world.
"""

import re
from datetime import date

import pytest
from django.urls import reverse

from connect_labs.labs.access.scopes import SYSTEM
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.tests import chlorine_world
from connect_labs.supply_chain.tests.chlorine_world import (
    PROGRAM,
    _ask,
    _comparable_pair,
    _compare_url,
    _registration,
    op,
)

pytestmark = pytest.mark.django_db

# The world's fixtures, shared rather than copied.
da = chlorine_world.da
account = chlorine_world.account
client_in_programme = chlorine_world.client_in_programme
world = chlorine_world.world


class TestARegulatoryApprovalRestsOnARegistration:
    def test_it_references_the_registration_and_the_award_page_shows_it(self, client_in_programme, da, world):
        registration = _registration(da, world)
        approval = _ask(da, world, rests_on_document_id=registration["id"])
        assert approval["rests_on_document_id"] == registration["id"]
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        assert "Rests on" in body.decode() and "Registration NG-0001" in body.decode()

    def test_a_document_from_another_programme_is_refused(self, da, world):
        elsewhere = SupplyDataAccess(access_token="unused", program_id=PROGRAM + 1, caller=SYSTEM)
        op(elsewhere, "supplier_create", data={"name": "Elsewhere"})
        foreign = op(
            elsewhere,
            "document_attach",
            data={"kind": "product_registration", "external_url": "https://example.org/x.pdf", "source": "document"},
        )
        with pytest.raises(ValueError, match="does not exist in this programme"):
            _ask(da, world, rests_on_document_id=foreign["id"])

    def test_the_answer_can_name_it_too(self, client_in_programme, da, world):
        registration = _registration(da, world)
        approval = _ask(da, world)
        form = client_in_programme.get(reverse("supply_chain:approval_decide", args=[approval["id"]])).content
        assert "Registration NG-0001" in form.decode()
        decided = op(
            da,
            "approval_decide",
            approval_id=approval["id"],
            status="approved",
            rests_on_document_id=registration["id"],
        )
        assert decided["rests_on_document_id"] == registration["id"]


class TestPlaceOrderWhileAnApprovalIsPending:
    def test_the_button_is_shown_disabled_with_whose_answer_and_since_when(self, client_in_programme, da, world):
        _ask(da, world)
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        body = body.decode()
        assert "Place order" in body
        button = body.split("Place order</button>", 1)[0].rsplit("<button", 1)[1]
        assert "disabled" in button
        assert "regulatory approval from <b>The regulator</b>" in body
        assert "asked 1 Sep 2026 and not yet answered" in body
        assert reverse("supply_chain:contract_create") + "?award=" not in body

    def test_once_approved_it_is_a_link(self, client_in_programme, da, world):
        approval = _ask(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved")
        body = client_in_programme.get(reverse("supply_chain:award_detail", args=[world["award"]["id"]])).content
        assert reverse("supply_chain:contract_create") + f"?award={world['award']['id']}" in body.decode()


class TestAnAwardRecordsThePersonWhoDecided:
    def _compare(self, world):
        return reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]]) + "?commodity=chlorine"

    def test_the_form_shows_the_signed_in_persons_name(self, client_in_programme, da, world):
        op(da, "quote_void", quote_id=world["quote"]["id"], reason="re-quoted")
        supplier = op(da, "supplier_create", data={"name": "Second chemicals"})
        op(
            da,
            "quote_record",
            data={
                "tender_id": world["tender"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "4.20",
                "as_quoted_unit": "per_pack",
                "quantity_basis": "600",
                "quantity_basis_unit": "jerry_can",
                "pack_spec_source": "stated_on_quote",
                "base_per_pack_stated": 20,
                "freight_basis": "included",
                "duties_basis": "included",
            },
        )
        body = client_in_programme.get(self._compare(world)).content.decode()
        assert re.search(r'<span data-testid="decided-by"[^>]*>Amina Bello</span>', body)
        assert 'name="decided_by"' not in body

    def test_a_posted_name_is_ignored_for_the_signed_in_person(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        supplier = op(da, "supplier_create", data={"name": "Third chemicals"})
        quote = op(
            da,
            "quote_record",
            data={
                "tender_id": world["tender"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "3.90",
                "as_quoted_unit": "per_pack",
            },
        )
        client_in_programme.post(
            self._compare(world),
            {"quote_id": quote["id"], "rationale": "cheaper", "decided_by": "Ngozi Eze"},
        )
        assert Award.objects.get(quote_id=quote["id"]).decided_by == "Amina Bello"

    def test_left_blank_it_is_the_signed_in_persons_name_not_their_login(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        supplier = op(da, "supplier_create", data={"name": "Fourth chemicals"})
        quote = op(
            da,
            "quote_record",
            data={
                "tender_id": world["tender"]["id"],
                "commodity_slug": "chlorine",
                "supplier_id": supplier["id"],
                "as_quoted_amount": "3.80",
                "as_quoted_unit": "per_pack",
            },
        )
        client_in_programme.post(self._compare(world), {"quote_id": quote["id"], "rationale": "cheaper"})
        assert Award.objects.get(quote_id=quote["id"]).decided_by == "Amina Bello"


class TestTheOrderPageSaysWhichAwardItWasPlacedAgainst:
    def _order(self, da, world, **extra):
        return op(
            da,
            "contract_create",
            data={
                "commodity_slug": "chlorine",
                "supplier_id": world["supplier"]["id"],
                "buyer_of_record": "programme_org",
                "buyer_org_id": world["us"]["id"],
                "source": "we_recorded",
                **extra,
            },
        )

    def test_it_names_the_award_who_decided_it_and_who_approved_it(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award

        Award.objects.filter(pk=world["award"]["id"]).update(decided_by="Amina Bello", decided_on=date(2026, 9, 2))
        approval = _ask(da, world)
        op(da, "approval_decide", approval_id=approval["id"], status="approved", decided_on="2026-09-10")
        order = self._order(da, world, award_id=world["award"]["id"], tender_id=world["tender"]["id"])
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        text = re.sub(r"\s+([,.;)])", r"\1", re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", body)))
        assert "Against the award to Sahel Chemicals, decided 2 Sep 2026 by Amina Bello" in text
        assert reverse("supply_chain:award_detail", args=[world["award"]["id"]]) in body
        assert "The regulator (Regulatory) approved 10 Sep 2026" in text

    def test_an_order_placed_without_an_award_says_nothing_of_one(self, client_in_programme, da, world):
        order = self._order(da, world)
        body = client_in_programme.get(reverse("supply_chain:order_detail", args=[order["id"]])).content.decode()
        assert "Against the award" not in body
        assert reverse("supply_chain:award_detail", args=[world["award"]["id"]]) not in body


class TestTheAwardControlsFitTheColumn:
    """At 1440px the reason, date, decided-by and Award button, stacked in the
    table's last cell, clipped the button to "A" and cut the questions off.
    They sit in a full-width row under each offer instead, so the table is
    only as wide as its figures."""

    def test_they_sit_in_one_block_under_the_grid_not_in_a_cell(self, client_in_programme, da, world):
        _comparable_pair(da, world)
        body = client_in_programme.get(_compare_url(world)).content.decode()
        forms = re.findall(r'<form method="post" action=""[^>]*? class="([^"]*)"[^>]*>(.*?)</form>', body, re.S)
        award_forms = [(cls, inner) for cls, inner in forms if 'name="rationale"' in inner]
        # ONE award for the tender, its quote chosen in it: both comparable quotes are offered.
        assert len(award_forms) == 1
        _, inner = award_forms[0]
        for field in ("quote_id", "rationale", "decided_on"):
            assert f'name="{field}"' in inner
        assert len(re.findall(r"<option value=\"\d+\"", inner)) == 2
        assert 'data-testid="decided-by"' in inner
        assert 'type="submit"' in inner and "Award" in inner
        # Not in the grid: each comparable row carries a link that opens the block with it chosen.
        grid = re.search(r'<table [^>]*data-testid="comparison-grid".*?</table>', body, re.S).group(0)
        assert "<form" not in grid
        assert len(re.findall(r'data-testid="grid-award"', grid)) == 2
        assert body.count('data-testid="award-start"') == 1

    def test_the_stacked_form_still_awards(self, client_in_programme, da, world):
        from connect_labs.supply_chain.models import Award, Quote

        _comparable_pair(da, world)
        quote = Quote.objects.get(supplier__org__name="Second chemicals")
        client_in_programme.post(
            _compare_url(world),
            {"quote_id": quote.pk, "rationale": "cheaper", "decided_on": "2026-09-20"},
        )
        award = Award.objects.get(quote_id=quote.pk)
        assert (award.decided_by, award.decided_on.isoformat()) == ("Amina Bello", "2026-09-20")
