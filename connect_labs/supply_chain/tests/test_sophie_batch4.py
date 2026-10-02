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
from connect_labs.supply_chain.standing import BLOCKED_RULE, standing_rows
from connect_labs.supply_chain.tests import test_history_timeline as timeline
from connect_labs.supply_chain.tests import test_sophie_batch3 as batch3
from connect_labs.supply_chain.tests.test_history_timeline import (
    _COMPARABLE,
    AUG_3,
    AUG_20,
    PACK_EMAIL,
    PROGRAM,
    _correct_pack,
    _quote_with,
    op,
)
from connect_labs.supply_chain.tests.test_sophie_batch3 import (
    _ALL_BUT_PACK,
    MARKET_PROGRAM,
    _compare,
    _home,
    _page,
    _row,
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
    return re.search(rf'<tr data-testid="ranked-row-actions" data-actions-for="{quote_id}".*?</tr>', body, re.S).group(
        0
    )


# ---- 1. the questions and the award form do not overlap --------------------


@pytest.mark.django_db
class TestTheQuestionsAndTheAwardFormAreTwoBlocks:
    def test_the_form_sits_below_the_questions_in_a_cell_that_wraps(self, da, base, client_in_program):
        # Comparable, and still asked its shelf life, lead time and validity.
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        row = _actions(_page(client_in_program, base["tender"]["id"]), quote["id"])

        cell = re.search(r"<td colspan[^>]*>", row).group(0)
        # base-table's cells are whitespace-nowrap: unwrapped, a question ran under the inputs.
        assert "whitespace-normal" in cell
        stack = re.search(r'<div class="([^"]*)">\s*<div data-testid="row-questions"', row).group(1)
        assert "flex-col" in stack.split()
        questions = row[row.index('data-testid="row-questions"') :]
        questions = questions[: questions.index("</div>\n")]
        assert "<form" not in questions and "Ask the supplier" in questions
        assert row.index('data-testid="row-questions"') < row.index("<form")
        assert not re.search(r"\b(absolute|fixed|relative|-mt-\d|z-\d+)\b", row)

    def test_the_form_carries_a_stable_hook(self, da, base, client_in_program):
        # The walkthrough recipe finds the award form, its reason and its button by these.
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        row = _actions(_page(client_in_program, base["tender"]["id"]), quote["id"])
        form = re.search(r'<form [^>]*data-testid="award-form"[^>]*>.*?</form>', row, re.S).group(0)
        assert f'name="quote_id" value="{quote["id"]}"' in form
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
    def test_the_badge_names_the_missing_requirements(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        row = _row(_compare(da, base["tender"]["id"]), quote["id"])
        assert row["specification"]["summary"] == "Not stated: sachets per carton, shelf life"

        body = _page(client_in_program, base["tender"]["id"])
        # On a blocked card the blocker is said once; the rest reads as not blocking (batch 5).
        unstated = re.search(r'data-testid="not-blocking"[^>]*>(.*?)</p>', body).group(1)
        assert " ".join(re.sub(r"<[^>]+>", "", unstated).split()) == "Not stated: shelf life"
        assert "OF 2 NOT STATED" not in body.upper()

    def test_the_pack_question_is_neutral_and_the_requirement_is_said_apart(self, da, base, client_in_program):
        _with_spec(da)
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        blocking = _row(_compare(da, base["tender"]["id"]), quote["id"])["blocking"]["question"]
        assert blocking["question"] == "How many sachets are in one carton?"
        assert blocking["requirement"] == "exactly 150"

        card = batch3._card(_page(client_in_program, base["tender"]["id"]), quote["id"])
        asked = re.search(r'data-testid="blocking-question"[^>]*>(.*?)</p>', card, re.S).group(1)
        assert "We require" not in asked and "weigh" not in asked
        # Since batch 6 the blocker's spec says it (since batch 8 a grey line, not a chip),
        # so the line under the question does not.
        assert "Our specification" not in asked
        assert re.search(
            r'data-testid="blocker-spec-line"[^>]*>Sachets per carton: not stated \(tender requires 150\)<', card
        )

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
    def test_the_ranked_row_opens_on_the_reply(self, da, base, ace, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        corrected = _correct_pack(da, quote, ace)
        body = _page(client_in_program, base["tender"]["id"])
        ranked = re.search(
            rf'<tr data-testid="ranked-row-detail" data-detail-for="{corrected["id"]}">.*?</tr>', body, re.S
        ).group(0)

        source = re.search(r'<details data-testid="correction-source".*?</details>', ranked, re.S).group(0)
        summary = " ".join(re.search(r"<summary[^>]*>(.*?)</summary>", source, re.S).group(1).split())
        assert summary == "Corrected 28 Aug by ACE (agent) from Northwind Foods email:"
        excerpt = re.search(r'<blockquote data-testid="source-excerpt"[^>]*>(.*?)</blockquote>', source, re.S)
        assert excerpt.group(1) == PACK_EMAIL

    def test_the_timeline_uses_the_same_units(self, da, base, ace, client_in_program):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _ALL_BUT_PACK)
        _correct_pack(da, quote, ace)
        body = client_in_program.get(
            reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
        ).content.decode()
        assert "Quote · Northwind Foods · corrected: sachets per carton 150 (was not stated)" in body
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


@pytest.mark.django_db
class TestTheBasisFlagNamesWhatTheComparisonBlocks:
    def test_named_and_counted_as_the_comparison_counts(self, da, base, home_client):
        tender_id = base["tender"]["id"]
        sahel = op(da, "supplier_create", AUG_3, data={"name": "Sahel Nutrition"})
        lakeside = op(da, "supplier_create", AUG_3, data={"name": "Lakeside Foods"})
        _quote_basis(da, tender_id, base["supplier"]["id"], freight_basis="not_specified")
        # Excluded with no amount: the comparison cannot cost it either.
        _quote_basis(da, tender_id, sahel["id"], duties_basis="excluded")
        _quote_basis(da, tender_id, lakeside["id"])  # complete

        (row,) = (r for r in standing_rows(PROGRAM, datetime.date(2026, 8, 30)) if r.kind == "tender")
        # Since batch 5 one flag covers every blocked quote, whatever blocks it.
        (flag,) = (f for f in row.stale if f.startswith("Can't compare yet"))
        assert (
            flag == "Can't compare yet: Northwind Foods — missing: freight; Sahel Nutrition — missing: duties amount"
        )
        assert flag.rule == BLOCKED_RULE

        blocked = _compare(da, tender_id)["blocked"]
        assert {r["supplier_name"] for r in blocked} == {"Northwind Foods", "Sahel Nutrition"}

        body = _home(home_client)
        # Since batch 7 a several-supplier flag is a one-line marker that folds open.
        rendered = re.search(
            r'<details data-testid="stale-flag" title="([^"]*)" class="([^"]*)">(.*?)</details>', body, re.S
        )
        assert rendered.group(1) == BLOCKED_RULE
        assert "amber" in rendered.group(2) and "red" not in rendered.group(2)
        assert "fa-flag" in rendered.group(3)


# ---- 6. the AI mark is not the colour of a warning ------------------------


@pytest.mark.django_db
class TestTheAIBadgeIsNotAmber:
    def test_everywhere_it_renders(self, da, base, ace, client_in_program, home_client):
        quote = _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {}, channel="mcp", actor=ace)
        pages = {
            "comparison": _page(client_in_program, base["tender"]["id"]),
            "tender": client_in_program.get(
                reverse("supply_chain:procurement_tender_detail", args=[base["tender"]["id"]])
            ).content.decode(),
            "overview": _home(home_client),
        }
        assert quote
        for name, body in pages.items():
            tags = re.findall(r"<(?:span|summary)\b[^>]*\bdata-ai[\s>][^>]*>", body)
            assert tags, name
            for tag in tags:
                classes = re.search(r'class="([^"]*)"', tag).group(1)
                assert "indigo" in classes and "amber" not in classes, (name, classes)


# ---- 7. the comparison's header -------------------------------------------


@pytest.mark.django_db
class TestTheComparisonHeader:
    def test_nothing_comparable_is_said_once(self, da, base, client_in_program):
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, {})
        body = _page(client_in_program, base["tender"]["id"])
        assert "0 of 1 quote can be compared." in body
        assert "Nothing is comparable yet" not in body

    def test_the_cost_basis_is_said_under_the_table_header(self, da, base, client_in_program):
        Commodity.objects.filter(slug="rutf").update(
            base_per_pack=150, course_definition={"base_units_per_course": 150}
        )
        _quote_with(da, base["tender"]["id"], base["supplier"]["id"], AUG_20, _COMPARABLE)
        body = _page(client_in_program, base["tender"]["id"])
        note = re.search(r'data-testid="cost-basis"[^>]*>(.*?)</p>', body).group(1)
        # A carton is a course here, so since the unanswered round's batch 4 the basis
        # and the equivalence are one line.
        assert note == "Basis: 1 carton = 150 sachets = 1 course (one child treated)"

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
    start = body.index(">Invited suppliers</h2>")
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
        panel = _invited_panel(_tender_page(client_in_program, base["tender"]["id"]))
        assert "No one invited yet" not in panel
        # Not listed again: they are in the Outreach table just above (unanswered round, batch 2).
        assert "Asked directly" not in panel and "Northwind Foods" not in panel

    def test_nobody_asked_still_says_so(self, da, base, client_in_program):
        assert "No one invited yet" in _invited_panel(_tender_page(client_in_program, base["tender"]["id"]))

    def test_an_awarded_tender_offers_no_invitation(self, da, base, client_in_program, invitable):
        open_body = _tender_page(client_in_program, base["tender"]["id"])
        assert "Invite a registered supplier" in open_body

        Tender.objects.filter(pk=base["tender"]["id"]).update(status="awarded")
        body = _tender_page(client_in_program, base["tender"]["id"])
        assert "Invite a registered supplier" not in body
        assert "while it is open" not in body

    def test_history_is_headed_like_the_other_sections_and_dates_read_one_way(self, da, base, client_in_program):
        batch3._outreach(da, base["tender"]["id"], base["supplier"]["id"], "2026-07-06")
        body = _tender_page(client_in_program, base["tender"]["id"])
        assert '<h2 class="text-lg font-semibold text-gray-900 mb-2 scroll-mt-20">History</h2>' in body
        assert '<section id="history"' in body
        assert "6 Jul 2026" in body
        assert "2026-07-06" not in body
        # The response deadline reads as words. Date inputs' min/max attributes are
        # machine format by design, and one carries TODAY -- which is the deadline's
        # date on 30 Sep 2026 -- so they are not the reader's text.
        readable = re.sub(r'\b(min|max)="[^"]*"', "", body)
        assert "30 Sep 2026" in body and "2026-09-30" not in readable  # the response deadline


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
    table = body[body.index("who is actually buying") :]
    row = next(r for r in re.findall(r"<tr>.*?</tr>", table, re.S) if reference in r)
    return [" ".join(c.split()) for c in re.findall(r"<td[^>]*>(.*?)</td>", row, re.S)][3]


def _in_transit(body):
    cell = body[body.index(">In transit</div>") :]
    return " ".join(re.search(r"<div[^>]*>(.*?)</div>", cell[len(">In transit</div>") :], re.S).group(1).split())


@pytest.mark.django_db
class TestAPastDateAgreesWithItself:
    def test_between_dispatch_and_receipt(self, da, base, home_client, dispatched_order):
        past = _home(home_client, as_of="2026-08-25")
        assert _contracts_status(past, "PO-ONROAD") == "in transit"
        assert _in_transit(past) == "600 cartons"

    def test_a_shipment_with_a_dispatch_day_is_on_the_road_until_delivered(self):
        from connect_labs.supply_chain.models import Shipment

        day = datetime.date(2026, 8, 20)
        assert Shipment(status="planned", dispatched_on=day).is_in_transit
        assert not Shipment(status="planned").is_in_transit
        assert not Shipment(status="delivered", dispatched_on=day).is_in_transit

    def test_once_received(self, da, base, home_client, dispatched_order):
        live = _home(home_client)
        assert _contracts_status(live, "PO-ONROAD") != "in transit"
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
