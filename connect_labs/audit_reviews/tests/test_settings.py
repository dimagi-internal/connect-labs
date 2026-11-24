"""Editable settings: validated, previewed before saving, versioned, undoable, and applied to every week."""

import pytest

from connect_labs.audit_reviews import service
from connect_labs.audit_reviews.models import AuditSource, Decision, SettingsVersion
from connect_labs.audit_reviews.tests.test_service import CASES, FakeDao, _pending, kc_state, source_for

pytestmark = pytest.mark.django_db

CASE = CASES["go_live_first_run"]
MAIN_A, MAIN_B = "m_on_time_28d", "m_visits_28d"


@pytest.fixture(autouse=True)
def _fake_snapshot_reads(monkeypatch):
    monkeypatch.setattr(service, "read_run_state", lambda run: kc_state(run.kc, CASE["D"]["metrics"]))


@pytest.fixture
def live():
    last = CASE["D"]["runs"][-1]["run_id"]
    source, d = source_for({**CASE, "opts": {"goLiveRunId": last}})
    service.sync(source, FakeDao(CASE), d)
    return source


def _open_new(source):
    return len(_pending(source, "open"))


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "bad,match",
    [
        ({"metrics": {"nope": {"role": "main"}}}, "Unknown metric"),
        ({"metrics": {MAIN_A: {"role": "boss"}}}, "role"),
        ({"metrics": {MAIN_A: {"levels": ["galaxy"]}}}, "levels"),
        ({"metrics": {MAIN_A: {"initiateOn": ["worse_ish"]}}}, "opens a review"),
        ({"metrics": {MAIN_A: {"persistence": 9}}}, "1-8"),
        ({"levelActions": {"flw": ["dance"]}}, "Actions for flw"),
        ({"closeAfter": 0}, "1-8"),
        ({"options": {"Bad Key": {"label": "x", "action": "investigate"}}}, "lower-case"),
        ({"options": {"call": {"label": "", "action": "investigate"}}}, "label"),
        ({"options": {"call": {"label": "Call", "action": "fly"}}}, "unknown action"),
        ({"metrics": {"m_missed_28d": {"parent": MAIN_A}, MAIN_A: {"role": "off"}}}, "parent metric is turned off"),
        ("not an object", "object"),
    ],
)
def test_invalid_settings_are_refused_with_the_reason(live, bad, match):
    with pytest.raises(service.Refused, match=match):
        service.validate_settings(live, bad)


# ---------------------------------------------------------------------------
# Preview, save, undo
# ---------------------------------------------------------------------------


def test_preview_shows_the_change_and_saves_nothing(live):
    before = _open_new(live)
    assert before > 0
    preview = service.preview_settings(live, {"metrics": {MAIN_A: {"role": "detail"}, MAIN_B: {"role": "detail"}}})
    assert preview["before"]["triage_open_new"] == before
    assert preview["after"]["triage_open_new"] == 0 and preview["after"]["main_metrics"] == []
    assert _open_new(live) == before
    assert not SettingsVersion.objects.filter(source=live).exists()


def test_save_versions_and_applies_to_every_week(live):
    ent = _pending(live, "open")[0]
    service.record_decisions(live, [{"kind": "open", "entity": ent}], user=None, by="S")
    saved = service.save_settings(
        live, {"metrics": {MAIN_A: {"role": "off"}, MAIN_B: {"role": "off"}}}, user=None, by="Admin", note="pause"
    )
    assert saved == {"number": 1}
    payload = service.world(live)
    assert _open_new(live) == 0, "no main metric left, so nothing proposes a review"
    assert any(r["ent"] == ent for r in payload["reviews"]), "a review staff opened is kept"
    assert Decision.objects.filter(source=live, entity=ent, kind="open").exists()
    assert payload["settings_history"][0] == {
        **payload["settings_history"][0],
        "number": 1,
        "saved_by": "Admin",
        "note": "pause",
    }
    assert payload["settings"]["metrics"][MAIN_A] == {"role": "off"}


def test_a_stale_edit_is_refused(live):
    service.save_settings(live, {"closeAfter": 3}, user=None, by="A")
    with pytest.raises(service.Refused, match="Someone else saved"):
        service.save_settings(live, {"closeAfter": 4}, user=None, by="B", expected_version=0)
    service.save_settings(live, {"closeAfter": 4}, user=None, by="B", expected_version=1)
    assert AuditSource.objects.get(pk=live.pk).settings["closeAfter"] == 4


def test_undo_restores_the_previous_version_as_a_new_one(live):
    with pytest.raises(service.Refused, match="no earlier version"):
        service.undo_settings(live, user=None, by="A")
    service.save_settings(live, {"closeAfter": 3}, user=None, by="A")
    service.save_settings(live, {"closeAfter": 5}, user=None, by="B")
    assert service.undo_settings(live, user=None, by="C", expected_version=2) == {"number": 3}
    assert AuditSource.objects.get(pk=live.pk).settings["closeAfter"] == 3
    assert service.world(live)["CFG"]["closeAfter"] == 3
    v = SettingsVersion.objects.get(source=live, number=3)
    assert (v.saved_by, v.note) == ("C", "Undid version 2")


def test_settings_in_use_before_the_first_save_become_version_one(live):
    AuditSource.objects.filter(pk=live.pk).update(settings={"closeAfter": 2, "metrics": {}})
    live.refresh_from_db()
    service.save_settings(live, {"closeAfter": 6}, user=None, by="A")
    numbers = list(SettingsVersion.objects.filter(source=live).values_list("number", "settings"))
    assert numbers[0] == (1, {"closeAfter": 2, "metrics": {}}) and numbers[1][0] == 2
    service.undo_settings(live, user=None, by="A")
    assert AuditSource.objects.get(pk=live.pk).settings == {"closeAfter": 2, "metrics": {}}


def test_options_and_level_actions_are_stored_for_the_page(live):
    service.save_settings(
        live,
        {
            "levelActions": {"flw": ["investigate", "share_feedback"], "opportunity": []},
            "options": {
                "home_visit": {
                    "label": "Home visit with the FLW",
                    "action": "investigate",
                    "levels": ["flw"],
                    "custom": True,
                }
            },
            "metrics": {MAIN_A: {"options": ["home_visit"]}},
        },
        user=None,
        by="A",
    )
    payload = service.world(live)
    assert payload["CFG"]["levelActions"]["flw"] == ["investigate", "share_feedback"]
    assert payload["settings"]["options"]["home_visit"]["custom"] is True
