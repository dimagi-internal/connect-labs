"""Idempotent synthetic-data seeder for the Campaign Utility Tool demo.

Deterministic via random.Random(SEED). Honors the prototype's invariants
(amount = days*rate, fraud pairs share an identifier, the canonical
donor/region/role/planning/household constants) without byte-matching the
JS PRNG output.
"""

from __future__ import annotations

import datetime
import random

from django.db import transaction
from django.utils import timezone

from commcare_connect.campaign.models import (
    Activity,
    AuditLog,
    Campaign,
    Donor,
    HouseholdStat,
    Microplan,
    Region,
    RegionPlan,
    ReportDay,
    Worker,
    WorkerRole,
    Workspace,
)

SEED = 20260603

DEMO_USERS = [
    ("tunde.balogun@dimagi.com", "Tunde Balogun", "payment_admin", "Kano, Kaduna", "active"),
    ("ngozi.eze@partner.org", "Ngozi Eze", "compliance_admin", "All regions", "active"),
    ("fatima.bello@moh.gov.ng", "Fatima Bello", "operations_manager", "All regions", "active"),
    ("david.mensah@dimagi.com", "David Mensah", "payment_admin", "Sokoto, Bauchi", "active"),
    ("grace.adeyemi@donor.org", "Grace Adeyemi", "reporting_user", "All regions", "active"),
    ("samuel.okoro@dimagi.com", "Samuel Okoro", "operations_manager", "Kano", "inactive"),
]


def seed_demo_users():
    from commcare_connect.campaign.models import CampaignUser

    for username, name, role, scope, status in DEMO_USERS:
        CampaignUser.objects.get_or_create(
            commcare_username=username,
            defaults={"email": username, "name": name, "role": role, "scope": scope, "status": status},
        )


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

ACTIVITIES = [
    # (id, name, donor_short, status, start, end, requests, workers, region, target, reached)
    (
        "ACT-01",
        "Fixed-post immunization — Kano metro",
        "Gavi",
        "Active",
        "May 18",
        "Jun 14",
        1840,
        142,
        "Kano",
        920000,
        612000,
    ),
    (
        "ACT-02",
        "Door-to-door catch-up — Kaduna",
        "BMGF",
        "Active",
        "May 20",
        "Jun 14",
        1320,
        98,
        "Kaduna",
        680000,
        401000,
    ),
    (
        "ACT-03",
        "Mobile teams — Sokoto rural",
        "UNICEF",
        "At risk",
        "May 22",
        "Jun 14",
        760,
        61,
        "Sokoto",
        410000,
        188000,
    ),
    ("ACT-04", "Fixed-post — Bauchi", "Gavi", "Active", "May 18", "Jun 14", 980, 72, "Bauchi", 380000, 152000),
    ("ACT-05", "IDP camp outreach — Borno", "WHO", "Planned", "Jun 3", "Jun 14", 0, 0, "Borno", 260000, 0),
    (
        "ACT-06",
        "Vitamin A co-delivery — Kano",
        "UNICEF",
        "Completed",
        "May 18",
        "May 31",
        1420,
        120,
        "Kano",
        240000,
        231000,
    ),
]
MP_OWNERS = ["Ngozi Eze", "Amara Okafor", "Ibrahim Sani", "Fatima Bello", "Chidi Okafor"]
ROLE_MIX = [("vaccinator", 0.40), ("supervisor", 0.10), ("mobilizer", 0.22), ("recorder", 0.16), ("town", 0.12)]

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


def _split(total, weights):
    """Distribute an integer total across weights; last bucket takes the remainder."""
    out, acc = [], 0
    for w in weights[:-1]:
        v = round(total * w)
        out.append(v)
        acc += v
    out.append(total - acc)
    return out


def _mp_status(fill, cov, spent, actual_wf):
    if spent == 0 and actual_wf == 0:
        return "Planned"
    if fill < 0.75 or cov < 0.40:
        return "At risk"
    if fill < 0.90 or cov < 0.55:
        return "Behind"
    return "On track"


def _seed_activities(rng, campaign):
    for aid, name, donor, status, start, end, requests, workers, region, target, reached in ACTIVITIES:
        Activity.objects.create(
            campaign=campaign,
            activity_id=aid,
            name=name,
            donor=donor,
            status=status,
            start=start,
            end=end,
            requests=requests,
            workers=workers,
            region=region,
            target=target,
            reached=reached,
            synced=(status == "Completed" or aid == "ACT-01"),
        )


