"""The unanswered-round walkthrough's replay keeps its story the same age on any render day.

THIS REPOSITORY IS PUBLIC. Everything the replay writes is invented.

The pages count ages from `date.today()`, so a replay on fixed calendar dates
reads older on every later render: "No reply · 17 days" becomes "· 40 days",
and the drafted reminders change with the day. The replay moves every story
date by (render day - 2 Oct 2026); these pin the offsets the story has.
"""

import datetime as dt
import importlib.util
from pathlib import Path

import pytest

REPLAY = Path(__file__).resolve().parents[3] / "scripts/walkthroughs/supply-sophie-unanswered-round/replay.py"


@pytest.fixture(scope="module")
def replay():
    spec = importlib.util.spec_from_file_location("unanswered_round_replay_under_test", REPLAY)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _ago(replay, story_iso, today):
    return (today - dt.date.fromisoformat(replay.story_day(story_iso, today))).days


@pytest.mark.parametrize("today", [dt.date(2026, 10, 2), dt.date(2026, 11, 20), dt.date(2027, 3, 1)])
def test_story_dates_keep_their_distance_from_the_render_day(replay, today):
    assert _ago(replay, "2026-09-15", today) == 17  # the ask: "No reply · 17 days"
    assert _ago(replay, "2026-09-18", today) == 14  # first reminder = ask + 3
    assert _ago(replay, "2026-09-23", today) == 9  # Sahel's second reminder
    assert _ago(replay, "2026-10-06", today) == -4  # the deadline: "in 4 days"
    assert _ago(replay, "2026-07-28", today) == 66  # round 1's advance


def test_on_the_story_day_nothing_moves(replay):
    assert replay.story_day("2026-09-15", replay.STORY_TODAY) == "2026-09-15"
    dates = replay.story_dates(replay.STORY_TODAY)
    assert dates["ask_date"] == "15 Sep 2026"
    assert dates["deadline_date"] == "6 Oct 2026"
    assert dates["second_reminder_date"] == "23 Sep 2026"
    assert dates["northgate_asked_date"] == "18 Sep 2026"
    assert dates["advance_paid_date"] == "28 Jul 2026"
    assert dates["today_date"] == "2 Oct 2026"


def test_the_display_dates_follow_the_render_day(replay):
    dates = replay.story_dates(dt.date(2026, 11, 20))
    assert dates["ask_date"] == "3 Nov 2026"
    assert dates["deadline_date"] == "24 Nov 2026"
    assert dates["today_date"] == "20 Nov 2026"


@pytest.mark.django_db
def test_seeded_round_reads_the_same_age_whatever_the_day(replay):
    from connect_labs.supply_chain.models import Outreach, Payment, Tender
    from connect_labs.supply_chain.procurement.views import _days_since_ask, _deadline_passed

    today = dt.date.today() - dt.timedelta(days=40)  # a render day that is not the story's
    replay.ensure_program()
    out = replay.seed_world(create_buyer=True, today=today)

    tender = Tender.objects.get(id=out["round2_tender_id"])
    assert (tender.response_deadline - today).days == 4  # still ahead: chasing is worth it
    passed = _deadline_passed({"status": "open", "response_deadline": tender.response_deadline.isoformat()}, today)
    assert not passed

    sahel = Outreach.objects.get(id=out["sahel_outreach_id"])
    assert _days_since_ask({"sent_on": sahel.sent_on.isoformat()}, today) == 17
    assert (sahel.last_reminder_on - sahel.sent_on).days == 8
    silent = Outreach.objects.filter(tender_id=tender.id, responded=False).order_by("last_reminder_on")
    assert [(o.last_reminder_on - o.sent_on).days for o in silent] == [8, 9, 10]

    advance = Payment.objects.get(contract_id=out["contract_id"])
    assert (today - advance.paid_on).days == 66
    assert out["ask_date"] == replay.display_day(sahel.sent_on.isoformat())
    assert out["deadline_date"] == replay.display_day(tender.response_deadline.isoformat())


@pytest.mark.django_db
def test_harmattan_round_2_waits_on_the_round_like_every_buyer_import_quote(replay):
    """Round 2's leading quote must not contradict "we import": CPT Kano, duty stated nil (as on
    PFI-0457). Under CPT the import is ours, so it waits on the round's own duty terms exactly as
    Sahel's EXW quote does, and counts zero once the waiver is set."""
    from decimal import Decimal

    from connect_labs.supply_chain.history.models import OperationCall
    from connect_labs.supply_chain.models import Quote, Tender
    from connect_labs.supply_chain.procurement.services.pricing import Money, _extras, basis_gaps

    replay.ensure_program()
    out = replay.seed_world(create_buyer=True, today=dt.date.today())
    tender = Tender.objects.get(id=out["round2_tender_id"])
    assert tender.duty_terms == ""  # unsettled until scene 5
    quote = Quote.objects.select_related("tender", "commodity").get(
        tender_id=tender.id, supplier_reference="PFI-2026-0611"
    )
    assert quote.incoterm == "CPT Kano"
    assert (quote.freight_basis, quote.duties_basis, quote.duties_amount) == ("included", "excluded", Decimal("0"))
    excerpt = (
        OperationCall.objects.filter(operation="quote_record", source_ref__contains="PFI0611").get().source_excerpt
    )
    assert "DDP" not in excerpt and "nil under your waiver" in excerpt
    from connect_labs.supply_chain.procurement.services.pricing import ROUND_DUTY_TERMS_REASON, Unconfirmed

    extras = _extras(quote, tender)
    assert isinstance(extras, Unconfirmed) and list(extras.reasons) == [ROUND_DUTY_TERMS_REASON]
    assert basis_gaps(quote) == []
    tender.duty_terms = "buyer_waiver"
    assert _extras(quote, tender) == Money(Decimal("0"))


@pytest.mark.django_db
def test_the_just_asked_rusf_tender_waits_on_its_suppliers(replay):
    """The overview carries a third row whose move is the suppliers': RUSF, asked 2 days back, nobody answered."""
    from connect_labs.supply_chain.models import Outreach, Tender
    from connect_labs.supply_chain.standing import standing_rows

    today = dt.date.today() - dt.timedelta(days=40)
    replay.ensure_program()
    out = replay.seed_world(create_buyer=True, today=today)

    tender = Tender.objects.get(id=out["rusf_tender_id"])
    assert tender.label == replay.RUSF_LABEL
    assert (tender.response_deadline - today).days == 12
    asked = Outreach.objects.filter(tender=tender)
    assert asked.count() == 3
    assert {(today - o.sent_on).days for o in asked} == {2}
    assert not asked.filter(responded=True).exists()
    emails = [c["email"] for o in asked for c in o.supplier.contacts]
    assert emails and all(e.endswith(".example.invalid") for e in emails)

    rows = {r.tender_id: r for r in standing_rows(out["program_id"], today) if r.kind == "tender"}
    rusf = rows[out["rusf_tender_id"]]
    assert "Collecting quotes" in rusf.stage
    assert rusf.whose == "suppliers"
    assert not rusf.ours and len(rusf.theirs) == 3
    assert rows[out["round2_tender_id"]].whose == "us"
