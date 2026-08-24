"""Per-opportunity progress reporting for the cohort loops (connect-labs#1220).

`synthetic_clone_profile` and `synthetic_clone_generate` did all their work and
returned nothing until the very end, so any MCP client with an idle-output
timeout killed the call mid-flight — Claude Code's default is 300s, so anything
past ~2 opportunities died. The work always completed server-side; the client
just could not tell, so the returned spec (which carries the resolved
bundle_root and the source->clone id mapping) was lost every time.

These tests pin the contract that makes that impossible: the per-opp loops emit
a progress callback per opportunity, and a caller that cannot report progress
must never take the run down with it.
"""

from unittest.mock import patch

import pytest

from connect_labs.labs.synthetic import clone_from_prod
from connect_labs.labs.synthetic.cohort import CohortSpec
from connect_labs.labs.synthetic.tests.test_bundle import _FakeDrive


def _bulk_fetch(opp_id, key):
    visits = [{"username": "a", "visit_date": "2026-05-04", "form_json": {}}] * 4 + [
        {"username": "b", "visit_date": "2026-05-11", "form_json": {}}
    ] * 4
    return {
        "": {"id": opp_id, "name": f"Opp {opp_id}"},
        "user_visits": visits,
        "user_data": [],
        "app_structure": {},
    }[key]


def test_profile_opps_bulk_reports_progress_per_opportunity(tmp_path):
    """One notification per opportunity — that is what resets the client's idle timer."""
    seen = []

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=lambda b, o, k, t: _bulk_fetch(o, k)):
        clone_from_prod.profile_opps_bulk(
            [100, 200, 300],
            base_url="https://x",
            oauth_token="t",
            bundle_root=str(tmp_path),
            progress=lambda progress, total, message: seen.append((progress, total, message)),
        )

    assert len(seen) >= 3, f"expected at least one report per opportunity, got {seen}"
    assert seen[-1][0] == 3, f"final report must show all 3 done: {seen[-1]}"
    assert all(total == 3 for _p, total, _m in seen), f"every report must carry the total: {seen}"


def test_profile_opps_bulk_reports_progress_for_a_failed_opportunity(tmp_path):
    """A skipped opp still advances the counter — otherwise the client's idle
    timer runs on through every failure and the abort looks like a hang."""
    seen = []

    def fake_fetch(base_url, opp_id, key, token):
        if opp_id == 999:
            raise RuntimeError("simulated failure")
        return _bulk_fetch(opp_id, key)

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=fake_fetch):
        _resolved, handles = clone_from_prod.profile_opps_bulk(
            [100, 999, 200],
            base_url="https://x",
            oauth_token="t",
            bundle_root=str(tmp_path),
            progress=lambda progress, total, message: seen.append((progress, total, message)),
        )

    assert len(handles) == 2  # failure isolation is unchanged
    assert [p for p, _t, _m in seen][-1] == 3, f"counter must reach 3 even with a failure: {seen}"


def test_a_broken_progress_callback_never_fails_the_run(tmp_path):
    """Progress is telemetry. A client that has gone away must not destroy work
    that already succeeded — that is the exact failure #1220 is about."""

    def _explode(*_a, **_k):
        raise RuntimeError("client disconnected")

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=lambda b, o, k, t: _bulk_fetch(o, k)):
        _resolved, handles = clone_from_prod.profile_opps_bulk(
            [100, 200],
            base_url="https://x",
            oauth_token="t",
            bundle_root=str(tmp_path),
            progress=_explode,
        )

    assert len(handles) == 2


def test_profile_cohort_forwards_progress():
    """The cohort spec path is the one the client actually calls."""
    seen = []
    drive = _FakeDrive()
    run_folder = drive.create_folder("run", "parent")
    spec = CohortSpec(
        opportunity_ids=[100, 200],
        program_name="KMC (Synthetic)",
        org_name="O",
        bundle_root=f"gdrive:{run_folder}",
    )

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=lambda b, o, k, t: _bulk_fetch(o, k)):
        clone_from_prod.profile_cohort(
            spec,
            base_url="https://x",
            oauth_token="t",
            drive=drive,
            progress=lambda progress, total, message: seen.append((progress, total, message)),
        )

    assert [p for p, _t, _m in seen][-1] == 2, f"expected progress through both opps: {seen}"


@pytest.mark.django_db
def test_generate_opps_bulk_reports_progress_per_bundle(tmp_path, settings, monkeypatch):
    """Phase 2 is the longer of the two — an 11-opp regenerate is where the
    client aborted a run whose work had entirely succeeded."""
    from connect_labs.labs.synthetic.tests.test_clone_phase2 import _bundle
    from connect_labs.labs.synthetic.tests.test_clone_phase2 import _FakeDrive as _Phase2Drive

    settings.LABS_SYNTHETIC_GDRIVE_PARENT_FOLDER_ID = "parent"
    _bundle(tmp_path)
    seen = []

    monkeypatch.setattr(
        clone_from_prod, "_fetch_endpoint", lambda *a, **k: (_ for _ in ()).throw(AssertionError("Phase 2 hit prod!"))
    )
    clone_from_prod.generate_opps_bulk(
        str(tmp_path),
        drive=_Phase2Drive(),
        progress=lambda progress, total, message: seen.append((progress, total, message)),
    )

    assert seen, "generate_opps_bulk emitted no progress at all"
    assert seen[-1][0] == seen[-1][1], f"final report must show every bundle done: {seen[-1]}"


def test_a_single_opportunity_reports_progress_between_its_stages(tmp_path):
    """Per-opp notifications keep a COHORT alive; they do nothing for ONE big opp.

    profile_opps_bulk emits once per opportunity, so a cohort of small opps survives.
    But a single large opportunity emits nothing between its first byte and its last,
    and the client's idle timer expires mid-call — aborting a run the server would have
    finished. Opp 874 (11,581 visits) is the case that exposed it: every smaller KMC opp
    profiles inside the 300s window and it does not.
    """
    seen = []

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=lambda b, o, k, t: _bulk_fetch(o, k)):
        clone_from_prod.profile_opp_to_bundle(
            874,
            base_url="https://example.invalid",
            oauth_token="t",
            store=clone_from_prod.make_bundle_store(str(tmp_path)),
            progress=lambda done, total, msg=None: seen.append((done, total, msg)),
        )

    # Every fetch is its own stage, and the manifest build is bracketed by two of
    # them -- the CPU-heavy stretch must not be entered without a notification
    # immediately before it.
    assert len(seen) >= 6, seen
    assert [d for d, _, _ in seen] == sorted(d for d, _, _ in seen), "progress must be monotonic"
    assert all(t == 6 for _, t, _ in seen), seen
    assert any("visits" in (m or "") for _, _, m in seen), "visit count is the useful signal here"


def test_a_caller_that_cannot_report_progress_does_not_take_the_profile_down(tmp_path):
    """Telemetry must never fail the call it exists to keep alive."""

    def _explode(*_a, **_k):
        raise RuntimeError("session gone")

    with patch.object(clone_from_prod, "_fetch_endpoint", side_effect=lambda b, o, k, t: _bulk_fetch(o, k)):
        handle = clone_from_prod.profile_opp_to_bundle(
            874,
            base_url="https://example.invalid",
            oauth_token="t",
            store=clone_from_prod.make_bundle_store(str(tmp_path)),
            progress=_explode,
        )

    assert handle
