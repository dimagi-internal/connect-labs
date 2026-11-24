"""'Not this week': a reason is required for a proposed new review, and it can be undone."""

import pytest

from connect_labs.audit_reviews import service
from connect_labs.audit_reviews.models import Decision
from connect_labs.audit_reviews.tests.test_service import CASES, FakeDao, _pending, kc_state, source_for

pytestmark = pytest.mark.django_db

CASE = CASES["go_live_first_run"]


@pytest.fixture(autouse=True)
def _fake_snapshot_reads(monkeypatch):
    monkeypatch.setattr(service, "read_run_state", lambda run: kc_state(run.kc, CASE["D"]["metrics"]))


@pytest.fixture
def live():
    last = CASE["D"]["runs"][-1]["run_id"]
    source, d = source_for({**CASE, "opts": {"goLiveRunId": last}})
    service.sync(source, FakeDao(CASE), d)
    return source


def _dismiss(source, ent, note=""):
    return service.record_decisions(source, [{"kind": "dismiss", "entity": ent, "note": note}], user=None, by="Ann")


def _undo(source, ent, note=""):
    return service.record_decisions(
        source, [{"kind": "undo_dismiss", "entity": ent, "note": note}], user=None, by="Ben"
    )


def test_setting_aside_a_new_review_needs_a_reason(live):
    ent = _pending(live, "open")[0]
    for blank in ["", "   ", None]:
        with pytest.raises(service.Refused, match="Say why"):
            _dismiss(live, ent, blank)
    _dismiss(live, ent, "LLO on leave this week; checking in next run")
    assert ent not in _pending(live, "open")
    aside = service.world(live)["set_aside"]
    assert aside == [
        {"entity": ent, "note": "LLO on leave this week; checking in next run", "by": "Ann", "at": aside[0]["at"]}
    ]


def test_setting_aside_several_at_once_refuses_all_if_one_has_no_reason(live):
    ents = _pending(live, "open")[:2]
    with pytest.raises(service.Refused, match="Say why"):
        service.record_decisions(
            live,
            [{"kind": "dismiss", "entity": ents[0], "note": "ok"}, {"kind": "dismiss", "entity": ents[1]}],
            user=None,
            by="Ann",
        )
    assert not Decision.objects.filter(source=live, kind="dismiss").exists()


def test_undo_puts_the_proposal_back_and_keeps_the_record(live):
    ent = _pending(live, "open")[0]
    _dismiss(live, ent, "Not convinced")
    _undo(live, ent, "The LLO lead disagrees: open it")
    assert ent in _pending(live, "open")
    assert service.world(live)["set_aside"] == []
    d = Decision.objects.get(source=live, kind="dismiss", entity=ent)
    assert d.revoked_at is not None and d.revoked_by == "Ben" and d.revoke_note == "The LLO lead disagrees: open it"
    # Now it can be opened, or set aside again.
    service.record_decisions(live, [{"kind": "open", "entity": ent}], user=None, by="Ben")
    assert any(r["ent"] == ent and r["status"] == "open" for r in service.world(live)["reviews"])


def test_a_proposal_can_be_set_aside_again_after_an_undo(live):
    ent = _pending(live, "open")[0]
    _dismiss(live, ent, "first")
    _undo(live, ent)
    _dismiss(live, ent, "second")
    assert Decision.objects.filter(source=live, kind="dismiss", entity=ent).count() == 2
    assert [a["note"] for a in service.world(live)["set_aside"]] == ["second"]


def test_only_something_set_aside_this_week_can_be_undone(live):
    ent = _pending(live, "open")[0]
    with pytest.raises(service.Refused, match="was not set aside this week"):
        _undo(live, ent)
