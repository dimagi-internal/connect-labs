"""App-owned test factories for the Campaign Utility Tool.

Deliberately self-contained: these depend only on `campaign` models + the shared
`User` model, NOT on any labs factory. If `connect_labs/campaign/` migrates
out of connect-labs, this module travels with it and keeps working. For a full,
prototype-shaped dataset use `services.seed.seed_campaign`; use these factories
when a test needs a small, hand-shaped graph (one campaign, a worker or two).
"""
from __future__ import annotations

from factory import Faker, Sequence, SubFactory
from factory.django import DjangoModelFactory

from connect_labs.campaign.models import (
    Campaign,
    CampaignUser,
    Donor,
    HouseholdStat,
    Region,
    RegionPlan,
    Worker,
    WorkerRole,
    Workspace,
)
from connect_labs.users.models import User


class UserFactory(DjangoModelFactory):
    username = Sequence(lambda n: "campaign-user-%d" % n)
    email = Faker("email")
    name = Faker("name")

    class Meta:
        model = User
        django_get_or_create = ["username"]


class WorkspaceFactory(DjangoModelFactory):
    country = "Nigeria"
    name = "Nigeria"
    slug = Sequence(lambda n: "nigeria-%d" % n)

    class Meta:
        model = Workspace


class CampaignFactory(DjangoModelFactory):
    workspace = SubFactory(WorkspaceFactory)
    name = "Measles–Rubella Vaccination Campaign"
    code = Sequence(lambda n: "MR-2026-R2-%d" % n)
    round = "Round 2"
    country = "Nigeria"
    status = "Active"
    days_elapsed = 16
    days_total = 28
    target_pop = 4_280_000

    class Meta:
        model = Campaign


class DonorFactory(DjangoModelFactory):
    campaign = SubFactory(CampaignFactory)
    donor_id = Sequence(lambda n: "donor-%d" % n)
    name = "Gavi, the Vaccine Alliance"
    short = "Gavi"
    committed = 2_400_000
    color = "#5D70D2"

    class Meta:
        model = Donor


class RegionFactory(DjangoModelFactory):
    campaign = SubFactory(CampaignFactory)
    region_id = Sequence(lambda n: "region-%d" % n)
    name = "Kano"
    lgas = ["Dala", "Fagge", "Gwale", "Nassarawa", "Tarauni"]

    class Meta:
        model = Region


class RegionPlanFactory(DjangoModelFactory):
    region = SubFactory(RegionFactory)
    planned_wf = 820
    actual_wf = 760
    budget = 12_000_000
    spent = 7_400_000
    target = 1_300_000
    reached = 980_000
    vaccine_alloc = 980_000
    vaccine_used = 740_000

    class Meta:
        model = RegionPlan


class WorkerRoleFactory(DjangoModelFactory):
    campaign = SubFactory(CampaignFactory)
    role_id = Sequence(lambda n: "role-%d" % n)
    name = "Vaccinator"
    rate = 6_000

    class Meta:
        model = WorkerRole


class HouseholdStatFactory(DjangoModelFactory):
    campaign = SubFactory(CampaignFactory)
    registered = 1_900_000
    visited = 1_540_000
    members = 1_700_000
    members_reached = 1_386_000
    coverage = [{"name": "Kano", "hh": 420_000, "visited": 360_000}]

    class Meta:
        model = HouseholdStat


class WorkerFactory(DjangoModelFactory):
    """A serializer-valid worker (every field the bootstrap payload reads is set)."""

    campaign = SubFactory(CampaignFactory)
    worker_id = Sequence(lambda n: "W%05d" % n)
    first = "Amara"
    last = "Bello"
    name = "Amara Bello"
    gender = "F"
    phone = "+234 800 000 0000"
    region_id = "kano"
    lga = "Dala"
    role_id = "vaccinator"
    rate = 6_000
    days_worked = 12
    days_approved = 0
    amount = 72_000
    kyc = "pending"
    pay = "pending"
    bank = "First Bank"
    acct = "0001112223"
    nin = "12345678901"
    passport = None
    enrolled = "May 18, 2026"
    attendance = 92
    prior_campaigns = 2
    duplicate = False
    dup_with = None
    fraud_rules = []
    linked = []
    investigation = None
    documents = [{"type": "NIN", "status": "verified"}]

    class Meta:
        model = Worker


class CampaignUserFactory(DjangoModelFactory):
    """A whitelist entry. Pass role=... to mint any of the five roles."""

    user = SubFactory(UserFactory)
    commcare_username = Sequence(lambda n: "member-%d@dimagi.com" % n)
    email = Sequence(lambda n: "member-%d@dimagi.com" % n)
    name = "Member"
    role = "campaign_admin"
    status = CampaignUser.Status.ACTIVE

    class Meta:
        model = CampaignUser
