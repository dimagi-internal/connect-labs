"""Serialize a Campaign + its related rows into the window.CUT_DATA shape
the prototype's React modules consume."""

from __future__ import annotations

from commcare_connect.campaign.models import Campaign, CampaignUser
from commcare_connect.campaign.services import providers, roles

KYC_STATES = ["approved", "pending", "rejected", "review"]
PAY_STATES = ["paid", "approved", "pending", "rejected", "hold"]
SHARED_LABEL = {
    "nin": "National ID (NIN)",
    "acct": "Payment account",
    "phone": "Phone number",
    "photo": "Profile photograph",
    "passport": "Passport number",
}


def _campaign(c: Campaign) -> dict:
    return {
        "name": c.name,
        "code": c.code,
        "round": c.round,
        "country": c.country,
        "period": c.period,
        "status": c.status,
        "daysElapsed": c.days_elapsed,
        "daysTotal": c.days_total,
        "targetPop": c.target_pop,
    }


def _donor(d) -> dict:
    return {"id": d.donor_id, "name": d.name, "short": d.short, "committed": d.committed, "color": d.color}


def _region(r) -> dict:
    return {"id": r.region_id, "name": r.name, "lgas": list(r.lgas)}


def _role(r) -> dict:
    return {"id": r.role_id, "name": r.name, "rate": r.rate}


def _planning(r) -> dict:
    p = r.plan
    return {
        "id": r.region_id,
        "name": r.name,
        "lgas": len(r.lgas),
        "plannedWf": p.planned_wf,
        "actualWf": p.actual_wf,
        "budget": p.budget,
        "spent": p.spent,
        "target": p.target,
        "reached": p.reached,
        "vaccineAlloc": p.vaccine_alloc,
        "vaccineUsed": p.vaccine_used,
    }


def _household(h) -> dict:
    return {
        "registered": h.registered,
        "visited": h.visited,
        "members": h.members,
        "membersReached": h.members_reached,
        "coverage": list(h.coverage),
    }


def _worker(w, role_names: dict, region_names: dict) -> dict:
    return {
        "id": w.worker_id,
        "first": w.first,
        "last": w.last,
        "name": w.name,
        "gender": w.gender,
        "phone": w.phone,
        "regionId": w.region_id,
        "region": region_names.get(w.region_id, ""),
        "lga": w.lga,
        "roleId": w.role_id,
        "role": role_names.get(w.role_id, ""),
        "rate": w.rate,
        "daysWorked": w.days_worked,
        "daysApproved": w.days_approved,
        "amount": w.amount,
        "kyc": w.kyc,
        "pay": w.pay,
        "bank": w.bank,
        "acct": w.acct,
        "nin": w.nin,
        "passport": w.passport,
        "enrolled": w.enrolled,
        "attendance": w.attendance,
        "priorCampaigns": w.prior_campaigns,
        "duplicate": w.duplicate,
        "dupWith": w.dup_with,
        "fraudRules": list(w.fraud_rules or []),
        "linked": list(w.linked or []),
        "investigation": w.investigation,
        "documents": list(w.documents or []),
    }


def _activity(a) -> dict:
    return {
        "id": a.activity_id,
        "name": a.name,
        "donor": a.donor,
        "status": a.status,
        "start": a.start,
        "end": a.end,
        "requests": a.requests,
        "workers": a.workers,
        "region": a.region,
        "target": a.target,
        "reached": a.reached,
        "synced": a.synced,
    }


def _user(cu, current_username) -> dict:
    return {
        "id": cu.commcare_username,
        "name": cu.name or cu.commcare_username,
        "email": cu.email,
        "role": roles.to_short(cu.role),
        "scope": cu.scope,
        "status": cu.status,
        "last": cu.last_login_at.strftime("%b %-d, %Y") if cu.last_login_at else "—",
        "you": cu.commcare_username == current_username,
    }


def _report_day(d) -> dict:
    return {"day": d.day, "enrolled": d.enrolled, "attended": d.attended, "paid": d.paid}


