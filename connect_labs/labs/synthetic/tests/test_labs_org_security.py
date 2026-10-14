"""Exploits found by the independent security review of #2435, each reproduced first.

S1   take over a labs-only organisation by merging your own into it
S2a  a Connect organisation whose slug looks labs-only holds that labs-only key
S2b  a filed opp falls back to a name-derived key when its row changes
S3   unfiled opps' keys, concurrent filing, the irreversible backfill, form dropdowns

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from __future__ import annotations

from io import StringIO
from unittest.mock import patch

import pytest
from django.core.exceptions import PermissionDenied
from django.core.management import call_command
from django.db import connection
from django.test.utils import CaptureQueriesContext

from connect_labs.labs.access import scopes as access_scopes
from connect_labs.labs.access.scopes import SYSTEM, Caller
from connect_labs.labs.models import LabsOrg
from connect_labs.labs.synthetic import org_tree, registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.marketplace.models import OrgMembership
from connect_labs.scope_config import service
from connect_labs.supply_chain.data_access import SupplyDataAccess
from connect_labs.supply_chain.operations import call_operation
from connect_labs.users.models import User

pytestmark = pytest.mark.django_db


@pytest.fixture(autouse=True)
def clear_cache():
    registry.invalidate_cache()
    yield
    registry.invalidate_cache()


def _user(name, **extra):
    return User.objects.create_user(username=name, email=f"{name}@dimagi.com", view_synthetic_opps=True, **extra)


def _opp(opp_id, *, created_by, org_name="Labs Synthetic", program_id=None):
    return SyntheticOpportunity.objects.create(
        opportunity_id=opp_id,
        gdrive_folder_id="f",
        labs_only=True,
        enabled=True,
        org_name=org_name,
        program_id=program_id,
        allowed_domains=["@dimagi.com"],
        created_by=created_by,
    )


def _as(user):
    """Supply access as `user`, naming no programme -- which `may_use` lets through."""
    return SupplyDataAccess(user=user, caller=Caller(user=user))


@pytest.fixture
def victim():
    return _user("victim")


@pytest.fixture
def attacker():
    return _user("attacker")


@pytest.fixture
def victims_org(victim):
    return _opp(10_001, created_by=victim, org_name="Victim Trust").labs_org


@pytest.fixture
def attackers_org(attacker):
    return _opp(10_002, created_by=attacker, org_name="Attacker Trust").labs_org


# --- S1: merge and upsert need ownership ----------------------------------------


def test_s1_an_admin_of_one_labs_only_org_cannot_merge_it_into_anothers(victims_org, attackers_org, attacker):
    with pytest.raises(PermissionDenied):
        call_operation("org_merge", _as(attacker), {"keep_id": victims_org.pk, "merge_id": attackers_org.pk})

    assert LabsOrg.objects.filter(pk=attackers_org.pk).exists()
    assert not service.administers_labs_only_org(Caller(user=attacker), victims_org.slug)


def test_s1_a_membership_moved_by_a_merge_administers_nothing(victims_org, attackers_org, victim, attacker):
    """Even a staff merge must not hand the merged-away row's admins the survivor."""
    from connect_labs.labs.org_merge import merge_orgs

    merge_orgs(keep_id=victims_org.pk, merge_id=attackers_org.pk)

    assert OrgMembership.objects.filter(org=victims_org, user=attacker).exists()  # it moved...
    assert not service.administers_labs_only_org(Caller(user=attacker), victims_org.slug)  # ...and counts for nothing
    assert service.administers_labs_only_org(Caller(user=victim), victims_org.slug)


def test_s1_merging_real_rows_needs_staff_or_an_admin_of_both(attacker):
    keep = LabsOrg.objects.create(slug="sahel-ngo", name="Sahel NGO")
    mine = LabsOrg.objects.create(slug="my-ngo", name="My NGO")
    OrgMembership.objects.create(org=mine, user=attacker, role="admin")

    with pytest.raises(PermissionDenied):
        call_operation("org_merge", _as(attacker), {"keep_id": keep.pk, "merge_id": mine.pk})

    OrgMembership.objects.create(org=keep, user=attacker, role="admin")
    call_operation("org_merge", _as(attacker), {"keep_id": keep.pk, "merge_id": mine.pk})
    assert not LabsOrg.objects.filter(pk=mine.pk).exists()


def test_s1_staff_and_system_still_merge(victims_org, attackers_org):
    staff = _user("ops", is_staff=True)
    call_operation("org_merge", _as(staff), {"keep_id": victims_org.pk, "merge_id": attackers_org.pk})
    a = LabsOrg.objects.create(slug="a-ngo", name="A")
    b = LabsOrg.objects.create(slug="b-ngo", name="B")
    call_operation("org_merge", SupplyDataAccess(caller=SYSTEM), {"keep_id": a.pk, "merge_id": b.pk})


