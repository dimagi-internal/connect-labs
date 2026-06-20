"""Synthetic CommCare worker-case generator.

Produces ``WorkerCase`` rows — synthetic CommCare cases (case_type
``campaign_worker``) whose properties carry the full Worker + KYC-Verification
field set the Data Model marks CommCare-owned. Geography (state/LGA/ward + a GPS
point) comes from labs ``AdminBoundary`` via :mod:`geography`; the per-worker
field values + fraud clusters reuse the seed's proven distributions (so the demo's
statistical shape and invariants — amount = days*rate, shared-identifier fraud
pairs — carry over) while scaling across real national geography.

This is the synthetic stand-in for what a live CommCare Case/Form API read will
later return; the shape is identical so the CommCareProvider is a drop-in swap.
"""

from __future__ import annotations

import random
from types import SimpleNamespace

from django.db.models import Q

from commcare_connect.campaign.models import Worker, WorkerCase
from commcare_connect.campaign.services import geography, serializers
from commcare_connect.campaign.services.geography import GeographyUnavailable
from commcare_connect.campaign.services.seed import BANKS, DOC_TYPES, FIRST_F, FIRST_M, LAST, ROLES, _inject_fraud

DEFAULT_SEED = 20260603


class WorkerCaseHandle:
    """Adapts a ``WorkerCase`` to the mutable-worker interface ``worker_actions``
    expects: attribute reads/writes proxy ``WorkerCase.properties`` and ``save()``
    persists the case. This lets the SAME payment/KYC mutation logic run on cases —
    the CommCare-owned store (workers/KYC have a CommCare parallel, so they live on
    the case, never a tool-local copy) — exactly as it runs on legacy Worker rows.
    """

    def __init__(self, worker_case: WorkerCase):
        object.__setattr__(self, "_wc", worker_case)

    def __getattr__(self, name):
        # Fetch _wc via object.__getattribute__ so a missing _wc invariant raises a
        # clean AttributeError instead of recursing through __getattr__ forever.
        wc = object.__getattribute__(self, "_wc")
        try:
            return wc.properties[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name, value):
        self._wc.properties[name] = value

    def save(self, update_fields=None):
        # Worker fields all live in the JSON bag, so we persist the whole bag.
        self._wc.save(update_fields=["properties"])

    @property
    def case(self) -> WorkerCase:
        return self._wc


def resolve_worker(campaign, worker_id):
    """One mutable worker for ``campaign`` — a WorkerCaseHandle for a CommCare-domain
    campaign (writes land on the case), else the legacy Worker ORM row. None if absent."""
    if campaign.commcare_domain:
        wc = WorkerCase.objects.filter(campaign=campaign, worker_id=worker_id).first()
        return WorkerCaseHandle(wc) if wc is not None else None
    return Worker.objects.filter(campaign=campaign, worker_id=worker_id).first()


def resolve_workers(campaign, worker_ids):
    """Mutable workers for ``campaign`` by id (cases for a domain campaign, else ORM)."""
    if campaign.commcare_domain:
        return [WorkerCaseHandle(wc) for wc in WorkerCase.objects.filter(campaign=campaign, worker_id__in=worker_ids)]
    return list(Worker.objects.filter(campaign=campaign, worker_id__in=worker_ids))


def query_workers(campaign, *, q="", kyc="", pay="", role="", region="", fraud="", page=1, page_size=50):
    """Filtered + paginated worker read for the /api/workers/ endpoint. Queries the
    right store efficiently at the DB level (WorkerCase for a CommCare-domain campaign,
    else Worker ORM) so a 50k roster paginates cheaply. Returns (serialized_page, total)."""
    page = max(1, int(page))
    page_size = max(1, min(int(page_size), 500))
    role_names = {r.role_id: r.name for r in campaign.worker_roles.all()}
    region_names = {r.region_id: r.name for r in campaign.regions.all()}
    start, end = (page - 1) * page_size, page * page_size

    if campaign.commcare_domain:
        qs = WorkerCase.objects.filter(campaign=campaign)
        if kyc:
            qs = qs.filter(properties__kyc=kyc)
        if pay:
            qs = qs.filter(properties__pay=pay)
        if role:
            qs = qs.filter(properties__role_id=role)
        if region:
            qs = qs.filter(region_id=region)
        if fraud == "flagged":
            qs = qs.exclude(properties__fraud_rules=[])
        elif fraud == "clean":
            qs = qs.filter(properties__fraud_rules=[])
        if q:
            qs = qs.filter(
                Q(properties__name__icontains=q) | Q(worker_id__icontains=q) | Q(properties__nin__icontains=q)
            )
        total = qs.count()
        workers = [SimpleNamespace(**wc.properties) for wc in qs.order_by("worker_id")[start:end]]
    else:
        qs = Worker.objects.filter(campaign=campaign)
        if kyc:
            qs = qs.filter(kyc=kyc)
        if pay:
            qs = qs.filter(pay=pay)
        if role:
            qs = qs.filter(role_id=role)
        if region:
            qs = qs.filter(region_id=region)
        if fraud == "flagged":
            qs = qs.exclude(fraud_rules=[])
        elif fraud == "clean":
            qs = qs.filter(fraud_rules=[])
        if q:
            qs = qs.filter(Q(name__icontains=q) | Q(worker_id__icontains=q) | Q(nin__icontains=q))
        total = qs.count()
        workers = list(qs.order_by("worker_id")[start:end])

    return [serializers._worker(w, role_names, region_names) for w in workers], total