def _microplan(m) -> dict:
    return {
        "id": m.microplan_id,
        "regionId": m.region_id,
        "region": m.region,
        "lga": m.lga,
        "settlements": m.settlements,
        "wards": m.wards,
        "plannedWf": m.planned_wf,
        "actualWf": m.actual_wf,
        "roles": list(m.roles or []),
        "budget": m.budget,
        "spent": m.spent,
        "plannedToDate": m.planned_to_date,
        "target": m.target,
        "objective": m.objective,
        "goalPct": m.goal_pct,
        "reached": m.reached,
        "doses": m.doses,
        "dosesUsed": m.doses_used,
        "coldBoxes": m.cold_boxes,
        "vehicles": m.vehicles,
        "status": m.status,
        "owner": m.owner,
        "updated": m.updated,
    }


def _audit(a) -> dict:
    return {
        "at": a.at.strftime("%b %-d, %Y · %H:%M"),
        "user": a.user,
        "action": a.action,
        "module": a.module,
        "ip": a.ip,
    }


# How many workers ship in the bootstrap (the first table page). The rest are
# fetched on demand from /api/workers/ — so a 50k-worker campaign isn't a 38 MB
# bootstrap. The overview/donuts read WORKERS_SUMMARY (computed over ALL workers),
# never the worker list, so they're accurate at any scale.
WORKERS_PAGE_SIZE = 200


def workers_summary(workers, role_names: dict) -> dict:
    """Server-computed aggregates over ALL workers (what the overview + donuts read,
    instead of iterating the full list client-side)."""
    s = {
        "total": 0,
        "kyc": {k: 0 for k in ("approved", "pending", "rejected", "review")},
        "pay": {k: 0 for k in ("paid", "approved", "pending", "rejected", "hold")},
        "amount": 0,
        "paidAmount": 0,
        "pendingAmount": 0,
        "duplicates": 0,
        "female": 0,
        "flagged": 0,
        "byRole": {},
    }
    for w in workers:
        s["total"] += 1
        if w.kyc in s["kyc"]:
            s["kyc"][w.kyc] += 1
        if w.pay in s["pay"]:
            s["pay"][w.pay] += 1
        amt = w.amount or 0
        s["amount"] += amt
        if w.pay == "paid":
            s["paidAmount"] += amt
        elif w.pay in ("pending", "approved"):
            s["pendingAmount"] += amt
        if w.duplicate:
            s["duplicates"] += 1
        if w.gender == "F":
            s["female"] += 1
        if w.fraud_rules:
            s["flagged"] += 1
        role = role_names.get(w.role_id, "")
        bucket = s["byRole"].setdefault(role, {"m": 0, "f": 0})
        bucket["f" if w.gender == "F" else "m"] += 1
    return s


def bootstrap_payload(c: Campaign, current_username: str | None = None, request=None) -> dict:
    # CommCare-HQ-owned roster is read through the data-source seam (workers via the
    # CommCare Case API); tool-owned entities (activities, microplans, reporting,
    # households, users) are read directly from our ORM below.
    provider = providers.get_provider(c, request=request)
    campaign = provider.campaign()
    regions = list(provider.regions())
    donors = list(provider.donors())
    worker_roles = list(provider.worker_roles())
    workers = list(provider.workers())
    role_names = {r.role_id: r.name for r in worker_roles}
    region_names = {r.region_id: r.name for r in regions}
    return {
        "CAMPAIGN": _campaign(campaign),
        "DONORS": [_donor(d) for d in donors],
        "REGIONS": [_region(r) for r in regions],
        "ROLES": [_role(r) for r in worker_roles],
        "ACTIVITIES": [_activity(a) for a in c.activities.all()],
        "PLANNING": [_planning(r) for r in regions],
        "MICROPLANS": [_microplan(m) for m in c.microplans.all()],
        "REPORT_DAYS": [_report_day(d) for d in c.report_days.all()],
        "HOUSEHOLDS": _household(c.household_stat),
        "WORKERS": [_worker(w, role_names, region_names) for w in workers[:WORKERS_PAGE_SIZE]],
        "WORKERS_TOTAL": len(workers),
        "WORKERS_PAGE_SIZE": WORKERS_PAGE_SIZE,
        "WORKERS_SUMMARY": workers_summary(workers, role_names),
        "USERS": [_user(u, current_username) for u in CampaignUser.objects.all().order_by("created_at")],
        "AUDIT_LOG": [_audit(a) for a in c.audit_logs.all()[:50]],
        "KYC_STATES": list(KYC_STATES),
        "PAY_STATES": list(PAY_STATES),
        "sharedLabel": dict(SHARED_LABEL),
    }
