"""Sophie's RUTF walkthrough, judged again: the fourth batch of what it found.

THIS REPOSITORY IS PUBLIC. Every company, product, figure and address here is invented.

Same fixtures as the third batch (test_sophie_batch3.py): a synthetic
program, history recorded through `call_operation` with `seed_overrides`, and
the pages read back as rendered.
"""

import datetime
import re

import pytest
from django.urls import reverse

from connect_labs.supply_chain.history.labels import Lookup, quote_field_label
from connect_labs.supply_chain.models import Award, Commodity, Quote, Tender
from connect_labs.supply_chain.procurement.services.questions import missing_facts
from connect_labs.supply_chain.procurement.views import cost_basis
from connect_labs.supply_chain.tests import test_history_timeline as timeline
from connect_labs.supply_chain.tests import test_sophie_batch3 as batch3
from connect_labs.supply_chain.tests.test_history_timeline import (
    _COMPARABLE,
    AUG_3,
    AUG_20,
    _correct_pack,
    _quote_with,
    op,
)
from connect_labs.supply_chain.tests.test_sophie_batch3 import (
    _ALL_BUT_PACK,
    MARKET_PROGRAM,
    _home,
    _page,
    _sign_in,
    _with_spec,
)

registered_synthetic = timeline.registered_synthetic
da = timeline.da
sophie = timeline.sophie
ace = timeline.ace
base = timeline.base
client_in_program = timeline.client_in_program
home_client = batch3.home_client
listed_tender = batch3.listed_tender


def _ranked(body, quote_id):
    return re.search(rf'<tr data-testid="ranked-row" data-quote-id="{quote_id}"[^>]*>.*?</tr>', body, re.S).group(0)


def _actions(body, quote_id):
    """The award: one block under the comparison, starting on this quote."""
    block = re.search(r'<details class="fold" id="award-block" data-testid="award-start".*?</details>', body, re.S)
    assert re.search(rf'<option value="{quote_id}" selected>', block.group(0))
    return block.group(0)


# ---- 1. the questions and the award form do not overlap --------------------


@pytest.mark.django_db
class TestTheQuestionsAndTheAwardFormAreTwoBlocks:

    def test_the_form_carries_a_stable_hook(self, da, base, client_in_program):
        # The walkthrough recipe finds the award form, its reason and its button by these.
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        row = _actions(_page(client_in_program, base["tender"]["id"]), quote["id"])
        form = re.search(r'<form [^>]*data-testid="award-form"[^>]*>.*?</form>', row, re.S).group(0)
        assert 'name="quote_id"' in form and f'<option value="{quote["id"]}" selected>' in form
        assert 'name="rationale"' in form
        assert re.search(r'<button [^>]*type="submit"[^>]*>\s*Award\s*</button>', form)


# ---- 2. who decided is who is signed in ------------------------------------


