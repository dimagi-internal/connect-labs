"""A labs-only organisation is a `LabsOrg` registry row, not a name.

It used to be the bucket `labs-synthetic-<slug of org_name>`: every synthetic
opportunity anyone gave the same organisation name fell into one organisation,
and renaming one moved it. These tests pin the identity to the registry row.

THIS REPOSITORY IS PUBLIC. Every name here is invented.
"""

from __future__ import annotations

import importlib

import pytest
from django.apps import apps as global_apps

from connect_labs.labs.context import _merge_labs_only_opps
from connect_labs.labs.models import LabsOrg
from connect_labs.labs.synthetic import org_tree, registry
from connect_labs.labs.synthetic.models import SyntheticOpportunity
from connect_labs.marketplace.models import OrgMembership
from connect_labs.scope_config.models import ScopeConfig
from connect_labs.users.models import User


@pytest.fixture(autouse=True)
def clear_cache():
    registry.invalidate_cache()
    yield
    registry.invalidate_cache()


def _user(db, name):
    return User.objects.create_user(username=name, email=f"{name}@dimagi.com", view_synthetic_opps=True)


@pytest.fixture
def ana(db):
    return _user(db, "ana")


@pytest.fixture
def ben(db):
    return _user(db, "ben")


def _opp(opp_id, *, org_name="Labs Synthetic", created_by=None, program_id=None):
    return SyntheticOpportunity.objects.create(
        opportunity_id=opp_id,
        gdrive_folder_id="f",
        labs_only=True,
        enabled=True,
        org_name=org_name,
        program_name="Prog",
        program_id=program_id,
        allowed_domains=["@dimagi.com"],
        created_by=created_by,
    )


@pytest.mark.django_db
def test_a_new_labs_only_opp_is_filed_under_a_registry_row(ana):
    opp = _opp(10_001, org_name="Baobab Trust", created_by=ana)

    assert opp.labs_org is not None
    assert opp.labs_org.connect_organization_id is None
    assert opp.labs_org.name == "Baobab Trust"
    # The key the tree, Settings and page addresses use is the row's own slug.
    assert org_tree.synthetic_org_slug(opp) == opp.labs_org.slug
    assert opp.labs_org.slug.startswith(org_tree.SYNTHETIC_ORG_PREFIX)
    # Whoever made it administers it, which is what lets them change its settings.
    assert OrgMembership.objects.filter(org=opp.labs_org, user=ana, role="admin").exists()


@pytest.mark.django_db
def test_two_people_naming_an_org_alike_get_two_organisations(ana, ben):
    mine = _opp(10_001, created_by=ana)
    theirs = _opp(10_002, created_by=ben)

    assert mine.labs_org_id != theirs.labs_org_id
    assert org_tree.synthetic_org_slug(mine) != org_tree.synthetic_org_slug(theirs)

    tree = _merge_labs_only_opps({}, ana)
    keys = sorted(o["slug"] for o in tree["organizations"])
    assert keys == sorted([mine.labs_org.slug, theirs.labs_org.slug])
    # Both still read as the name they were given.
    assert {o["name"] for o in tree["organizations"]} == {"Labs Synthetic"}
    by_program = {p["id"]: p["organization"] for p in tree["programs"]}
    assert by_program[10_001] != by_program[10_002]


@pytest.mark.django_db
def test_one_persons_opps_with_the_same_org_name_share_it(ana):
    first = _opp(10_001, org_name="Baobab Trust", created_by=ana)
    second = _opp(10_002, org_name="Baobab Trust", created_by=ana)

    assert first.labs_org_id == second.labs_org_id


@pytest.mark.django_db
def test_an_opp_joins_its_programmes_organisation(ana, ben):
    lead = _opp(10_001, org_name="Baobab Trust", created_by=ana, program_id=10_050)
    joining = _opp(10_002, org_name="Something Else", created_by=ben, program_id=10_050)

    assert joining.labs_org_id == lead.labs_org_id


