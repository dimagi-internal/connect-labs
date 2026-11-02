"""Public links to a saved run: the copy and its lifetime.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from datetime import timedelta
from types import SimpleNamespace

import pytest
from django.utils import timezone

from connect_labs.workflow import public_shares
from connect_labs.workflow.models import PublicRunShare

SNAPSHOT = {
    "pipelines": {"visits": {"rows": [{"opportunity_id": 7, "note": "a"}], "metadata": {"row_count": 1}}},
    "workers": [{"username": "worker-one", "name": "Worker One"}],
    "opportunity_ids": [7],
}


def _definition():
    return SimpleNamespace(
        id=11,
        name="Battery report",
        template_type="",
        data={
            "name": "Battery report",
            "statuses": [{"id": "new"}],
            "opportunity_ids": [7],
            "pipeline_sources": [{"pipeline_id": 3}],
            "config": {"showFilters": False, "actions": [{"key": "coach"}], "agent": {"share": True}},
        },
    )


def _run(status="completed", snapshot=SNAPSHOT):
    return SimpleNamespace(
        id=22,
        definition_id=11,
        opportunity_id=7,
        program_id=None,
        name="Week one",
        status=status,
        is_completed=status == "completed",
        state={"seen": True},
        period_start="2026-01-01",
        period_end="2026-01-07",
        completed_at="2026-01-08T00:00:00+00:00",
        snapshot=snapshot,
    )


def _share(**kwargs):
    defaults = dict(expires_at=timezone.now() + timedelta(days=1))
    defaults.update(kwargs)
    return public_shares.create(None, _definition(), _run(), "function WorkflowUI() {}", **defaults)


def test_the_copy_holds_what_the_page_draws_and_no_way_to_act():
    payload = public_shares.copy_payload(_definition(), _run(), "function WorkflowUI() {}")

    assert payload["renderCode"] == "function WorkflowUI() {}"
    assert payload["instance"]["snapshot"] == SNAPSHOT
    assert payload["instance"]["status"] == "completed"
    assert payload["pipelines"] == SNAPSHOT["pipelines"]
    assert payload["workers"] == SNAPSHOT["workers"]
    assert payload["definition"]["name"] == "Battery report"
    assert payload["definition"]["config"] == {"showFilters": False}
    assert "pipeline_sources" not in payload["definition"]


@pytest.mark.django_db
def test_a_link_past_its_expiry_finds_nothing_though_the_sweep_has_not_run():
    share = _share(expires_at=timezone.now() - timedelta(seconds=1))

    assert share.payload is not None
    assert public_shares.find_usable(share.token) is None


@pytest.mark.django_db
def test_a_live_link_finds_its_share_and_a_wrong_one_does_not():
    share = _share()

    assert public_shares.find_usable(share.token).pk == share.pk
    assert public_shares.find_usable("not-a-token") is None
    assert public_shares.find_usable("x" * 5000) is None


@pytest.mark.django_db
def test_ending_a_link_deletes_the_copy_and_keeps_the_record():
    share = _share()

    public_shares.end(share, PublicRunShare.EndedReason.ENDED)

    share.refresh_from_db()
    assert share.payload is None
    assert share.ended_reason == "ended"
    assert share.ended_at is not None
    assert public_shares.find_usable(share.token) is None


@pytest.mark.django_db
def test_ending_twice_keeps_the_first_reason():
    share = _share()

    public_shares.end(share, PublicRunShare.EndedReason.ENDED)
    public_shares.end(share, PublicRunShare.EndedReason.EXPIRED)

    share.refresh_from_db()
    assert share.ended_reason == "ended"


@pytest.mark.django_db
def test_the_sweep_ends_only_expired_links_and_can_run_again():
    expired = _share(expires_at=timezone.now() - timedelta(minutes=1))
    live = _share()

    assert public_shares.sweep_expired() == 1
    assert public_shares.sweep_expired() == 0

    expired.refresh_from_db()
    live.refresh_from_db()
    assert expired.payload is None
    assert expired.ended_reason == "expired"
    assert live.payload is not None
    assert live.ended_at is None


@pytest.mark.django_db
def test_deleting_a_run_ends_only_that_scopes_links():
    mine = _share()
    # Labs-only records number themselves, so the same run id can exist in another scope.
    other = _share()
    PublicRunShare.objects.filter(pk=other.pk).update(opportunity_id=8)

    public_shares.end_for_run(22, opportunity_id=7, program_id=None, user=None)

    mine.refresh_from_db()
    other.refresh_from_db()
    assert mine.ended_reason == "source_deleted"
    assert other.payload is not None


@pytest.mark.parametrize("days", [1, 7, 30, 90])
def test_the_offered_durations_are_accepted(days):
    now = timezone.now()
    assert public_shares.resolve_expiry(days, None, now=now) == now + timedelta(days=days)


def test_no_duration_means_seven_days():
    now = timezone.now()
    assert public_shares.resolve_expiry(None, None, now=now) == now + timedelta(days=7)


def test_a_chosen_date_lasts_to_the_end_of_that_day():
    now = timezone.now()
    day = (now + timedelta(days=10)).date()
    expires = public_shares.resolve_expiry(None, day.isoformat(), now=now)
    assert expires.date() == day + timedelta(days=1)
    assert (expires.hour, expires.minute) == (0, 0)


@pytest.mark.parametrize("days", [0, -1, 2, 365, "7; drop", "abc", True])
def test_a_duration_not_offered_is_refused(days):
    with pytest.raises(ValueError):
        public_shares.resolve_expiry(days, None)


def test_a_date_in_the_past_too_far_ahead_or_nonsense_is_refused():
    now = timezone.now()
    for until in [
        (now - timedelta(days=1)).date().isoformat(),
        (now + timedelta(days=366)).date().isoformat(),
        "9999-12-31",
        "next week",
        "2026-13-45",
        12345,
    ]:
        with pytest.raises(ValueError):
            public_shares.resolve_expiry(None, until, now=now)