def test_s1_nobody_but_staff_changes_a_labs_only_row(victims_org, victim):
    """Not even its own admin: linking it to Connect is how S2b re-keyed tenants."""
    data = {"slug": victims_org.slug, "name": "Renamed", "connect_organization_id": 4242}
    with pytest.raises(PermissionDenied):
        call_operation("org_upsert", _as(victim), {"data": data})
    victims_org.refresh_from_db()
    assert victims_org.connect_organization_id is None


def test_s1_a_stranger_cannot_rewrite_someone_elses_organisation(attacker):
    LabsOrg.objects.create(slug="sahel-ngo", name="Sahel NGO")

    with pytest.raises(PermissionDenied):
        call_operation("org_upsert", _as(attacker), {"data": {"slug": "sahel-ngo", "name": "Mine now"}})

    assert LabsOrg.objects.get(slug="sahel-ngo").name == "Sahel NGO"


def test_s1_adding_a_new_organisation_still_works(attacker):
    made = call_operation("org_upsert", _as(attacker), {"data": {"slug": "new-partner", "name": "New Partner"}})
    assert made["name"] == "New Partner"


def test_s1_its_admin_and_its_connect_members_still_edit_a_real_organisation(attacker):
    org = LabsOrg.objects.create(slug="sahel-ngo", name="Sahel NGO", connect_organization_id=88)
    with patch("connect_labs.supply_chain.org_access.caller_org_ids", return_value={88}):
        call_operation("org_upsert", _as(attacker), {"data": {"slug": "sahel-ngo", "name": "Sahel NGO Ltd"}})
    OrgMembership.objects.create(org=org, user=attacker, role="admin")
    call_operation("org_upsert", _as(attacker), {"data": {"slug": "sahel-ngo", "name": "Sahel NGO plc"}})
    assert LabsOrg.objects.get(pk=org.pk).name == "Sahel NGO plc"


# --- S2a: labs-only is the registry's word, and Connect slugs cannot claim it -------


def test_s2a_labs_only_is_decided_by_the_registry_row_not_the_tree(victims_org):
    tree_without_the_flag = {"organizations": [{"id": victims_org.slug, "slug": victims_org.slug}]}
    assert service.is_labs_only_org(victims_org.slug, tree_without_the_flag)
    assert victims_org.labs_only


def test_s2a_a_connect_org_with_a_labs_only_slug_holds_nothing(victims_org):
    squatter = User.objects.create_user(username="sq", email="sq@partner.example")
    connect = {"organizations": [{"id": 5150, "slug": victims_org.slug, "name": "Labs Synthetic Squat"}]}

    with patch.object(access_scopes, "_connect_org_data", return_value=connect):
        held = access_scopes.holdings(Caller(user=squatter))

    assert victims_org.slug not in held.org_slugs
    assert access_scopes.may_use(Caller(user=squatter), organization_id=victims_org.slug)


def test_s2a_a_connect_org_with_a_labs_only_slug_is_dropped_from_the_tree(victims_org, rf):
    from django.contrib.sessions.backends.db import SessionStore

    from connect_labs.labs.context import get_org_data

    request = rf.get("/")
    request.user = User.objects.create_user(username="sq", email="sq@partner.example")
    request.session = SessionStore()
    request.session["labs_oauth"] = {
        "organization_data": {"organizations": [{"id": 5150, "slug": victims_org.slug}, {"id": 7, "slug": "real"}]}
    }

    slugs = [o["slug"] for o in get_org_data(request)["organizations"]]

    assert slugs == ["real"]


def test_s2a_neither_self_registration_nor_upsert_mints_a_labs_only_looking_key(attacker):
    from connect_labs.marketplace.identity import mint_new_org, slug_for

    assert not slug_for("Labs Synthetic Victim Trust").startswith(org_tree.SYNTHETIC_ORG_PREFIX)
    assert not mint_new_org("Labs Synthetic Squat").slug.startswith(org_tree.SYNTHETIC_ORG_PREFIX)
    for data in (
        {"slug": "labs-synthetic-squat", "name": "Squat"},
        {"slug": "fine", "name": "Fine", "connect_organization_slug": "labs-synthetic-squat"},
    ):
        with pytest.raises((PermissionDenied, ValueError)):
            call_operation("org_upsert", _as(attacker), {"data": data})


# --- S2b: a filed opp is keyed by its row, never by its name ------------------------