@pytest.mark.django_db
def test_renaming_the_org_name_does_not_move_the_opp(ana):
    opp = _opp(10_001, org_name="Baobab Trust", created_by=ana)
    org = opp.labs_org_id

    opp.org_name = "Renamed Trust"
    opp.save()
    opp.refresh_from_db()

    assert opp.labs_org_id == org


@pytest.mark.django_db
def test_a_key_once_used_is_not_handed_to_a_new_organisation(ana, ben):
    # Settings written for an organisation that has since gone keep their key.
    ScopeConfig.objects.create(scope_type="organization", scope_key="labs-synthetic-baobab-trust", namespace="labs")
    LabsOrg.objects.create(slug="labs-synthetic-baobab-trust-2", name="Taken")

    opp = _opp(10_001, org_name="Baobab Trust", created_by=ana)

    assert opp.labs_org.slug not in {"labs-synthetic-baobab-trust", "labs-synthetic-baobab-trust-2"}
    assert opp.labs_org.slug.startswith("labs-synthetic-baobab-trust")


@pytest.mark.django_db
def test_an_opp_pointed_at_a_real_organisation_never_names_it(ana):
    """A labs-only opp must not make its viewers holders of a real Connect organisation."""
    real = LabsOrg.objects.create(slug="real-ngo", name="Real NGO", connect_organization_id=4242)
    opp = _opp(10_001, created_by=ana)
    SyntheticOpportunity.objects.filter(pk=opp.pk).update(labs_org=real)
    opp.refresh_from_db()

    key = org_tree.synthetic_org_slug(opp)

    assert key.startswith(org_tree.SYNTHETIC_ORG_PREFIX)
    assert key != "real-ngo"


@pytest.mark.django_db
def test_the_backfill_keeps_each_existing_bucket_and_its_key(ana, ben):
    """Existing buckets become registry rows under the key they already had,
    so every setting, cohort and link that names them keeps working."""
    shared_a = _opp(10_001, created_by=ana)
    shared_b = _opp(10_002, created_by=ben)
    solo = _opp(10_003, org_name="Baobab Trust", created_by=ana)
    # As they stood before this change: no registry row.
    SyntheticOpportunity.objects.update(labs_org=None)
    LabsOrg.objects.all().delete()
    OrgMembership.objects.all().delete()
    # A row that already holds a bucket's key is that bucket's row.
    existing = LabsOrg.objects.create(slug="labs-synthetic-baobab-trust", name="Baobab Trust")

    migration = importlib.import_module("connect_labs.labs.synthetic.migrations.0010_syntheticopportunity_labs_org")
    migration.backfill(global_apps, None)

    for opp in (shared_a, shared_b, solo):
        opp.refresh_from_db()
    assert shared_a.labs_org_id == shared_b.labs_org_id
    assert shared_a.labs_org.slug == "labs-synthetic-labs-synthetic"
    assert solo.labs_org_id == existing.pk
    # A bucket one person made is theirs; a bucket several people filled is nobody's.
    assert OrgMembership.objects.filter(org=existing, user=ana, role="admin").exists()
    assert not OrgMembership.objects.filter(org=shared_a.labs_org).exists()

    # Run twice, change nothing.
    migration.backfill(global_apps, None)
    assert LabsOrg.objects.filter(slug__startswith="labs-synthetic-").count() == 2


@pytest.mark.django_db
def test_finding_an_organisation_by_name_never_finds_a_labs_only_one(ana):
    """A labs-only organisation carries demo data's name ("Dimagi", a partner's).
    Matched by name, it would make a real organisation's name ambiguous."""
    from connect_labs.marketplace.identity import taken_by

    _opp(10_001, org_name="Baobab Trust", created_by=ana)

    assert taken_by("Baobab Trust") is None
    assert not LabsOrg.objects.real().filter(name="Baobab Trust").exists()
    real = LabsOrg.objects.create(slug="baobab-trust", name="Baobab Trust")
    assert list(LabsOrg.objects.real().filter(name="Baobab Trust")) == [real]
