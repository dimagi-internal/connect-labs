"""Pure mutation logic for worker payments + KYC, with the authoritative fraud guard."""
from __future__ import annotations

from django.utils import timezone


class FraudGuardError(Exception):
    """Raised when an action is blocked because a worker is flagged / KYC-rejected."""


def can_approve_pay(w) -> bool:
    return not (w.fraud_rules or []) and w.kyc != "rejected"


def set_pay(workers_qs, status):
    updated, blocked = [], []
    for w in workers_qs:
        if status in ("approved", "paid") and not can_approve_pay(w):
            blocked.append(w.worker_id)
            continue
        w.pay = status
        if status in ("approved", "paid"):
            w.days_approved = w.days_worked
        w.save(update_fields=["pay", "days_approved"])
        updated.append(w)
    return updated, blocked


def queue_pay(w, approved_count: int):
    if not can_approve_pay(w):
        raise FraudGuardError(f"{w.worker_id} has open fraud flags or rejected KYC")
    w.days_approved = max(0, min(int(approved_count), w.days_worked))
    w.pay = "approved"
    w.save(update_fields=["pay", "days_approved"])
    return w


def set_kyc(w, status: str):
    if status == "approved" and (w.fraud_rules or []):
        raise FraudGuardError(f"{w.worker_id} has open fraud flags")
    w.kyc = status
    w.save(update_fields=["kyc"])
    return w


def resolve_duplicate(w, keep: bool):
    w.duplicate = False
    w.fraud_rules = []
    if keep:
        w.linked = []
        w.save(update_fields=["duplicate", "fraud_rules", "linked"])
    else:
        w.kyc = "rejected"
        w.save(update_fields=["duplicate", "fraud_rules", "kyc"])
    return w


def save_investigation(w, status: str, outcome, note, by_name: str):
    inv = dict(w.investigation or {"status": "Open", "notes": [], "outcome": None})
    inv["status"] = status or inv.get("status", "Open")
    inv["outcome"] = outcome
    if note and note.strip():
        stamp = timezone.now().strftime("%b %-d, %Y · %H:%M")
        inv["notes"] = [{"at": stamp, "by": by_name, "text": note.strip()}, *(inv.get("notes") or [])]
    w.investigation = inv
    w.save(update_fields=["investigation"])
    return w
