"""Linking the organisation registry to Connect's own organisations.

The rules are `LabsOrg`'s, fixed before any of this existed; these tests are
about the cases where getting them wrong is silent: a link made on a guess, a
merge Connect did that labs never hears about, two rows quietly folded into one.
"""

import httpx
import pytest
from django.utils import timezone

from connect_labs.labs import connect_orgs
from connect_labs.labs.connect_orgs import ConnectRead, link, read_connect, refresh_cache
from connect_labs.labs.integrations.connect.export_client import ExportAPIError
from connect_labs.labs.models import ConnectOrganization, LabsOrg, Workspace, WorkspaceLLO
from connect_labs.marketplace.models import OrgConnectSlug
from connect_labs.scope_config.models import ScopeConfig

pytestmark = pytest.mark.django_db


def _connect(connect_id, slug, name="", **fields):
    defaults = {"slug": slug, "name": name or slug.replace("-", " ").title(), "last_seen_at": timezone.now()}
    defaults.update(fields)
    return ConnectOrganization.objects.create(connect_id=connect_id, **defaults)


def _org(slug, name=None, **fields):
    return LabsOrg.objects.create(slug=slug, name=name or slug.replace("-", " ").title(), **fields)


def _refreshed(org):
    org.refresh_from_db()
    return org


class TestTheCache:
    def test_a_complete_read_marks_what_it_did_not_see_as_gone(self):
        _connect(1, "kept")
        _connect(2, "vanished")

        refresh_cache(ConnectRead(organizations=[{"id": 1, "slug": "kept", "name": "Kept"}], complete=True))

        assert ConnectOrganization.objects.get(connect_id=1).gone_at is None
        assert ConnectOrganization.objects.get(connect_id=2).gone_at is not None

    def test_a_partial_read_never_marks_anything_gone(self):
        """The poller's own memberships are not Connect's whole list: absence
        from it proves nothing."""
        _connect(2, "not-ours")

        refresh_cache(ConnectRead(organizations=[{"id": 1, "slug": "ours", "name": "Ours"}], complete=False))

        assert ConnectOrganization.objects.get(connect_id=2).gone_at is None
        assert ConnectOrganization.objects.get(connect_id=1).has_profile is False

    def test_a_merge_record_says_where_an_organisation_went(self):
        _connect(1, "old-workspace")
        read = ConnectRead(
            organizations=[{"id": 2, "slug": "survivor", "name": "Survivor"}],
            merges=[{"source_id": 1, "source_slug": "old-workspace", "source_name": "Old", "target_id": 2}],
            complete=True,
        )

        refresh_cache(read)

        merged = ConnectOrganization.objects.get(connect_id=1)
        assert merged.gone_at is not None and merged.merged_into_id == 2

    def test_a_merge_whose_target_was_merged_again_resolves_by_slug(self):
        """Connect nulls a merge record's target when that target is merged in
        turn, leaving its slug: A→B then B→C must still lead A to C."""
        _connect(1, "a")
        _connect(2, "b")
        org = _org("sahel", connect_organization_id=1, connect_organization_slug="a")
        read = ConnectRead(
            organizations=[{"id": 3, "slug": "c", "name": "C"}],
            merges=[
                {"source_id": 1, "source_slug": "a", "target_id": None, "target_slug": "b"},
                {"source_id": 2, "source_slug": "b", "target_id": 3, "target_slug": "c"},
            ],
            complete=True,
        )

        refresh_cache(read)
        link()

        assert ConnectOrganization.objects.get(connect_id=1).merged_into_id == 2
        assert _refreshed(org).connect_organization_id == 3

    def test_slug_ids_of_labs_only_organisations_are_not_cached(self):
        refresh_cache(ConnectRead(organizations=[{"id": "labs-synthetic-x", "slug": "labs-synthetic-x"}]))
        assert not ConnectOrganization.objects.exists()


