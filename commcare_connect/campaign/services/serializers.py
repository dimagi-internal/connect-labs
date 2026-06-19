"""Serialize a Campaign + its related rows into the window.CUT_DATA shape
the prototype's React modules consume."""
from __future__ import annotations

from commcare_connect.campaign.models import Campaign

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


def _worker(w) -> dict:
    return {
        "id": w.worker_id,
        "first": w.first,
        "last": w.last,
        "name": w.name,
        "gender": w.gender,
        "phone": w.phone,
        "regionId": w.region_id,
        "lga": w.lga,
        "roleId": w.role_id,
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


def bootstrap_payload(c: Campaign) -> dict:
    regions = list(c.regions.select_related("plan").all())
    return {
        "CAMPAIGN": _campaign(c),
        "DONORS": [_donor(d) for d in c.donors.all()],
        "REGIONS": [_region(r) for r in regions],
        "ROLES": [_role(r) for r in c.worker_roles.all()],
        "ACTIVITIES": [],
        "PLANNING": [_planning(r) for r in regions],
        "MICROPLANS": [],
        "REPORT_DAYS": [],
        "HOUSEHOLDS": _household(c.household_stat),
        "WORKERS": [_worker(w) for w in c.workers.all()],
        "KYC_STATES": list(KYC_STATES),
        "PAY_STATES": list(PAY_STATES),
        "sharedLabel": dict(SHARED_LABEL),
    }
