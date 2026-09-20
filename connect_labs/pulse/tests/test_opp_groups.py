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

from connect_labs.pulse import groups
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