class TestLinkingAnUnlinkedOrganisation:
    def test_by_its_connect_slug(self):
        _connect(7, "sahel-clinics")
        org = _org("sahel", "Sahel Health", connect_organization_slug="sahel-clinics")

        report = link()

        assert _refreshed(org).connect_organization_id == 7
        assert report.linked == ["sahel → Connect 'sahel-clinics' (7) by slug"]

    def test_by_a_slug_the_directory_attributed_to_it(self):
        _connect(7, "sc-ng-2")
        org = _org("sahel", "Sahel Health")
        OrgConnectSlug.objects.create(org=org, slug="sc-ng-2", why="second workspace")

        link()

        assert _refreshed(org).connect_organization_id == 7

    def test_by_an_exact_name_unique_on_both_sides(self):
        _connect(7, "sahel-health-ng", name="Sahel Health")
        org = _org("sahel", "Sahel Health")

        report = link()

        assert _refreshed(org).connect_organization_id == 7
        assert "by name" in report.linked[0]

    def test_not_by_a_name_two_connect_organisations_share(self):
        _connect(7, "sahel-a", name="Sahel Health")
        _connect(8, "sahel-b", name="Sahel Health")
        org = _org("sahel", "Sahel Health")

        link()

        assert _refreshed(org).connect_organization_id is None

    def test_not_by_name_to_a_test_workspace(self):
        _connect(7, "sahel-test", name="Sahel Health", is_test=True)
        org = _org("sahel", "Sahel Health")

        link()

        assert _refreshed(org).connect_organization_id is None

    def test_never_renames_the_organisation(self):
        """The directory import finds its rows by name: a rename from Connect
        would make the next import mint a duplicate."""
        _connect(7, "sahel-clinics", name="SAHEL CLINICS LTD", short_name="SCL")
        org = _org("sahel", "Sahel Health", connect_organization_slug="sahel-clinics")

        link()

        org = _refreshed(org)
        assert (org.name, org.short_name) == ("Sahel Health", "")

    def test_a_labs_only_organisation_is_never_linked(self):
        _connect(7, "dimagi", name="Dimagi")
        org = _org("labs-synthetic-dimagi", "Dimagi", labs_only=True, connect_organization_slug="dimagi")

        link()

        assert _refreshed(org).connect_organization_id is None


class TestConflictsAreReportedNotResolved:
    def test_two_rows_matching_one_connect_organisation_link_neither(self):
        _connect(7, "sahel-clinics")
        a = _org("sahel-a", "Sahel A", connect_organization_slug="sahel-clinics")
        b = _org("sahel-b", "Sahel B", aliases=["sahel-clinics"])

        report = link()

        assert _refreshed(a).connect_organization_id is None
        assert _refreshed(b).connect_organization_id is None
        assert len(report.conflicts) == 1 and "linked none" in report.conflicts[0]

    def test_a_row_answering_to_two_connect_workspaces_links_neither(self):
        """One partner, two workspaces: Connect's merge tool is the fix, and
        picking one here would attach half the partner's delivery to it."""
        _connect(7, "sc-ng")
        _connect(8, "sc-ke")
        org = _org("sahel", "Sahel", aliases=["sc-ng", "sc-ke"])

        report = link()

        assert _refreshed(org).connect_organization_id is None
        assert "2 Connect organisations" in report.conflicts[0]

    def test_a_match_to_an_organisation_already_linked_elsewhere(self):
        _connect(7, "sahel-clinics")
        holder = _org("holder", connect_organization_id=7, connect_organization_slug="sahel-clinics")
        org = _org("sahel", aliases=["sahel-clinics"])

        report = link()

        assert _refreshed(org).connect_organization_id is None
        assert _refreshed(holder).connect_organization_id == 7
        assert "already linked to holder" in report.conflicts[0]