def _seed_microplans(rng, campaign, regions, roles):
    role_rate = {rid: rate for (rid, _name, rate) in roles}
    role_name = {rid: nm for (rid, nm, _rate) in roles}
    elapsed_frac = campaign.days_elapsed / campaign.days_total
    seq = 100
    for region in regions:  # Region instances (with .plan, .lgas, .region_id, .name)
        plan = region.plan
        lgas = list(region.lgas)
        wts = [0.7 + rng.random() * 0.7 for _ in lgas]
        sw = sum(wts)
        fracs = [w / sw for w in wts]

        # distribute region totals across LGAs (last takes remainder)
        def dist(total, fracs=fracs):
            out, acc = [], 0
            for f in fracs[:-1]:
                v = round(total * f)
                out.append(v)
                acc += v
            out.append(total - acc)
            return out

        pw, aw = dist(plan.planned_wf), dist(plan.actual_wf)
        bud, sp = dist(plan.budget), dist(plan.spent)
        tgt, rc = dist(plan.target), dist(plan.reached)
        dz, dzu = dist(plan.vaccine_alloc), dist(plan.vaccine_used)
        for i, lga in enumerate(lgas):
            seq += 1
            planned_wf = pw[i]
            role_weights = [w for (_rid, w) in ROLE_MIX]
            role_planned = _split(planned_wf, role_weights)
            # actual per role scaled by region fill
            fill_r = (aw[i] / planned_wf) if planned_wf else 0
            mp_roles = []
            actual_acc = 0
            for j, (rid, _w) in enumerate(ROLE_MIX):
                pl = role_planned[j]
                ac = round(pl * fill_r) if j < len(ROLE_MIX) - 1 else max(0, aw[i] - actual_acc)
                actual_acc += ac
                mp_roles.append(
                    {"roleId": rid, "role": role_name[rid], "rate": role_rate[rid], "planned": pl, "actual": ac}
                )
            goal = 95
            objective = round(tgt[i] * goal / 100)
            fill = (aw[i] / planned_wf) if planned_wf else 0
            cov = (rc[i] / objective) if objective else 0
            status = _mp_status(fill, cov, sp[i], aw[i])
            Microplan.objects.create(
                campaign=campaign,
                microplan_id=f"MP-{seq}",
                region_id=region.region_id,
                region=region.name,
                lga=lga,
                settlements=rng.randint(8, 34),
                wards=rng.randint(3, 11),
                planned_wf=planned_wf,
                actual_wf=aw[i],
                roles=mp_roles,
                budget=bud[i],
                spent=sp[i],
                planned_to_date=round(bud[i] * elapsed_frac),
                target=tgt[i],
                objective=objective,
                goal_pct=goal,
                reached=rc[i],
                doses=dz[i],
                doses_used=dzu[i],
                cold_boxes=max(2, round(dz[i] / 18000)),
                vehicles=max(1, round(planned_wf / 60)),
                status=status,
                owner=rng.choice(MP_OWNERS),
                updated=f"Jun {rng.randint(1, 3)}, 2026",
            )


def _gen_workers(rng, roles, regions, count=64):
    workers = []
    for i in range(count):
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


def _inject_fraud(rng, workers, pairs=7):
    if len(workers) < 2:
        return
    pairs = min(pairs, len(workers) // 2)
    for _ in range(pairs):
        a, b = rng.sample(range(len(workers)), 2)
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


def _seed_report_days(rng, campaign):
    rows = []
    for d in range(16):
        daily = rng.randint(120000, 210000) * (0.6 if d < 3 else 1.0)
        rows.append(
            ReportDay(
                campaign=campaign,
                day=f"D{d + 1}",
                order=d,
                enrolled=round(daily),
                attended=round(daily * (0.88 + rng.random() * 0.1)),
                paid=round(daily * (0.7 + rng.random() * 0.15)),
            )
        )
    ReportDay.objects.bulk_create(rows)


AUDIT_SEED = [
    (datetime.datetime(2026, 6, 3, 9, 41), "Amara Okafor", "Approved 8 worker payments", "Payments", "102.89.x.x"),
    (datetime.datetime(2026, 6, 3, 9, 12), "Ngozi Eze", "Approved KYC for W10342", "KYC", "197.210.x.x"),
    (
        datetime.datetime(2026, 6, 3, 8, 55),
        "Amara Okafor",
        "Changed Samuel Okoro's role to Operations Manager",
        "User Management",
        "102.89.x.x",
    ),
    (datetime.datetime(2026, 6, 2, 17, 30), "Tunde Balogun", "Logged in", "Authentication", "105.112.x.x"),
    (
        datetime.datetime(2026, 6, 2, 16, 4),
        "Amara Okafor",
        "Invited aisha.lawal@partner.org (Compliance Administrator)",
        "User Management",
        "102.89.x.x",
    ),
    (datetime.datetime(2026, 6, 2, 14, 48), "Fatima Bello", "Created activity ACT-05", "Activities", "154.113.x.x"),
    (
        datetime.datetime(2026, 6, 2, 11, 20),
        "Amara Okafor",
        "Deactivated user Joseph Idoko",
        "User Management",
        "102.89.x.x",
    ),
]


def _seed_audit_log(campaign):
    rows = [
        AuditLog(
            campaign=campaign,
            at=timezone.make_aware(dt) if timezone.is_naive(dt) else dt,
            user=user,
            action=action,
            module=module,
            ip=ip,
        )
        for dt, user, action, module, ip in AUDIT_SEED
    ]
    AuditLog.objects.bulk_create(rows)


@transaction.atomic
def seed_campaign(fresh: bool = False, worker_count: int = 64) -> Campaign:
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
    workers = _gen_workers(rng, roles, regions, count=worker_count)
    # Scale fraud clusters with the roster (≈7 pairs at the canonical 64 workers).
    _inject_fraud(rng, workers, pairs=max(1, round(worker_count * 7 / 64)))
    Worker.objects.bulk_create([Worker(campaign=c, **w) for w in workers])
    region_objs = list(c.regions.select_related("plan").all())
    _seed_activities(rng, c)
    _seed_microplans(rng, c, region_objs, ROLES)
    _seed_report_days(rng, c)
    _seed_audit_log(c)
    seed_demo_users()
    return c
