"""Several Connect opportunities that were really one engagement.

Connect has no grouping for opportunities, so an engagement run as a series of
cohorts — the Connect Interviews work, 37 opportunities for one partner and 35
for another — arrives as that many unrelated engagements. Every figure, count
and cost then repeats the split.

The organisation and cohort names here are invented. This repository is public
and the behaviour is what these tests are about; real partner identity lives in
the directory, never in a fixture.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from django.utils import timezone

from connect_labs.marketplace import directory
from connect_labs.pulse import groups
from connect_labs.pulse.management.commands.pulse_group_opps import apply_groups
from connect_labs.pulse.models import PulseOppGroup, PulseOpportunity


@pytest.mark.django_db
class TestTheGroupRecord:
    def test_a_group_needs_a_stated_reason(self):
        """Unexplained, a grouping is a guess a later reader trusts."""
        with pytest.raises(ValueError):
            PulseOppGroup.objects.create(slug="frht-interviews", name="FRHT Interviews", org_slug="frht", why="  ")

    def test_an_opportunity_carries_its_group(self):
        group = PulseOppGroup.objects.create(
            slug="frht-interviews", name="FRHT Interviews", org_slug="frht", why="one engagement, many cohorts"
        )
        opp = PulseOpportunity.objects.create(opportunity_id=1, name="[01] FRHT Interviews", org_slug="frht")
        opp.group = group
        opp.save(update_fields=["group"])
        assert list(group.members.values_list("opportunity_id", flat=True)) == [1]

    def test_deleting_a_group_leaves_its_opportunities(self):
        """The grouping is a reading of the data; undoing it must not lose a cohort."""
        group = PulseOppGroup.objects.create(
            slug="frht-interviews", name="FRHT Interviews", org_slug="frht", why="one engagement"
        )
        PulseOpportunity.objects.create(opportunity_id=1, name="[01]", org_slug="frht", group=group)
        group.delete()
        assert PulseOpportunity.objects.get(opportunity_id=1).group_id is None


@pytest.fixture
def grouped(db):
    """Two cohorts of one engagement, and one ordinary opportunity beside them."""
    group = PulseOppGroup.objects.create(
        slug="frht-interviews", name="FRHT Interviews", org_slug="frht", why="one engagement, many cohorts"
    )
    for oid, visits in ((11, 100), (12, 50)):
        PulseOpportunity.objects.create(
            opportunity_id=oid,
            name=f"[{oid}] FRHT Interviews",
            org_slug="frht",
            service_slug="interview",
            lifetime_visit_count=visits,
            group=group,
        )
    PulseOpportunity.objects.create(
        opportunity_id=20, name="FRHT CHC", org_slug="frht", service_slug="chc", lifetime_visit_count=5
    )
    groups.invalidate()
    return group


@pytest.mark.django_db
class TestResolution:
    def test_a_member_resolves_to_its_group(self, grouped):
        assert groups.key_for(11) == "frht-interviews"

    def test_an_ungrouped_opportunity_is_its_own_key(self, grouped):
        assert groups.key_for(20) == 20

    def test_a_key_expands_to_every_member(self, grouped):
        assert sorted(groups.members("frht-interviews")) == [11, 12]

    def test_an_opportunity_id_expands_to_itself(self, grouped):
        assert groups.members(20) == [20]

    def test_resolve_takes_a_group_slug_or_an_opportunity_id(self, grouped):
        assert groups.resolve("frht-interviews").slug == "frht-interviews"
        assert groups.resolve("20").opportunity_id == 20
        assert groups.resolve("nope") is None
        assert groups.resolve("") is None

    def test_resolving_a_members_own_id_gives_the_group(self, grouped):
        """Two links to the same work must not report different figures."""
        assert groups.resolve("11").slug == "frht-interviews"

    def test_a_group_created_after_the_cache_warmed_is_seen(self, grouped):
        """`partner_names` learned this: a minute of staleness is fine, a test
        that passes alone and fails in a suite is not."""
        groups.key_for(20)
        second = PulseOppGroup.objects.create(slug="later", name="Later", org_slug="frht", why="because")
        PulseOpportunity.objects.filter(opportunity_id=20).update(group=second)
        groups.invalidate()
        assert groups.key_for(20) == "later"


@pytest.mark.django_db
class TestCollapse:
    def test_members_fold_into_one_row_with_summed_figures(self, grouped):
        rows = [
            {"id": 11, "name": "[11]", "visits": 100, "approved": 8, "works": 10, "usd_total": 30.0, "spark": [1, 2]},
            {"id": 12, "name": "[12]", "visits": 50, "approved": 2, "works": 10, "usd_total": 10.0, "spark": [3, 0]},
            {"id": 20, "name": "FRHT CHC", "visits": 5, "approved": 1, "works": 1, "usd_total": 1.0, "spark": [0, 1]},
        ]
        folded = {r["id"]: r for r in groups.collapse(rows)}
        assert set(folded) == {"frht-interviews", 20}
        row = folded["frht-interviews"]
        assert row["name"] == "FRHT Interviews"
        assert row["visits"] == 150
        assert row["usd_total"] == pytest.approx(40.0)
        assert row["spark"] == [4, 2]
        assert row["members"] == [11, 12]
        assert folded[20]["name"] == "FRHT CHC"

    def test_a_rate_is_recomputed_from_the_summed_parts(self, grouped):
        """Averaging two rates weighs a 2-unit cohort like a 2,000-unit one."""
        rows = [
            {"id": 11, "approved": 8, "works": 10, "usd_total": 80.0, "rate": 10.0, "approval_rate": 0.8},
            {"id": 12, "approved": 2, "works": 10, "usd_total": 100.0, "rate": 50.0, "approval_rate": 0.2},
        ]
        (row,) = groups.collapse(rows)
        assert row["rate"] == pytest.approx(18.0)  # $180 over 10 approved units
        assert row["approval_rate"] == pytest.approx(0.5)  # 10 approved of 20

    def test_the_engagement_is_live_if_any_cohort_is(self, grouped):
        rows = [
            {"id": 11, "active": False, "last_ts": 100},
            {"id": 12, "active": True, "last_ts": 500},
        ]
        (row,) = groups.collapse(rows)
        assert row["active"] is True
        assert row["last_ts"] == 500

    def test_an_ungrouped_row_is_passed_through_untouched(self, grouped):
        rows = [{"id": 20, "name": "FRHT CHC", "visits": 5}]
        assert groups.collapse(rows) == rows


def _deliver(opp_id, *, org="frht", visits=1, usd="2.00", approved=1, when=None):
    """One service and one paid unit of work on an opportunity."""
    from connect_labs.pulse.models import PulseEvent, PulseWork

    now = when or timezone.now()
    PulseEvent.objects.create(
        connect_visit_id=opp_id * 100 + visits,
        opportunity_id=opp_id,
        org_slug=org,
        field_ts=now,
        sync_ts=now,
        lat=11.0,
        lon=7.6,
        country="NG",
        status="approved",
        service_slug="interview",
    )
    PulseWork.objects.create(
        work_key=f"{opp_id:0>60}{visits:0>4}",
        opportunity_id=opp_id,
        org_slug=org,
        status="approved",
        approved_count=approved,
        created_ts=now - timedelta(days=1),
        service_slug="interview",
        country="NG",
        usd_to_worker=usd,
        usd_to_org="0.00",
    )


@pytest.fixture
def viewer(client, django_user_model):
    """Partner identity needs a session; these tests are about scoping."""
    client.force_login(django_user_model.objects.create(username="viewer"))
    return client


@pytest.fixture
def delivering(grouped):
    """Each cohort delivers, so every spine has something to narrow."""
    _deliver(11, visits=1, usd="8.00", approved=8)
    _deliver(12, visits=1, usd="2.00", approved=2)
    _deliver(20, visits=1, usd="1.00", approved=1)
    return grouped


def summary(client, **params):
    from django.urls import reverse

    return client.get(reverse("pulse:api_summary"), params).json()


@pytest.mark.django_db
class TestScopingToAnEngagement:
    def test_the_engagement_covers_every_cohort(self, viewer, delivering):
        data = summary(viewer, opportunity="frht-interviews")
        assert data["scope"]["opportunities"] == 1
        assert data["scope"]["lifetime_visits"] == 150
        assert data["stored"]["events"] == 2
        assert data["money"]["works"] == 2

    def test_a_cohorts_own_id_gives_the_same_answer(self, viewer, delivering):
        """A link to a cohort and a link to its engagement are the same work."""
        assert summary(viewer, opportunity="11")["scope"] == summary(viewer, opportunity="frht-interviews")["scope"]

    def test_an_ungrouped_opportunity_is_unchanged(self, viewer, delivering):
        data = summary(viewer, opportunity="20")
        assert data["scope"]["lifetime_visits"] == 5
        assert data["stored"]["events"] == 1

    def test_the_partners_count_counts_the_engagement_once(self, viewer, delivering):
        """Two cohorts and one other opportunity is two engagements, not three."""
        assert summary(viewer, org="frht")["scope"]["opportunities"] == 2

    def test_an_unknown_key_is_ignored_not_an_error(self, viewer, delivering):
        """A stale link degrades to the unfiltered display, not a 500."""
        data = summary(viewer, opportunity="no-such-engagement")
        assert data["stored"]["events"] == 3

    def test_the_selection_is_reported_as_the_engagement(self, viewer, delivering):
        """The page marks the selected row, and the row is the engagement."""
        from django.urls import reverse

        data = viewer.get(reverse("pulse:api_partner"), {"org": "frht", "opportunity": "11"}).json()
        assert data["selected_opportunity"] == "frht-interviews"


@pytest.mark.django_db
class TestTheRoster:
    def test_lists_one_row_for_the_engagement(self, viewer, delivering):
        from django.urls import reverse

        data = viewer.get(reverse("pulse:api_partner"), {"org": "frht"}).json()
        rows = {r["id"]: r for r in data["opportunities"]}
        assert set(rows) == {"frht-interviews", 20}
        assert rows["frht-interviews"]["name"] == "FRHT Interviews"
        assert rows["frht-interviews"]["visits"] == 150
        assert rows["frht-interviews"]["members"] == [11, 12]
        assert rows["frht-interviews"]["usd_total"] == pytest.approx(10.0)

    def test_the_dossier_index_offers_the_engagement_not_its_cohorts(self, viewer, delivering):
        from django.urls import reverse

        page = viewer.get(reverse("pulse:index")).content.decode()
        assert "FRHT Interviews" in page
        assert "[11] FRHT Interviews" not in page
        # Someone who knows the work as "[11]" must still find it.
        assert "[11] frht interviews" in page.lower()


@pytest.mark.django_db
class TestTheEngagementPage:
    def test_a_cohorts_page_redirects_to_its_engagement(self, viewer, delivering):
        from django.urls import reverse

        res = viewer.get(reverse("pulse:opp", args=[11]))
        assert res.status_code == 302
        assert res["Location"] == reverse("pulse:opp_group", args=["frht-interviews"])

    def test_an_ungrouped_opportunity_keeps_its_own_page(self, viewer, delivering):
        from django.urls import reverse

        assert viewer.get(reverse("pulse:opp", args=[20])).status_code == 200

    def test_the_engagement_has_a_page(self, viewer, delivering):
        from django.urls import reverse

        page = viewer.get(reverse("pulse:opp_group", args=["frht-interviews"])).content.decode()
        assert "FRHT Interviews" in page

    def test_the_api_covers_every_cohort_and_lists_them(self, viewer, delivering):
        from django.urls import reverse

        data = viewer.get(reverse("pulse:api_opp"), {"id": "frht-interviews"}).json()
        assert data["opp"]["name"] == "FRHT Interviews"
        assert data["opp"]["lifetime_visits"] == 150
        assert data["totals"]["events"] == 2
        assert [c["id"] for c in data["opp"]["cohorts"]] == [11, 12]

    def test_an_ordinary_opportunity_lists_no_cohorts(self, viewer, delivering):
        from django.urls import reverse

        data = viewer.get(reverse("pulse:api_opp"), {"id": "20"}).json()
        assert data["opp"]["cohorts"] == []
        assert data["opp"]["lifetime_visits"] == 5


@pytest.mark.django_db
class TestCostsForAnEngagement:
    def test_a_fee_entered_once_spreads_across_the_cohorts(self, delivering):
        """The fee was agreed once; splitting it by hand would invent a split."""
        from decimal import Decimal

        from connect_labs.pulse import costs
        from connect_labs.pulse.models import PulseCostEntry

        group = PulseOppGroup.objects.get(slug="frht-interviews")
        PulseCostEntry.objects.create(group=group, kind=PulseCostEntry.KIND_ORG_FEE, usd="100.00", reason="agreed fee")
        by_opp = costs.opportunity_costs()
        # 8 approved units on one cohort and 2 on the other: $10 a unit.
        assert by_opp[11].fixed_usd == Decimal("80.00")
        assert by_opp[12].fixed_usd == Decimal("20.00")
        assert by_opp[20].fixed_usd == Decimal("0")

    def test_an_entry_names_an_opportunity_or_an_engagement_not_both(self, delivering):
        from connect_labs.pulse.models import PulseCostEntry

        group = PulseOppGroup.objects.get(slug="frht-interviews")
        with pytest.raises(ValueError):
            PulseCostEntry.objects.create(opportunity_id=11, group=group, kind="fixed", usd="1.00", reason="x")
        with pytest.raises(ValueError):
            PulseCostEntry.objects.create(kind="fixed", usd="1.00", reason="x")

    def test_one_question_about_the_engagement_not_one_per_cohort(self, db):
        """Asked per cohort, the same question appeared 37 times -- and each
        cohort's share fell under the threshold that decides it is worth
        asking at all."""
        from connect_labs.pulse import costs

        group = PulseOppGroup.objects.create(
            slug="frht-interviews", name="FRHT Interviews", org_slug="frht", why="one engagement"
        )
        for oid in (11, 12):
            PulseOpportunity.objects.create(
                opportunity_id=oid,
                name=f"[{oid}] FRHT Interviews",
                org_slug="frht",
                service_slug="interview",
                invoices_synced_at=timezone.now(),
                group=group,
            )
        groups.invalidate()
        # $150 each: under the $200 threshold alone, $300 together.
        _deliver(11, usd="150.00", approved=50)
        _deliver(12, usd="150.00", approved=50)

        rows = [r for r in costs.cost_issues() if r["kind"] == "no_org_pay"]
        assert [r["opportunity_id"] for r in rows] == ["frht-interviews"]
        assert rows[0]["opportunity"] == "FRHT Interviews"
        assert rows[0]["cohorts"] == 2
        assert rows[0]["amount_usd"] == pytest.approx(300)

    def test_an_entry_on_the_engagement_settles_the_question(self, db):
        from connect_labs.pulse import costs
        from connect_labs.pulse.models import PulseCostEntry

        group = PulseOppGroup.objects.create(
            slug="frht-interviews", name="FRHT Interviews", org_slug="frht", why="one engagement"
        )
        PulseOpportunity.objects.create(
            opportunity_id=11,
            name="[11] FRHT Interviews",
            org_slug="frht",
            service_slug="interview",
            invoices_synced_at=timezone.now(),
            group=group,
        )
        groups.invalidate()
        _deliver(11, usd="300.00", approved=50)
        assert [r["kind"] for r in costs.cost_issues()] == ["no_org_pay"]

        PulseCostEntry.objects.create(group=group, kind=PulseCostEntry.KIND_ORG_FEE, usd="100.00", reason="agreed fee")
        assert [r["kind"] for r in costs.cost_issues() if r["kind"] == "no_org_pay"] == []


@pytest.mark.django_db
class TestApplyingTheSheet:
    """The groupings live on the LLO Directory, like every other verdict labs
    cannot infer. This command only applies what the tab says."""

    @pytest.fixture
    def to_group(self, db):
        for oid, visits, name in (
            (11, 100, "[11] FRHT Interviews"),
            (12, 50, "INT - NG - FRHT - Jul26"),
            (13, 41, "Interviews UAT FRHT"),
        ):
            PulseOpportunity.objects.create(
                opportunity_id=oid,
                name=name,
                org_slug="frht",
                service_slug="interview",
                lifetime_visit_count=visits,
                is_test=oid == 13,
            )
        PulseOpportunity.objects.create(
            opportunity_id=20, name="FRHT CHC", org_slug="frht", service_slug="chc", lifetime_visit_count=5
        )
        groups.invalidate()

    @staticmethod
    def rows(*members):
        head = [directory.OPPORTUNITY_GROUPS_HEADER]
        return head + [list(m) for m in members]

    def parsed(self, *members):
        parsed, skipped = directory.parse_opportunity_groups(self.rows(*members))
        return parsed, skipped

    def test_applies_the_membership_the_tab_states(self, to_group):
        parsed, _ = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
            ["frht-interviews", "FRHT Interviews", "frht", "12", "Jul26", "one engagement", "Jonathan"],
        )
        apply_groups(parsed)
        group = PulseOppGroup.objects.get(slug="frht-interviews")
        assert sorted(group.members.values_list("opportunity_id", flat=True)) == [11, 12]
        assert group.why == "one engagement"
        assert PulseOpportunity.objects.get(opportunity_id=20).group_id is None

    def test_a_row_with_no_stated_reason_is_refused(self, to_group):
        parsed, skipped = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "", "Jonathan"],
        )
        assert parsed == {}
        assert "no stated reason" in skipped[0]

    def test_a_test_opportunity_is_reported_not_grouped(self, to_group):
        """A UAT run is out of every Pulse figure already; grouping it would put
        scaffolding inside a real engagement's membership."""
        parsed, _ = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
            ["frht-interviews", "FRHT Interviews", "frht", "13", "UAT", "one engagement", "Jonathan"],
        )
        report = apply_groups(parsed)
        assert sorted(
            PulseOppGroup.objects.get(slug="frht-interviews").members.values_list("opportunity_id", flat=True)
        ) == [11]
        assert any("is a test" in line for line in report["skipped"])

    def test_an_opportunity_labs_has_never_seen_is_reported(self, to_group):
        parsed, _ = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
            ["frht-interviews", "FRHT Interviews", "frht", "999", "ghost", "one engagement", "Jonathan"],
        )
        report = apply_groups(parsed)
        assert any("999 is not in Pulse" in line for line in report["skipped"])

    def test_an_opportunity_of_another_partner_is_refused(self, to_group):
        parsed, _ = self.parsed(
            ["other-interviews", "Other Interviews", "someone-else", "11", "[11]", "one engagement", "Jonathan"],
        )
        report = apply_groups(parsed)
        assert any("belongs to 'frht'" in line for line in report["skipped"])
        assert PulseOpportunity.objects.get(opportunity_id=11).group_id is None

    def test_a_group_cannot_span_two_partners(self, to_group):
        parsed, skipped = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
            ["frht-interviews", "FRHT Interviews", "someone-else", "12", "Jul26", "one engagement", "Jonathan"],
        )
        assert parsed["frht-interviews"].members == [11]
        assert any("cannot be added" in line for line in skipped)

    def test_an_opportunity_dropped_from_the_tab_leaves_the_group(self, to_group):
        """The tab is authoritative in both directions, or a correction there
        would never reach labs."""
        both, _ = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
            ["frht-interviews", "FRHT Interviews", "frht", "12", "Jul26", "one engagement", "Jonathan"],
        )
        apply_groups(both)
        fewer, _ = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
        )
        report = apply_groups(fewer)
        assert report["detached"] == 1
        assert PulseOpportunity.objects.get(opportunity_id=12).group_id is None

    def test_a_dry_run_writes_nothing(self, to_group):
        parsed, _ = self.parsed(
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
        )
        apply_groups(parsed, dry_run=True)
        assert not PulseOppGroup.objects.exists()

    def test_an_empty_read_is_a_failed_read(self, to_group):
        with pytest.raises(ValueError):
            apply_groups({})

    def test_a_guidance_row_is_not_a_verdict(self, to_group):
        parsed, skipped = self.parsed(
            ["# one row per opportunity", "", "", "", "", "", ""],
            ["frht-interviews", "FRHT Interviews", "frht", "11", "[11]", "one engagement", "Jonathan"],
        )
        assert list(parsed) == ["frht-interviews"]
        assert skipped == []


