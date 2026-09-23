"""End-to-end QA for the Photo Verification report's data path, run entirely
against the LOCAL labs DB via two labs-only synthetic opportunities (opp_id
>= 10_000 route to the ORM backend, no prod, no permission checks).

Proves the whole chain the report depends on: seeded AuditSession records ->
sessions-summary endpoint -> ``?ids=`` / ``?created_by=`` filtering ->
per-session assessment_stats that the report render code sums into a pass
rate. The seed helper is the SAME one the management command uses, so the
numbers asserted here are exactly what a human sees when clicking through.
"""

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from connect_labs.audit.photo_verification_demo import demo_org_data, seed_demo


@pytest.fixture
def authed_client(client, db):
    user = get_user_model().objects.create_user(username="tester", password="pw")
    client.force_login(user)
    session = client.session
    # org_data carries the two demo opps under the demo program, so program-mode
    # fan-out (and single-opp reads) both resolve.
    session["labs_oauth"] = {"access_token": "tok", "organization_data": demo_org_data()}
    session.save()
    return client


def _summary(authed_client, opp_id, ids, created_by):
    url = reverse("audit:opportunity_sessions_summary", args=[opp_id])
    resp = authed_client.get(url, {"ids": ids, "created_by": created_by})
    assert resp.status_code == 200
    body = resp.json()
    assert body["success"] is True
    return body["sessions"]


def _totals(sessions):
    p = sum(s["assessment_stats"]["pass"] for s in sessions)
    f = sum(s["assessment_stats"]["fail"] for s in sessions)
    d = sum(s["assessment_stats"]["duplicate_fake"] for s in sessions)
    return p, f, d


class TestPhotoVerificationEndpointLocal:
    def test_eha_opp_filtered_to_abdoul_gives_85_percent(self, authed_client):
        demo = seed_demo()
        sessions = _summary(authed_client, demo["eha_opp_id"], demo["ids_csv"], demo["auditor"])
        p, f, d = _totals(sessions)
        denom = p + f + d
        assert (p, denom) == (17, 20)  # 85.0%
        assert round(p / denom * 100, 2) == 85.0

    def test_c3hd_opp_filtered_to_abdoul_gives_75_percent(self, authed_client):
        demo = seed_demo()
        sessions = _summary(authed_client, demo["c3hd_opp_id"], demo["ids_csv"], demo["auditor"])
        p, f, d = _totals(sessions)
        denom = p + f + d
        assert (p, denom) == (9, 12)  # 75.0%
        assert round(p / denom * 100, 2) == 75.0

    def test_ids_filter_excludes_off_list_audit(self, authed_client):
        # Session on run 9999 (not in the id list) must not appear.
        demo = seed_demo()
        sessions = _summary(authed_client, demo["eha_opp_id"], demo["ids_csv"], demo["auditor"])
        assert all(s["id"] != demo["excluded_by_ids_session_id"] for s in sessions)

    def test_created_by_filter_excludes_other_auditor(self, authed_client):
        # A session on an in-list run (5064) but a different auditor must be dropped.
        demo = seed_demo()
        sessions = _summary(authed_client, demo["eha_opp_id"], demo["ids_csv"], demo["auditor"])
        assert all(s["id"] != demo["excluded_by_auditor_session_id"] for s in sessions)

    def test_matches_by_own_session_id_not_only_run_id(self, authed_client):
        # The C3HD session whose OWN id is in the list (no run link) must be kept.
        demo = seed_demo()
        sessions = _summary(authed_client, demo["c3hd_opp_id"], demo["ids_csv"], demo["auditor"])
        assert demo["own_id_match_session_id"] in {s["id"] for s in sessions}


class TestPhotoAuditReportProgramMode:
    def test_program_endpoint_pools_both_opportunities(self, authed_client):
        # Program-mode fan-out across the demo program's two opps, filtered to
        # Abdoul's audits, gives the pooled program total: 26/32 = 81.25%.
        demo = seed_demo()
        url = reverse("audit:program_sessions_summary", args=[demo["program_id"]])
        resp = authed_client.get(url, {"ids": demo["ids_csv"], "created_by": demo["auditor"]})
        assert resp.status_code == 200
        sessions = resp.json()["sessions"]

        # both opportunities are represented, each session tagged with its opp
        assert {s["opportunity_id"] for s in sessions} == {demo["eha_opp_id"], demo["c3hd_opp_id"]}

        p, f, d = _totals(sessions)
        denom = p + f + d
        assert (p, denom) == (26, 32)
        assert round(p / denom * 100, 2) == 81.25