def test_s2b_a_filed_opp_never_falls_back_to_its_name(victim):
    opp = _opp(10_001, created_by=victim, org_name="Labs Synthetic")
    # Its row stops being labs-only (only staff can do this now, but say it happened).
    LabsOrg.objects.filter(pk=opp.labs_org_id).update(connect_organization_id=4242, labs_only=False)
    opp = SyntheticOpportunity.objects.select_related("labs_org").get(pk=opp.pk)

    key = org_tree.synthetic_org_slug(opp)

    assert key != org_tree.legacy_org_slug("Labs Synthetic")
    assert key == org_tree.unfiled_key(opp)
    assert service.is_labs_only_org(key, {})  # so only staff change it


def test_s2b_an_unfiled_opp_gets_a_key_of_its_own():
    opp = SyntheticOpportunity(opportunity_id=10_009, org_name="Labs Synthetic", labs_only=True)
    assert org_tree.synthetic_org_slug(opp) == org_tree.unfiled_key(opp) != org_tree.legacy_org_slug("Labs Synthetic")


# --- S3 ---------------------------------------------------------------------------


def test_s3_a_new_key_avoids_the_legacy_key_of_an_opp_not_yet_filed(victim):
    waiting = _opp(10_001, created_by=victim, org_name="Baobab Trust")
    # As if made by the old code during the deploy window: no row yet.
    SyntheticOpportunity.objects.filter(pk=waiting.pk).update(labs_org=None)
    LabsOrg.objects.filter(slug="labs-synthetic-baobab-trust").delete()

    other = _opp(10_002, created_by=_user("other"), org_name="Baobab Trust")

    assert other.labs_org.slug != "labs-synthetic-baobab-trust"


def test_s3_the_backfill_command_files_what_the_deploy_window_left_and_is_idempotent(victim):
    a = _opp(10_001, created_by=victim, org_name="Window Trust")
    b = _opp(10_002, created_by=victim, org_name="Window Trust")
    SyntheticOpportunity.objects.filter(pk__in=[a.pk, b.pk]).update(labs_org=None)
    LabsOrg.objects.filter(slug__startswith="labs-synthetic-window-trust").delete()

    call_command("labs_only_orgs_backfill", stdout=StringIO())
    call_command("labs_only_orgs_backfill", stdout=StringIO())

    a.refresh_from_db()
    b.refresh_from_db()
    assert a.labs_org_id == b.labs_org_id
    assert a.labs_org.slug == "labs-synthetic-window-trust"
    assert a.labs_org.labs_only
    assert service.administers_labs_only_org(Caller(user=victim), a.labs_org.slug)
    assert LabsOrg.objects.filter(slug__startswith="labs-synthetic-window-trust").count() == 1


def test_s3_filing_is_serialised(victim):
    with CaptureQueriesContext(connection) as queries:
        _opp(10_001, created_by=victim)
    assert any("pg_advisory_xact_lock" in q["sql"] for q in queries.captured_queries)


def test_s3_supply_dropdowns_offer_no_labs_only_organisation(victims_org):
    from connect_labs.supply_chain.network.forms import OrgMergeForm

    form = OrgMergeForm()
    for name in ("keep", "merge"):
        assert victims_org not in form.fields[name].queryset


def test_s3_the_audit_command_lists_each_labs_only_org_with_admins_opps_and_domains(victims_org, victim):
    out = StringIO()
    call_command("labs_only_orgs_report", stdout=out)
    text = out.getvalue()
    assert victims_org.slug in text
    assert "victim" in text
    assert "opps=1" in text and "domains=1" in text


# --- re-review of #2438 -------------------------------------------------------------


def test_a_name_never_mints_an_unfiled_key(victim):
    """`unfiled_key(opp 10502)` is labs-synthetic-unfiled-10502; an org named "Unfiled 10502" must not take it."""
    made = _opp(10_001, created_by=victim, org_name="Unfiled 10502").labs_org

    assert not made.slug.startswith(f"{org_tree.SYNTHETIC_ORG_PREFIX}unfiled-")
    assert org_tree._key_taken("labs-synthetic-unfiled-10502")


def test_the_backfill_never_files_into_an_unfiled_key(victim):
    waiting = _opp(10_001, created_by=victim, org_name="Unfiled 10502")
    SyntheticOpportunity.objects.filter(pk=waiting.pk).update(labs_org=None)
    LabsOrg.objects.filter(pk=waiting.labs_org_id).delete()

    call_command("labs_only_orgs_backfill", stdout=StringIO())

    waiting.refresh_from_db()
    assert waiting.labs_org is not None
    assert not waiting.labs_org.slug.startswith(f"{org_tree.SYNTHETIC_ORG_PREFIX}unfiled-")


def test_an_opp_joins_a_programme_whose_id_is_its_first_opps_own(victim):
    """An opp with no program_id is its own programme (program_id = opportunity_id)."""
    first = _opp(10_050, created_by=victim, org_name="Baobab Trust")
    joining = _opp(10_051, created_by=_user("other"), org_name="Elsewhere", program_id=10_050)

    assert joining.labs_org_id == first.labs_org_id