class TestFollowingConnectMerges:
    def test_a_linked_organisation_follows_its_merge_and_keeps_the_old_slug(self):
        _connect(1, "sahel-old", gone_at=timezone.now(), merged_into_id=2)
        _connect(2, "sahel-clinics")
        org = _org("sahel", connect_organization_id=1, connect_organization_slug="sahel-old")

        report = link()

        org = _refreshed(org)
        assert (org.connect_organization_id, org.connect_organization_slug) == (2, "sahel-clinics")
        assert "sahel-old" in org.aliases, "history recorded under the old slug must still resolve"
        assert len(report.relinked) == 1

    def test_a_chain_of_merges_is_followed_to_the_survivor(self):
        _connect(1, "a", gone_at=timezone.now(), merged_into_id=2)
        _connect(2, "b", gone_at=timezone.now(), merged_into_id=3)
        _connect(3, "c")
        org = _org("sahel", connect_organization_id=1, connect_organization_slug="a")

        link()

        assert _refreshed(org).connect_organization_id == 3

    def test_an_unlinked_slug_of_a_merged_workspace_links_to_the_survivor(self):
        _connect(1, "sahel-old", gone_at=timezone.now(), merged_into_id=2)
        _connect(2, "sahel-clinics")
        org = _org("sahel", connect_organization_slug="sahel-old")

        link()

        assert _refreshed(org).connect_organization_id == 2

    def test_a_merge_onto_an_organisation_another_row_holds_is_a_conflict(self):
        _connect(1, "sahel-old", gone_at=timezone.now(), merged_into_id=2)
        _connect(2, "sahel-clinics")
        follower = _org("follower", connect_organization_id=1, connect_organization_slug="sahel-old")
        holder = _org("holder", connect_organization_id=2, connect_organization_slug="sahel-clinics")

        report = link()

        assert _refreshed(follower).connect_organization_id == 1
        assert _refreshed(holder).connect_organization_id == 2
        assert "follower and holder are one Connect organisation" in report.conflicts[0]

    def test_gone_with_no_merge_is_reported_with_what_hangs_off_its_slug(self):
        _connect(1, "sahel-old", gone_at=timezone.now())
        org = _org("sahel", connect_organization_id=1, connect_organization_slug="sahel-old")
        ScopeConfig.objects.create(scope_type="organization", scope_key="sahel-old", namespace="labs")
        workspace = Workspace.objects.create(org=_org("ws", labs_only=True))
        WorkspaceLLO.objects.create(workspace=workspace, slug="sahel-old")

        report = link()

        assert _refreshed(org).connect_organization_id == 1, "nothing to follow: left for a person"
        assert "recorded no merge" in report.gone[0]
        assert len(report.orphaned_config) == 2

    def test_a_linked_slug_follows_the_connect_row(self):
        _connect(7, "sahel-clinics")
        org = _org("sahel", connect_organization_id=7, connect_organization_slug="stale")

        link()

        assert _refreshed(org).connect_organization_slug == "sahel-clinics"


class TestReadingConnect:
    class _Client:
        base_url = "https://connect.example"

        def __init__(self, refuse=()):
            self.refuse = set(refuse)
            self.http_client = self

        def fetch_all(self, endpoint):
            if endpoint in self.refuse:
                response = httpx.Response(404, request=httpx.Request("GET", endpoint))
                try:
                    response.raise_for_status()
                except httpx.HTTPStatusError as exc:
                    raise ExportAPIError("404") from exc
            if endpoint == connect_orgs.PROFILE_ENDPOINT:
                return [{"id": 1, "slug": "a", "name": "A", "countries": ["NGA"], "team_size": "11-50"}]
            return [{"source_id": 9, "target_id": 1}]

        def get(self, url):
            return httpx.Response(
                200,
                json={"organizations": [{"id": 1, "slug": "a", "name": "A"}]},
                request=httpx.Request("GET", url),
            )

    def test_the_profile_export_is_the_whole_list(self):
        read = read_connect(self._Client())
        assert read.complete and read.merges and not read.notes

    def test_a_refused_profile_export_falls_back_to_the_pollers_organisations(self):
        read = read_connect(self._Client(refuse={connect_orgs.PROFILE_ENDPOINT}))
        assert read.complete is False
        assert read.organizations == [{"id": 1, "slug": "a", "name": "A"}]
        assert "llo_entity_internal_access" in read.notes[0]

    def test_no_merge_export_yet_is_a_note_not_a_failure(self):
        read = read_connect(self._Client(refuse={connect_orgs.MERGE_ENDPOINT}))
        assert read.merges == [] and "merged organisations are reported" in read.notes[0]

    def test_a_dry_run_writes_nothing(self):
        org = _org("a-org", "Elsewhere", connect_organization_slug="a")

        report = connect_orgs.sync(self._Client(), dry_run=True)

        assert report.linked and _refreshed(org).connect_organization_id is None
        assert not ConnectOrganization.objects.exists()
