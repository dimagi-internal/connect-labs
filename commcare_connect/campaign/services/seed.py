"""Idempotent synthetic-data seeder for the Campaign Utility Tool demo.

Deterministic via random.Random(SEED). Honors the prototype's invariants
(amount = days*rate, fraud pairs share an identifier, the canonical
donor/region/role/planning/household constants) without byte-matching the
JS PRNG output.
"""
from __future__ import annotations

import random

from django.db import transaction

from commcare_connect.campaign.models import (
    Campaign,
    Donor,
    HouseholdStat,
    Region,
    RegionPlan,
    Worker,
    WorkerRole,
    Workspace,
)

SEED = 20260603

CAMPAIGN = dict(
    name="Measles–Rubella Vaccination Campaign",
    code="MR-2026-R2",
    round="Round 2",
    country="Nigeria",
    period="May 18 – Jun 14, 2026",
    status="Active",
    days_elapsed=16,
    days_total=28,
    target_pop=4280000,
)
DONORS = [
    ("gavi", "Gavi, the Vaccine Alliance", "Gavi", 2400000, "#5D70D2"),
    ("gates", "Bill & Melinda Gates Foundation", "BMGF", 1850000, "#3843D0"),
    ("unicef", "UNICEF", "UNICEF", 1200000, "#01A2A9"),
    ("who", "World Health Organization", "WHO", 650000, "#9A5183"),
]
ROLES = [
    ("vaccinator", "Vaccinator", 4500),
    ("mobilizer", "Social Mobilizer", 3500),
    ("recorder", "Recorder", 3500),
    ("supervisor", "Team Supervisor", 6500),
    ("town", "Town Announcer", 3000),
]
REGIONS = [
    ("kano", "Kano", ["Dala", "Fagge", "Gwale", "Nassarawa", "Tarauni"]),
    ("kaduna", "Kaduna", ["Chikun", "Kaduna North", "Kaduna South", "Zaria"]),
    ("sokoto", "Sokoto", ["Sokoto North", "Sokoto South", "Wamakko"]),
    ("bauchi", "Bauchi", ["Bauchi", "Katagum", "Misau"]),
    ("borno", "Borno", ["Maiduguri", "Jere", "Konduga"]),
]
PLAN_PLANNED = [820, 540, 360, 410, 380]
PLAN_ACTUAL_F = [0.97, 0.92, 0.88, 0.95, 0.66]
PLAN_BUDGET = [1850000, 1240000, 760000, 980000, 720000]
PLAN_SPENT_F = [0.61, 0.55, 0.48, 0.52, 0.34]
PLAN_TARGET = [920000, 680000, 410000, 380000, 260000]
PLAN_REACHED_F = [0.66, 0.59, 0.46, 0.40, 0.27]
PLAN_VALLOC = [980000, 720000, 440000, 410000, 290000]
PLAN_VUSED_F = [0.64, 0.57, 0.45, 0.42, 0.26]
HH_HH = [142000, 98000, 61000, 88000, 97200]
HH_VIS_F = [0.71, 0.66, 0.58, 0.52, 0.41]

FIRST_F = ["Amara", "Bilkisu", "Chiamaka", "Fatima", "Halima", "Ngozi", "Yetunde", "Zainab"]
FIRST_M = ["Abubakar", "Chidi", "Emeka", "Ibrahim", "Musa", "Oluwaseun", "Sani", "Tunde"]
LAST = ["Abubakar", "Adeyemi", "Bello", "Eze", "Garba", "Lawal", "Mohammed", "Okafor", "Sani", "Usman"]
BANKS = ["GTBank", "Access Bank", "Zenith Bank", "UBA", "First Bank"]
DOC_TYPES = ["National ID (NIN)", "Bank Verification Number (BVN)", "Proof of address"]
DUP_KINDS = [
    ("Duplicate National ID (NIN)", "nin", "nin"),
    ("Shared payment account", "acct", "acct"),
    ("Duplicate phone number", "phone", "phone"),
    ("Matching profile photograph", None, "photo"),
]


