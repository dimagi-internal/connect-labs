"""The report surfaces actually render, and the public one is properly scoped.

These drive the real views against a real database rather than asserting on
context dicts: the failure this guards against is a template that raises on a
report with nothing filled in yet, which is the state every report starts in.
"""

from __future__ import annotations

from datetime import datetime
from datetime import timezone as dt_timezone

import pytest
from django.contrib.auth import get_user_model
from django.urls import reverse

from connect_labs.pulse.models import PulseOpportunity, PulseReport, PulseRollup, PulseWork


@pytest.fixture
def author(db):
    return get_user_model().objects.create_user(username="author", password="x")


@pytest.fixture
def opp(db):
    return PulseOpportunity.objects.create(
        opportunity_id=901, name="Cholera response", org_slug="pride", program_id=77, country="NG"
    )


@pytest.fixture
def report(db, opp):
    return PulseReport.objects.create(
        slug="tok123",
        title="Emergency Cholera Response",
        prepared_for="Jacob and Annie Ma-Weaver",
        gift_line="Your $20,000 gift funded the results shown in this report.",
        program_id=77,
        deliverables=[
            {"label": "ORS co-packs", "basis": PulseReport.BASIS_SERVICES, "multiplier": 2},
            {"label": "Referrals", "basis": PulseReport.BASIS_MANUAL, "override": "1777"},
        ],
        site_chips=["Dalori I & II", "Fariya"],
    )


def _seed(opp, *, approved=8, rejected=2, visits=40):
    ts = datetime(2026, 7, 10, tzinfo=dt_timezone.utc)
    for i in range(approved):
        PulseWork.objects.create(
            work_key=f"a{i}",
            opportunity_id=opp.opportunity_id,
            program_id=77,
            org_slug="pride",
            worker_hash=f"w{i % 3}",
            country="NG",
            status="approved",
            created_ts=ts,
            approved_count=1,
            usd_to_worker="1.00",
            usd_to_org="2.20",
        )
    for i in range(rejected):
        PulseWork.objects.create(
            work_key=f"r{i}",
            opportunity_id=opp.opportunity_id,
            program_id=77,
            org_slug="pride",
            worker_hash="w0",
            country="NG",
            status="rejected",
            created_ts=ts,
        )
    if visits:
        PulseRollup.objects.create(bucket_hour=ts, opportunity_id=opp.opportunity_id, status="approved", n=visits)


class TestReportSurface:
    def test_empty_report_still_renders(self, client, db):
        """Every report begins with nothing filled in; that must not 500."""
        PulseReport.objects.create(slug="blank")
        assert client.get(reverse("pulse:report", kwargs={"slug": "blank"})).status_code == 200

    def test_renders_derived_and_manual_figures(self, client, report, opp):
        _seed(opp)
        body = client.get(reverse("pulse:report", kwargs={"slug": "tok123"})).content.decode()

        assert "Emergency Cholera Response" in body
        assert "Jacob and Annie Ma-Weaver" in body
        # 40 verified visits from the rollup, doubled by the ORS multiplier.
        assert "80" in body
        assert "1,777" in body  # the hand-entered override
        assert "Dalori I &amp; II" in body

    def test_verification_rate_is_visit_level_when_rollups_exist(self, client, report, opp):
        _seed(opp, approved=8, rejected=2, visits=0)
        PulseRollup.objects.create(
            bucket_hour=datetime(2026, 7, 10, tzinfo=dt_timezone.utc),
            opportunity_id=opp.opportunity_id,
            status="approved",
            n=90,
        )
        PulseRollup.objects.create(
            bucket_hour=datetime(2026, 7, 10, 1, tzinfo=dt_timezone.utc),
            opportunity_id=opp.opportunity_id,
            status="rejected",
            n=10,
        )
        body = client.get(reverse("pulse:report", kwargs={"slug": "tok123"})).content.decode()
        assert "90.0%" in body

    def test_public_and_not_indexable(self, client, report):
        response = client.get(reverse("pulse:report", kwargs={"slug": "tok123"}))
        assert response.status_code == 200
        assert response["X-Robots-Tag"] == "noindex, nofollow"

    def test_revoked_is_indistinguishable_from_missing(self, client, report):
        report.revoked = True
        report.save()
        assert client.get(reverse("pulse:report", kwargs={"slug": "tok123"})).status_code == 404
        assert client.get(reverse("pulse:report", kwargs={"slug": "never-existed"})).status_code == 404

    def test_partner_name_withheld_when_anonymised(self, client, report, opp):
        from connect_labs.pulse.models import PulseOrganization

        PulseOrganization.objects.create(slug="pride", name="PRIDE")
        _seed(opp)
        report.org_slug = "pride"
        report.show_partner_names = False
        report.save()

        body = client.get(reverse("pulse:report", kwargs={"slug": "tok123"})).content.decode()
        assert "PRIDE" not in body


class TestEditor:
    def test_requires_login(self, client, report):
        response = client.get(reverse("pulse:report_edit", kwargs={"slug": "tok123"}))
        assert response.status_code in (302, 403)

    def test_renders_for_author(self, client, author, report, opp):
        _seed(opp)
        client.force_login(author)
        body = client.get(reverse("pulse:report_edit", kwargs={"slug": "tok123"})).content.decode()
        assert "ORS co-packs" in body
        # Human-entered figures are badged so the author can tell them apart.
        assert "typed" in body

    def test_warns_when_window_has_no_visit_coverage(self, client, author, report, opp):
        """The one warning that matters: the headline silently changes unit."""
        _seed(opp, visits=0)
        client.force_login(author)
        body = client.get(reverse("pulse:report_edit", kwargs={"slug": "tok123"})).content.decode()
        assert "Counting payment units" in body

    def test_save_round_trips_deliverables(self, client, author, report):
        client.force_login(author)
        client.post(
            reverse("pulse:report_edit", kwargs={"slug": "tok123"}),
            {
                "title": "Updated",
                "eyebrow": "",
                "prepared_for": "",
                "gift_line": "",
                "org_slug": "",
                "service_slug": "",
                "intro": "",
                "where_we_worked": "",
                "partner_funding": "",
                "footnote": "",
                "photo_caption": "",
                "program_id": "77",
                "opportunity_id": "",
                "window_start": "2026-06-25",
                "window_end": "2026-07-31",
                "site_chips": "Camp A, Camp B",
                "d_label": ["Aqua Tabs", ""],
                "d_description": ["", ""],
                "d_basis": [PulseReport.BASIS_SERVICES, PulseReport.BASIS_SERVICES],
                "d_multiplier": ["3", "1"],
                "d_override": ["", ""],
            },
        )
        report.refresh_from_db()
        assert report.title == "Updated"
        assert report.site_chips == ["Camp A", "Camp B"]
        # The blank-labelled row is dropped rather than saved as an empty tile.
        assert len(report.deliverables) == 1
        assert report.deliverables[0]["multiplier"] == 3.0
        assert str(report.window_start) == "2026-06-25"

    def test_revoke_from_editor(self, client, author, report):
        client.force_login(author)
        client.post(reverse("pulse:report_edit", kwargs={"slug": "tok123"}), {"action": "revoke"})
        report.refresh_from_db()
        assert report.revoked


class TestList:
    def test_create_seeds_default_deliverables(self, client, author, db):
        client.force_login(author)
        client.post(reverse("pulse:report_list"), {"title": "New report", "program": "77"})
        created = PulseReport.objects.get(title="New report")
        assert created.program_id == 77
        assert created.deliverables, "a new report should start with usable lines"
        assert created.slug