@pytest.mark.django_db
class TestDecidedByIsTheSignedInPerson:
    def test_it_is_shown_not_asked(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        row = _actions(_page(client_in_program, base["tender"]["id"]), quote["id"])
        assert re.search(r'data-testid="decided-by"[^>]*>Sophie Bello<', row)
        assert 'name="decided_by"' not in row

    def test_a_posted_name_is_ignored(self, da, base, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        url = reverse("supply_chain:procurement_comparison", args=[base["tender"]["id"]]) + "?commodity=rutf"
        client_in_program.post(url, {"quote_id": quote["id"], "rationale": "cheapest", "decided_by": "Somebody Else"})
        assert Award.objects.get(quote_id=quote["id"]).decided_by == "Sophie Bello"


# ---- 3. the specification names what is missing ---------------------------


@pytest.mark.django_db
class TestTheSpecificationNamesWhatIsMissing:

    def test_a_supplier_message_still_says_the_requirement(self, da, base):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        stored = Quote.objects.get(pk=quote["id"])
        facts = {f.key: f for f in missing_facts(stored, stored.commodity, stored.tender)}
        assert facts["pack_spec"].text == "How many sachets are in one carton? We require exactly 150."

    def test_a_unit_s_weight_is_asked_only_when_the_specification_sets_one(self, da, base):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {})
        stored = Quote.objects.get(pk=quote["id"])
        pack = next(f for f in missing_facts(stored, stored.commodity, stored.tender) if f.key == "pack_spec")
        assert pack.question == "How many sachets are in one carton?"

        Commodity.objects.filter(pk=stored.commodity_id).update(
            spec_requirements=[{"field": "sachet_weight_grams", "operator": "==", "value": 92, "unit": "g"}]
        )
        stored = Quote.objects.get(pk=quote["id"])
        pack = next(f for f in missing_facts(stored, stored.commodity, stored.tender) if f.key == "pack_spec")
        assert pack.question == "How many sachets are in one carton? What does one sachet weigh?"


# ---- 4. a correction in the commodity's own units, opening on its source ---


@pytest.mark.django_db
class TestACorrectionSaysItsUnitsAndOpensOnItsSource:

    def test_the_timeline_uses_the_same_units(self, da, base, ace, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        _correct_pack(da, quote, ace)
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
        ).content.decode()
        # Under its email event (unanswered round 1004 b3): the kind set apart, the
        # supplier named once in the event's head rather than again on the line.
        assert re.search(r"Quote</span> · sachets per carton not stated → <strong[^>]*>150</strong>", body)
        assert "units per pack" not in body

    def test_a_commodity_without_units_keeps_the_generic_words(self):
        assert quote_field_label("base_per_pack_stated", {}, Lookup()) == "Units per pack"


# ---- 5. the overview's basis flag names the quotes, as the comparison blocks them


def _quote_basis(da, tender_id, supplier_id, **basis):
    return _quote_with(
        da,
        tender_id,
        supplier_id,
        AUG_20,
        {**_COMPARABLE, "freight_basis": "included", "duties_basis": "included", **basis},
    )


# ---- 6. the AI mark is not the colour of a warning ------------------------


# ---- 7. the comparison's header -------------------------------------------


@pytest.mark.django_db
class TestTheComparisonHeader:

    def test_only_what_the_commodity_defines(self):
        rutf = {"base_unit": "sachet", "pack_unit": "carton"}
        assert cost_basis(rutf) == ""
        assert cost_basis({**rutf, "base_per_pack": 150}) == "1 carton = 150 sachets"
        spec = [{"field": "sachets_per_carton", "operator": "==", "value": 150}]
        assert cost_basis({**rutf, "spec_requirements": spec}) == "1 carton = 150 sachets"
        at_least = [{"field": "sachets_per_carton", "operator": ">=", "value": 150}]
        assert cost_basis({**rutf, "spec_requirements": at_least}) == ""
        course = {"base_units_per_day": 3, "days_per_course": 7}
        assert cost_basis({**rutf, "course_definition": course}) == "a course is 21 sachets"


# ---- 8. the tender page ----------------------------------------------------


def _tender_page(client, tender_id):
    return client.get(reverse("supply_chain:procurement_tender_detail", args=[tender_id])).content.decode()


def _invited_panel(body):
    start = body.index('data-testid="invited-suppliers"')
    return body[start : body.index("</ul>", start)]


@pytest.fixture
def invitable(db):
    from connect_labs.labs.models import LabsOrg
    from connect_labs.supply_chain.models import SupplierProfile

    org = LabsOrg.objects.create(slug="plateau-mills", name="Plateau Mills")
    SupplierProfile.objects.create(org=org)
    return org


@pytest.mark.django_db
class TestTheTenderPage:
    def test_who_was_asked_is_listed_not_no_one(self, da, base, client_in_program):
        batch3._outreach(da, base["tender"]["id"], base["supplier"]["id"], "2026-08-01")
        body = _tender_page(client_in_program, base["tender"]["id"])
        # With everyone asked in Outreach and no marketplace invitation, the panel has
        # nothing to hold and is left out (DDD 003 batch 2); when shown, it repeats no one.
        panel = _invited_panel(body) if 'data-testid="invited-suppliers"' in body else ""
        assert "No one invited yet" not in panel
        # Not listed again: they are in the Outreach table just above (unanswered round, batch 2).
        assert "Asked directly" not in panel and "Northwind Foods" not in panel

    def test_nobody_asked_still_says_so(self, da, base, client_in_program):
        # Said once, in the Suppliers table, which lists invitations and marketplace bids alike.
        assert "Nobody asked yet." in _tender_page(client_in_program, base["tender"]["id"])

    def test_an_awarded_tender_offers_no_invitation(self, da, base, client_in_program, invitable):
        open_body = _tender_page(client_in_program, base["tender"]["id"])
        assert "Invite a registered supplier" in open_body

        Tender.objects.filter(pk=base["tender"]["id"]).update(status="awarded")
        body = _tender_page(client_in_program, base["tender"]["id"])
        assert "Invite a registered supplier" not in body
        assert "while it is open" not in body


# ---- 9. a past date agrees with itself -------------------------------------


@pytest.fixture
def dispatched_order(da, base):
    """600 cartons dispatched 20 Aug, received 1 Sep."""
    contract = op(
        da,
        "contract_create",
        AUG_3,
        data={
            "supplier_id": base["supplier"]["id"],
            "commodity_slug": "rutf",
            "buyer_of_record": "programme_org",
            "buyer_org_id": base["us"]["id"],
            "reference": "PO-ONROAD",
            "quantity": "600",
            "quantity_unit": "carton",
            "status": "placed",
            "source": "we_recorded",
        },
    )
    shipment = op(
        da,
        "shipment_record",
        AUG_20,
        data={
            "contract_id": contract["id"],
            "reference": "SH-ONROAD",
            "dispatched_on": "2026-08-20",
            "expected_on": "2026-09-01",
            "source": "supplier_reported",
            "lines": [{"quantity": "600", "quantity_unit": "carton"}],
        },
    )
    store = op(
        da,
        "supply_point_upsert",
        AUG_3,
        data={"slug": "kano-store", "name": "Kano store", "kind": "central_store", "source": "we_recorded"},
    )
    op(
        da,
        "receipt_record",
        datetime.datetime(2026, 9, 1, 9, tzinfo=datetime.UTC),
        data={
            "contract_id": contract["id"],
            "shipment_id": shipment["id"],
            "supply_point_id": store["id"],
            "received_on": "2026-09-01",
            "source": "we_recorded",
            "lines": [{"quantity_accepted": "600", "quantity_unit": "carton"}],
        },
    )
    return contract


def _contracts_status(body, reference):
    table = body[body.index('data-testid="fold-contracts"') :]
    row = next(r for r in re.findall(r"<tr>.*?</tr>", table, re.S) if reference in r)
    return [" ".join(c.split()) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)][3]