def _gen_workers(rng, roles, regions):
    workers = []
    for i in range(64):
        gender = "F" if rng.random() < 0.42 else "M"
        first = rng.choice(FIRST_F if gender == "F" else FIRST_M)
        last = rng.choice(LAST)
        region_id, region_name, lgas = rng.choice(regions)
        lga = rng.choice(lgas)
        role_id, role_name, rate = rng.choice(roles)
        days_worked = rng.randint(8, 16)
        days_approved = max(0, days_worked - rng.randint(0, 4))
        kr = rng.random()
        kyc = "approved" if kr < 0.64 else "pending" if kr < 0.82 else "review" if kr < 0.92 else "rejected"
        if kyc == "approved":
            pr = rng.random()
            pay = "paid" if pr < 0.4 else "approved" if pr < 0.7 else "pending" if pr < 0.9 else "hold"
        else:
            pay = "rejected" if kyc == "rejected" else "hold" if kyc in ("pending", "review") else "pending"
        documents = [
            {"type": DOC_TYPES[0], "status": "verified" if kyc == "approved" else "submitted"},
            {"type": DOC_TYPES[1], "status": "verified" if rng.random() < 0.5 else "pending"},
            {"type": DOC_TYPES[2], "status": "submitted"},
        ]
        workers.append(
            dict(
                worker_id=f"W{10234 + i}",
                first=first,
                last=last,
                name=f"{first} {last}",
                gender=gender,
                phone=f"+234 8{rng.randint(0, 9)}{rng.randint(1000000, 9999999)}",
                region_id=region_id,
                lga=lga,
                role_id=role_id,
                rate=rate,
                days_worked=days_worked,
                days_approved=days_approved,
                amount=days_worked * rate,
                kyc=kyc,
                pay=pay,
                bank=rng.choice(BANKS),
                acct=str(rng.randint(10**9, 10**10 - 1)),
                nin=str(rng.randint(10**10, 10**11 - 1)),
                passport=(f"A{rng.randint(10**7, 10**8 - 1)}" if rng.random() < 0.3 else None),
                enrolled=f"May {rng.randint(10, 17)}",
                attendance=round(days_worked / 16 * 100),
                prior_campaigns=rng.randint(0, 4),
                duplicate=False,
                dup_with=None,
                fraud_rules=[],
                linked=[],
                investigation=None,
                documents=documents,
            )
        )
    return workers


def _inject_fraud(rng, workers):
    for _ in range(7):
        a, b = rng.sample(range(64), 2)
        wa, wb = workers[a], workers[b]
        rule, field, shared = rng.choice(DUP_KINDS)
        for w in (wa, wb):
            w["duplicate"] = True
        if field:
            wb[field] = wa[field]
        if shared == "nin":
            wb["last"] = wa["last"]
            wb["name"] = f"{wb['first']} {wb['last']}"
        for w, other in ((wa, wb), (wb, wa)):
            if rule not in w["fraud_rules"]:
                w["fraud_rules"].append(rule)
            w["linked"].append({"id": other["worker_id"], "name": other["name"], "shared": shared})
        wa["dup_with"], wb["dup_with"] = wb["worker_id"], wa["worker_id"]
    for w in workers:
        if w["kyc"] == "rejected" and "Failed KYC verification" not in w["fraud_rules"]:
            w["fraud_rules"].append("Failed KYC verification")


@transaction.atomic
def seed_campaign(fresh: bool = False) -> Campaign:
    ws, _ = Workspace.objects.get_or_create(slug="nigeria", defaults={"country": "Nigeria", "name": "Nigeria"})
    existing = Campaign.objects.filter(workspace=ws, code=CAMPAIGN["code"]).first()
    if existing and not fresh:
        return existing
    if existing:
        existing.delete()

    rng = random.Random(SEED)
    c = Campaign.objects.create(workspace=ws, **CAMPAIGN)
    for o, (did, name, short, committed, color) in enumerate(DONORS):
        Donor.objects.create(
            campaign=c, donor_id=did, name=name, short=short, committed=committed, color=color, order=o
        )
    for o, (rid, name, rate) in enumerate(ROLES):
        WorkerRole.objects.create(campaign=c, role_id=rid, name=name, rate=rate, order=o)
    regions = []
    for i, (rid, name, lgas) in enumerate(REGIONS):
        r = Region.objects.create(campaign=c, region_id=rid, name=name, lgas=lgas, order=i)
        RegionPlan.objects.create(
            region=r,
            planned_wf=PLAN_PLANNED[i],
            actual_wf=round(PLAN_PLANNED[i] * PLAN_ACTUAL_F[i]),
            budget=PLAN_BUDGET[i],
            spent=round(PLAN_BUDGET[i] * PLAN_SPENT_F[i]),
            target=PLAN_TARGET[i],
            reached=round(PLAN_TARGET[i] * PLAN_REACHED_F[i]),
            vaccine_alloc=PLAN_VALLOC[i],
            vaccine_used=round(PLAN_VALLOC[i] * PLAN_VUSED_F[i]),
        )
        regions.append((rid, name, lgas))
    HouseholdStat.objects.create(
        campaign=c,
        registered=486200,
        visited=312800,
        members=2140000,
        members_reached=1386000,
        coverage=[{"name": REGIONS[i][1], "hh": HH_HH[i], "visited": round(HH_HH[i] * HH_VIS_F[i])} for i in range(5)],
    )
    roles = [(rid, name, rate) for rid, name, rate in ROLES]
    workers = _gen_workers(rng, roles, regions)
    _inject_fraud(rng, workers)
    Worker.objects.bulk_create([Worker(campaign=c, **w) for w in workers])
    return c
