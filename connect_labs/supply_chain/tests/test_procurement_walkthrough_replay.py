"""The unanswered-round walkthrough's replay and seed hook (scripts/walkthroughs/supply-sophie-unanswered-round).

The replay keeps its story the same age on any render day; it and the `--sahel-replies` hook
share ONE render day (the app's, not the machine's calendar); and the sheets narrative's seed
differs only where it must.

THIS REPOSITORY IS PUBLIC. Every name, address and figure here is invented.
"""

import datetime as dt
import importlib.util
import sys
from pathlib import Path
from unittest import mock
from zoneinfo import ZoneInfo

import pytest
from django.utils import timezone

HERE = Path(__file__).resolve().parents[3] / "scripts/walkthroughs/supply-sophie-unanswered-round"


def _load(name, filename):
    sys.path.insert(0, str(HERE.parent))
    spec = importlib.util.spec_from_file_location(name, HERE / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def replay():
    return _load("unanswered_round_replay_under_test", "replay.py")


@pytest.fixture(scope="module")
def seed():
    return _load("unanswered_round_seed_under_test", "seed.py")


# 23:30 on 1 Oct in Los Angeles is 06:30 on 2 Oct in UTC: the machine's calendar and the app's disagree.
_LATE_IN_LA = dt.datetime(2026, 10, 2, 6, 30, tzinfo=dt.UTC)


def test_replay_and_hook_compute_the_same_today(replay, seed):
    assert replay.story_today() == seed.story_today() == timezone.localdate()


def test_the_hook_without_django_reads_the_apps_zone(seed, settings):
    assert seed.APP_TIME_ZONE == settings.TIME_ZONE
    real = dt.datetime

    class Frozen(real):
        @classmethod
        def now(cls, tz=None):
            return _LATE_IN_LA.astimezone(tz) if tz else _LATE_IN_LA.astimezone(ZoneInfo("America/Los_Angeles"))

    fake_settings = mock.Mock(configured=False)
    with mock.patch.object(seed.dt, "datetime", Frozen), mock.patch("django.conf.settings", fake_settings):
        assert seed.story_today() == dt.date(2026, 10, 2)


def test_across_midnight_both_say_the_apps_day(replay, seed):
    with mock.patch.object(timezone, "now", return_value=_LATE_IN_LA):
        assert replay.story_today() == seed.story_today() == dt.date(2026, 10, 2)


@pytest.mark.django_db
def test_the_reply_never_predates_the_chase(replay, seed):
    """Seed on the render day, then record Sahel's reply as the local hook does: replied on or after the chase."""
    from connect_labs.supply_chain.models import Outreach

    replay.ensure_program()
    out = replay.seed_world(create_buyer=True)
    sahel = Outreach.objects.get(id=out["sahel_outreach_id"])
    chased = sahel.last_reminder_on
    world = replay.World(out["program_id"], replay.personas())
    today = seed.story_today()
    world.email(
        today.isoformat(),
        "outreach_update",
        **seed.SAHEL_REPLY,
        outreach_id=out["sahel_outreach_id"],
        data={"responded": True, "response_kind": "quote", "responded_on": today.isoformat()},
    )
    sahel.refresh_from_db()
    assert sahel.responded_on >= chased
    assert sahel.responded_on == timezone.localdate()


def _kanem_excerpt():
    from connect_labs.supply_chain.history.models import OperationCall

    return (
        OperationCall.objects.filter(operation="quote_record", source_ref__contains="CAK9q2719").get().source_excerpt
    )


def test_the_clearing_day_follows_the_render_day(replay):
    assert replay.forwarder_clear_dates(dt.date(2026, 10, 2)) == {
        "forwarder_clear_date_iso": "2026-10-14",
        "forwarder_clear_date": "14 Oct",
    }
    assert replay.forwarder_clear_dates(dt.date(2026, 11, 20))["forwarder_clear_date"] == "2 Dec"


@pytest.mark.django_db
def test_the_clearing_day_is_later_than_the_shipments_seeded_eta(replay):
    from connect_labs.supply_chain.models import Shipment

    today = dt.date.today() - dt.timedelta(days=40)
    replay.ensure_program()
    out = replay.seed_world(create_buyer=True, today=today)
    shipment = Shipment.objects.get(contract_id=out["contract_id"])
    clear = dt.date.fromisoformat(out["forwarder_clear_date_iso"])
    assert shipment.expected_on is not None and clear > shipment.expected_on
    assert (clear - today).days == 12
    assert out["forwarder_clear_date"] == f"{clear.day} {clear:%b}"


@pytest.mark.django_db
def test_without_sheets_kanems_email_is_unchanged(replay):
    replay.ensure_program()
    replay.seed_world(create_buyer=True, today=dt.date.today())
    assert replay.KANEM_PACK_SENTENCE not in _kanem_excerpt()


@pytest.mark.django_db
def test_under_sheets_the_email_states_the_pack_the_ai_left_out(replay):
    from connect_labs.supply_chain.models import Quote

    replay.ensure_program()
    replay.seed_world(create_buyer=True, today=dt.date.today(), sheets=True)
    assert _kanem_excerpt().endswith("Pro-forma KF/Q/2719 attached: cartons of 150 x 92 g sachets.")
    quote = Quote.objects.get(supplier_reference="KF/Q/2719")
    assert quote.pack_spec_source == "not_stated"
    assert quote.base_per_pack_stated is None


def test_only_a_sheets_seed_names_the_keyword_on_the_server(seed):
    assert 'replay["run"](mint=True, **{})' in seed._driver("")
    assert "replay[\"run\"](mint=True, **{'sheets': True})" in seed._driver("", sheets=True)


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
    assert [(o.last_reminder_on - o.sent_on).days for o in silent] == [
        8,
        11,
        12,
    ]  # Sahel due today; Lagoon, Savanna later

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
    assert emails and all(e.endswith(".example") for e in emails)

    rows = {r.tender_id: r for r in standing_rows(out["program_id"], today) if r.kind == "tender"}
    rusf = rows[out["rusf_tender_id"]]
    assert "Collecting quotes" in rusf.stage
    assert rusf.whose == "suppliers"
    assert not rusf.ours and len(rusf.theirs) == 3
    assert rows[out["round2_tender_id"]].whose == "us"


@pytest.mark.django_db
def test_a_reseed_replaces_a_stale_contact_on_the_shared_company(replay):
    """The company outlives the reset; an address left by an earlier seed must not reach the drafts."""
    from connect_labs.supply_chain.models import Supplier

    today = dt.date.today() - dt.timedelta(days=40)
    replay.ensure_program()
    replay.seed_world(create_buyer=True, today=today)
    sahel = Supplier.objects.get(org__name="Sahel Nutrition Industries")
    profile = sahel.org.supplier_profile
    profile.contacts = [{"name": "Old Contact", "email": "sales@sahel-nutrition.example.invalid"}]
    profile.save()

    replay.reset()
    replay.seed_world(create_buyer=True, today=today)
    emails = [c["email"] for c in Supplier.objects.get(org__name="Sahel Nutrition Industries").contacts]
    assert emails == ["sales@sahel-nutrition.example"]