def _in_transit(body):
    cell = body[body.index(">In transit</div>") :]
    return " ".join(re.search(r"<div[^>]*>(.*?)</div>", cell[len(">In transit</div>") :], re.S).group(1).split())


@pytest.mark.django_db
class TestAPastDateAgreesWithItself:
    def test_between_dispatch_and_receipt(self, da, base, home_client, dispatched_order):
        past = _home(home_client, as_of="2026-08-25")
        assert _contracts_status(past, "PO-ONROAD").endswith("in transit")
        assert _in_transit(past) == "600 cartons"

    def test_a_shipment_with_a_dispatch_day_is_on_the_road_until_delivered(self):
        from connect_labs.supply_chain.models import Shipment

        day = datetime.date(2026, 8, 20)
        assert Shipment(status="planned", dispatched_on=day).is_in_transit
        assert not Shipment(status="planned").is_in_transit
        assert not Shipment(status="delivered", dispatched_on=day).is_in_transit

    def test_once_received(self, da, base, home_client, dispatched_order):
        live = _home(home_client)
        assert not _contracts_status(live, "PO-ONROAD").endswith("in transit")
        assert _in_transit(live) != "600 cartons"


# ---- 10. the public listing, seen by its own program -----------------------


def _listing(client, tender):
    return client.get(reverse("supply_chain:market_tender", args=[tender.pk])).content.decode()


@pytest.mark.django_db
class TestTheListingHero:
    def test_the_owning_program_is_not_asked_to_register(self, client, django_user_model, listed_tender):
        user = django_user_model.objects.create_user(username="sophie4", password="x", email="s4@example.org")
        _sign_in(client, user, [{"id": MARKET_PROGRAM, "name": "Lakeside RUTF"}])
        body = _listing(client, listed_tender)
        assert "Register as a supplier" not in body and "Register to bid" not in body

    def test_a_visitor_with_no_supplier_is(self, client, django_user_model, listed_tender):
        user = django_user_model.objects.create_user(username="visitor4", password="x", email="v4@example.org")
        _sign_in(client, user, [{"id": MARKET_PROGRAM + 1, "name": "Someone else's"}])
        assert "Register as a supplier" in _listing(client, listed_tender)

    def test_what_is_asked_for_and_where_it_goes(self, client, listed_tender):
        body = _listing(client, listed_tender)
        assert re.search(r'data-testid="asked-for"[^>]*>500 cartons<', body)
        assert re.search(r'data-testid="delivered-to"[^>]*>Lakeside<', body)
        assert "DELIVERED TO" in body and "WHERE THE GOODS GO" not in body

    def test_more_than_one_place_is_counted(self, client, listed_tender):
        Tender.objects.filter(pk=listed_tender.pk).update(
            delivery_points=[{"key": "a", "city": "Lakeside"}, {"key": "b", "city": "Hilltown"}]
        )
        assert re.search(r'data-testid="delivered-to"[^>]*>2 places<', _listing(client, listed_tender))

    def test_who_posted_it_and_when(self, client, django_user_model, listed_tender):
        Tender.objects.filter(pk=listed_tender.pk).update(
            opened_at=datetime.datetime(2026, 7, 6, 9, tzinfo=datetime.UTC)
        )
        anonymous = _listing(client, listed_tender)
        assert re.search(r'data-testid="posted-by"[^>]*>Posted 6 Jul 2026<', anonymous)

        user = django_user_model.objects.create_user(username="sophie5", password="x", email="s5@example.org")
        client.force_login(user)
        session = client.session
        session["labs_oauth"] = {
            "organization_data": {
                "programs": [{"id": MARKET_PROGRAM, "name": "Lakeside RUTF", "organization": "lakeside-trust"}],
                "organizations": [{"slug": "lakeside-trust", "name": "Lakeside Health Trust"}],
            }
        }
        session.save()
        own = _listing(client, listed_tender)
        assert re.search(r'data-testid="posted-by"[^>]*>Posted 6 Jul 2026 by Lakeside Health Trust<', own)