@pytest.mark.django_db
class TestTheIngestLeavesMembershipAlone:
    """Connect has no grouping to mirror, so every sync could quietly undo one.

    `refresh_opportunities` rewrites the opportunities Connect lists and
    `reclassify_opportunities` rewrites every stored one; membership is labs'
    own reading of the data and must survive both.
    """

    def test_a_refresh_leaves_membership_alone(self, grouped, monkeypatch):
        from connect_labs.pulse import ingest

        payload = {
            "organizations": [{"id": 1, "slug": "frht", "name": "FRHT", "funder": ""}],
            "programs": [{"id": 10, "name": "Interviews", "delivery_type": "interview", "organization": "frht"}],
            "opportunities": [
                {
                    "id": 11,
                    "name": "[11] FRHT Interviews — renamed upstream",
                    "organization": "frht",
                    "program": 10,
                    "is_active": True,
                    "visit_count": 120,
                }
            ],
        }
        monkeypatch.setattr("connect_labs.pulse.client.fetch_json", lambda *a, **k: payload)
        ingest.refresh_opportunities(object())

        opp = PulseOpportunity.objects.get(opportunity_id=11)
        assert opp.name.endswith("renamed upstream")  # the sync did run
        assert opp.group_id is not None

    def test_a_reclassify_leaves_membership_alone(self, grouped):
        from connect_labs.pulse import ingest

        ingest.reclassify_opportunities()
        assert PulseOpportunity.objects.get(opportunity_id=11).group_id is not None
