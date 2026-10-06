"""An award before the tender's deadline is allowed, and says what was still open.

Owner decision, 2026-10-04 (DDD run supply-sophie-unanswered-round, scene 5): Sophie
may award while suppliers are silent and questions unanswered. The award form shows
what is still open as chips, and the award records the same facts
(moves.open_at_decision), so the decision says it was early.

THIS REPOSITORY IS PUBLIC. Every company, product and figure here is invented.
"""

import datetime

import pytest
from django.urls import reverse

from connect_labs.supply_chain import moves
from connect_labs.supply_chain.models import Award
from connect_labs.supply_chain.operations import call_operation
from connect_labs.supply_chain.tests import test_history_timeline as timeline
from connect_labs.supply_chain.tests.test_history_timeline import _COMPARABLE, AUG_3, AUG_20, _quote_with, op

registered_synthetic = timeline.registered_synthetic
da = timeline.da
sophie = timeline.sophie
client_in_program = timeline.client_in_program

TODAY = datetime.date.today()


def _world(da, deadline):
    op(
        da,
        "commodity_upsert",
        AUG_3,
        data={"slug": "rutf", "name": "RUTF", "base_unit": "sachet", "pack_unit": "carton"},
    )
    names = ("Quill Foods", "Marsh Nutrition", "Ember Pastes")
    suppliers = {n: op(da, "supplier_create", AUG_3, data={"name": n}) for n in names}
    tender = op(
        da,
        "tender_create",
        AUG_3,
        data={
            "label": "Tender Early",
            "delivery_point": {"city": "Kano"},
            "response_deadline": deadline.isoformat(),
            "lines": [{"commodity_slug": "rutf", "quantity": "600", "quantity_unit": "carton"}],
        },
    )
    op(da, "tender_update", AUG_3, tender_id=tender["id"], data={"status": "open"})
    for name, supplier in suppliers.items():
        # Marsh replied with a question instead of a quote: logged as a reply on its invitation.
        replied = {"responded": True, "responded_on": "2026-08-20"} if name == "Marsh Nutrition" else {}
        op(
            da,
            "outreach_log",
            AUG_3,
            data={"tender_id": tender["id"], "supplier_id": supplier["id"], "sent_on": "2026-08-03", **replied},
        )
    quill = suppliers["Quill Foods"]
    quote = _quote_with(da, tender["id"], quill["id"], AUG_20, _COMPARABLE)
    # Its question: a reply owed by us.
    marsh = suppliers["Marsh Nutrition"]
    op(
        da,
        "commitment_record",
        AUG_20,
        data={
            "kind": "question",
            "supplier_id": marsh["id"],
            "tender_id": tender["id"],
            "text": "Who imports?",
            "raised_on": "2026-08-20",
            "source": "supplier_reported",
        },
    )
    return {"tender": tender, "quote": quote, "suppliers": suppliers}


def _award(da, world, **extra):
    return call_operation(
        "award_create",
        da,
        {"tender_id": world["tender"]["id"], "quote_id": world["quote"]["id"], "rationale": "lowest landed", **extra},
        channel="command",
    )


class TestTheChips:
    def test_each_open_fact_is_one_chip_in_the_moves_words(self):
        chips = moves.open_at_decision_chips({"deadline_days": 4, "silent": 2, "replies_owed": 1})
        assert [(c["label"], c["tone"]) for c in chips] == [
            ("Deadline in 4 days", "neutral"),
            ("2 silent", "theirs"),
            ("1 reply owed", "ours"),
        ]

    def test_plurals_and_the_last_day(self):
        chips = moves.open_at_decision_chips({"deadline_days": 0, "silent": 1, "replies_owed": 3})
        assert [c["label"] for c in chips] == ["Deadline today", "1 silent", "3 replies owed"]

    def test_nothing_open_is_no_chips(self):
        assert moves.open_at_decision_chips({"deadline_days": None, "silent": 0, "replies_owed": 0}) == []
        assert moves.open_at_decision_chips(None) == []


@pytest.mark.django_db
class TestTheAwardRecordsWhatWasOpen:
    def test_an_award_before_the_deadline_is_allowed_and_records_the_open_facts(self, da):
        world = _world(da, TODAY + datetime.timedelta(days=4))
        award = _award(da, world)

        # Ember is silent; Marsh replied with a question, so it is owed a reply rather than silent.
        assert award["open_at_decision"] == {
            "deadline": (TODAY + datetime.timedelta(days=4)).isoformat(),
            "deadline_days": 4,
            "silent": 1,
            # Marsh's question is still open, so it is not yet waited on for a quote.
            "awaiting_quote": 0,
            "replies_owed": 1,
        }
        assert Award.objects.get(pk=award["id"]).open_at_decision["deadline_days"] == 4

    def test_a_backdated_decision_counts_the_days_from_the_day_it_was_made(self, da):
        world = _world(da, TODAY + datetime.timedelta(days=4))
        award = _award(da, world, decided_on=(TODAY - datetime.timedelta(days=2)).isoformat())
        assert award["open_at_decision"]["deadline_days"] == 6

    def test_after_the_deadline_no_days_are_left_to_record(self, da):
        world = _world(da, TODAY - datetime.timedelta(days=1))
        award = _award(da, world)
        assert award["open_at_decision"]["deadline_days"] is None
        assert [c["label"] for c in moves.open_at_decision_chips(award["open_at_decision"])] == [
            "1 silent",
            "1 reply owed",
        ]


@pytest.mark.django_db
class TestThePages:
    def test_the_award_form_shows_what_is_still_open(self, da, client_in_program):
        world = _world(da, TODAY + datetime.timedelta(days=4))
        url = reverse("supply_chain:procurement_comparison", args=[world["tender"]["id"]])
        body = client_in_program.get(url, {"commodity": "rutf"}).content.decode()

        start = body.index('data-testid="award-open-facts"')
        facts = body[start : body.index("</summary>", start)]
        assert "still open" in facts
        assert ">Deadline in 4 days<" in facts
        assert ">1 silent<" in facts
        assert ">1 reply owed<" in facts

    def test_the_award_page_shows_what_was_open_at_the_decision(self, da, client_in_program):
        world = _world(da, TODAY + datetime.timedelta(days=4))
        award = _award(da, world)
        body = client_in_program.get(reverse("supply_chain:award_detail", args=[award["id"]])).content.decode()

        start = body.index('data-testid="award-open-at-decision"')
        facts = body[start : body.index("</div>", start)]
        assert "Open at decision" in facts
        assert ">Deadline in 4 days<" in facts
        assert ">1 silent<" in facts
        assert ">1 reply owed<" in facts