def _placements(states):
    """All (state, lga, ward) triples under the given states, in a deterministic
    order (geography.* already order by name)."""
    out = []
    for state in states:
        for lga in geography.lgas(state):
            for ward in geography.wards(lga):
                out.append((state, lga, ward))
    return out


def _worker_props(i, placement, rng):
    state, lga, ward = placement
    gps = geography.random_point_in(ward, rng)
    gender = "F" if rng.random() < 0.42 else "M"
    first = rng.choice(FIRST_F if gender == "F" else FIRST_M)
    last = rng.choice(LAST)
    role_id, _role_name, rate = rng.choice(ROLES)
    days_worked = rng.randint(8, 16)
    days_approved = max(0, days_worked - rng.randint(0, 4))
    kr = rng.random()
    kyc = "approved" if kr < 0.64 else "pending" if kr < 0.82 else "review" if kr < 0.92 else "rejected"
    if kyc == "approved":
        pr = rng.random()
        pay = "paid" if pr < 0.4 else "approved" if pr < 0.7 else "pending" if pr < 0.9 else "hold"
    else:
        pay = "rejected" if kyc == "rejected" else "hold"
    documents = [
        {"type": DOC_TYPES[0], "status": "verified" if kyc == "approved" else "submitted"},
        {"type": DOC_TYPES[1], "status": "verified" if rng.random() < 0.5 else "pending"},
        {"type": DOC_TYPES[2], "status": "submitted"},
    ]
    return dict(
        worker_id=f"W{10234 + i}",
        first=first,
        last=last,
        name=f"{first} {last}",
        gender=gender,
        phone=f"+234 8{rng.randint(0, 9)}{rng.randint(1000000, 9999999)}",
        region_id=state.boundary_id,
        lga=lga.name,
        ward=ward.name,
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
        location=[round(gps.x, 6), round(gps.y, 6)],
    )


def generate_worker_cases(campaign, *, count, states_limit=None, seed=DEFAULT_SEED, batch_size=2000):
    """Generate ``count`` synthetic worker cases for ``campaign`` and bulk-insert
    them as ``WorkerCase`` rows. ``states_limit`` caps how many states the roster
    spreads across (None = all loaded states, i.e. national scale)."""
    rng = random.Random(seed)
    states = geography.states()
    if not states:
        raise GeographyUnavailable(
            "No NGA admin boundaries loaded. Run `manage.py load_geopode_from_drive --iso NGA`."
        )
    if states_limit:
        states = states[:states_limit]
    placements = _placements(states)
    if not placements:
        raise GeographyUnavailable("States are loaded but have no LGA/ward children to place workers in.")

    workers = [_worker_props(i, rng.choice(placements), rng) for i in range(count)]
    # Scale fraud clusters with the roster (≈7 pairs per 64 workers), reusing the
    # seed's shared-identifier injection (worker dicts share its key shape).
    _inject_fraud(rng, workers, pairs=max(1, round(count * 7 / 64)))

    cases = [
        WorkerCase(
            campaign=campaign,
            case_id=f"wc-{campaign.id}-{w['worker_id']}",
            case_type="campaign_worker",
            worker_id=w["worker_id"],
            region_id=w["region_id"],
            lga=w["lga"],
            ward=w["ward"],
            properties=w,
        )
        for w in workers
    ]
    WorkerCase.objects.bulk_create(cases, batch_size=batch_size)
    return cases
