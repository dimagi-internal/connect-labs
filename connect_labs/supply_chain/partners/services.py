"""Building a partner's network from how it says it distributes.

A partner states one of three ways of getting stock to its field workers
(`PartnerPlan.distribution_model`). This module turns that into the network:

  * the partner has a warehouse, run by it;
  * every place it runs carries a sensible minimum and maximum stock (months), lower the
    nearer the stock is to the field worker, because a facility that holds a lot is a
    facility that can lose a lot;
  * field workers sit under the right point: straight under the warehouse when the partner hands
    to them directly, under a facility when the partner goes through facilities.

Nothing is moved quietly. Changing the model is recorded as a dated change of plan
(`set_plan`); field workers move only when the caller asks for it, and the result names every one
that moved, which also goes on the plan's note.
"""

from datetime import date
from decimal import Decimal

from django.db import transaction

from connect_labs.supply_chain.models import PartnerPlan, SupplyPoint
from connect_labs.supply_chain.partners.models import DISTRIBUTION_MODELS
from connect_labs.supply_chain.stock.services.issues import partner_by_point

# Months of stock (minimum, maximum) a place is managed to, by what the place is.
# The shorter the chain, the more the warehouse holds; a facility keeps little (shared stock is what
# gets diverted, Sophie's "keep stock at shared facilities low"); a field worker keeps about a
# fortnight to a month, enough for the next handout.
BANDS = {
    "through_facilities": {
        "store": (1, 3),
        "facility": (Decimal("0.5"), 1),
        "worker": (Decimal("0.25"), Decimal("0.5")),
    },
    "direct_to_workers": {"store": (1, 3), "facility": (Decimal("0.5"), 1), "worker": (Decimal("0.5"), 1)},
    "mixed": {"store": (1, 3), "facility": (Decimal("0.5"), 1), "worker": (Decimal("0.5"), 1)},
}

_STORE_KINDS = ("central_store", "regional_store")


def _band(model, point):
    kind = "worker" if point.kind == "user_held" else "facility" if point.kind == "facility" else "store"
    low, high = BANDS[model][kind]
    return Decimal(low), Decimal(high)


def current_plan(program_id, org_id):
    return PartnerPlan.objects.filter(program_id=program_id, org_id=org_id, ended_on__isnull=True).first()


def plans_by_partner(program_id) -> dict:
    """{org id: current PartnerPlan} for every partner that has stated one."""
    return {p.org_id: p for p in PartnerPlan.objects.filter(program_id=program_id, ended_on__isnull=True)}


@transaction.atomic
def set_plan(program_id, org, model, starts_on, *, source, recorded_by_org_id=None, note=""):
    """Record how `org` distributes from `starts_on`, ending the plan before it that day."""
    if model not in DISTRIBUTION_MODELS:
        raise ValueError(f"distribution model must be one of {', '.join(DISTRIBUTION_MODELS)}")
    for current in PartnerPlan.objects.select_for_update().filter(program_id=program_id, org=org, ended_on=None):
        if current.starts_on > starts_on:
            raise ValueError(
                f"{org.name}'s current plan began on {current.starts_on}, after {starts_on}; "
                "a new plan cannot start before the one it replaces"
            )
        current.ended_on = starts_on
        current.save(update_fields=["ended_on", "updated_at"])
    return PartnerPlan.objects.create(
        program_id=program_id,
        org=org,
        distribution_model=model,
        starts_on=starts_on,
        source=source,
        recorded_by_org_id=recorded_by_org_id,
        note=note,
    )


@transaction.atomic
def build_network(program_id, org, model, *, previous_model=None, move_workers=False):
    """Make the partner's places match `model`. Returns what was done.

    {"warehouse": point, "created_warehouse": bool, "bands_set": n,
     "workers_moved": [names], "workers_waiting": [names]}

    `workers_waiting` are field workers who should sit under a facility but there is more than one
    (or none) to choose from: someone has to say which, so they are listed, not guessed.
    """
    owner = partner_by_point(program_id)
    mine = [p for p in SupplyPoint.objects.filter(program_id=program_id) if owner.get(p.pk) == org.pk]

    warehouse = next((p for p in mine if p.kind in _STORE_KINDS and p.parent_id is None), None)
    created = False
    if warehouse is None:
        warehouse, created = SupplyPoint.objects.get_or_create(
            program_id=program_id,
            slug=f"{org.slug}-warehouse",
            defaults={
                "name": f"{org.name} warehouse",
                "kind": "central_store",
                "managed_by_org": org,
                "source": "we_recorded",
            },
        )
        if warehouse.managed_by_org_id is None:
            warehouse.managed_by_org = org
            warehouse.save(update_fields=["managed_by_org", "updated_at"])
        mine.append(warehouse)

    bands_set = 0
    for point in mine:
        if point.kind == "in_transit" or point.status != "active":
            continue
        # Only places still on the previous model's defaults (or on none) are re-banded: a band someone
        # chose by hand is theirs.
        was_default = point.min_months_of_stock is None and point.max_months_of_stock is None
        if previous_model and previous_model != model and not was_default:
            was_default = (point.min_months_of_stock, point.max_months_of_stock) == _band(previous_model, point)
        if was_default:
            point.min_months_of_stock, point.max_months_of_stock = _band(model, point)
            point.save(update_fields=["min_months_of_stock", "max_months_of_stock", "updated_at"])
            bands_set += 1

    workers = [p for p in mine if p.kind == "user_held"]
    facilities = [p for p in mine if p.kind == "facility" and p.status == "active"]
    moved, waiting = [], []
    if model == "direct_to_workers":
        for worker in workers:
            parent = worker.parent
            if parent is not None and parent.kind == "facility":
                if move_workers:
                    worker.parent = warehouse
                    worker.save(update_fields=["parent", "updated_at"])
                    moved.append(worker.name)
                else:
                    waiting.append(worker.name)
            elif parent is None and move_workers:
                worker.parent = warehouse
                worker.save(update_fields=["parent", "updated_at"])
                moved.append(worker.name)
    elif model == "through_facilities":
        for worker in workers:
            if worker.parent is not None and worker.parent.kind == "facility":
                continue
            if len(facilities) == 1 and move_workers:
                worker.parent = facilities[0]
                worker.save(update_fields=["parent", "updated_at"])
                moved.append(worker.name)
            else:
                waiting.append(worker.name)
    return {
        "warehouse": warehouse,
        "created_warehouse": created,
        "bands_set": bands_set,
        "workers_moved": moved,
        "workers_waiting": waiting,
    }


def assign_places(program_id, org, point_ids, *, only_unassigned=True) -> list:
    """Make `org` the partner running these places (and so everything below them that names no one).

    By default a place that already names an organisation is left alone, so assigning cannot take
    another partner's place by accident. Returns the places changed.
    """
    changed = []
    for point in SupplyPoint.objects.filter(program_id=program_id, pk__in=point_ids):
        if point.managed_by_org_id == org.pk:
            continue
        if only_unassigned and point.managed_by_org_id is not None:
            continue
        point.managed_by_org = org
        point.save(update_fields=["managed_by_org", "updated_at"])
        changed.append(point)
    return changed


def places_without_partner(program_id) -> list:
    """Top places (stores and facilities) with no organisation of their own or above them."""
    owner = partner_by_point(program_id)
    return [
        p
        for p in SupplyPoint.objects.filter(program_id=program_id, status="active")
        .exclude(kind__in=("user_held", "in_transit", "supplier_site", "customs"))
        .order_by("name")
        if owner.get(p.pk) is None
    ]


def as_day(value):
    return value if isinstance(value, date) else date.fromisoformat(str(value))


def today():
    return date.today()


def profile_of(program_id, org_id):
    """The partner's profile row (contact, defaults), or None when nothing is recorded yet."""
    from connect_labs.supply_chain.models import PartnerProfile

    return PartnerProfile.objects.filter(program_id=program_id, org_id=org_id).first()


def profiles_by_partner(program_id) -> dict:
    """{org id: PartnerProfile} for every partner with a profile."""
    from connect_labs.supply_chain.models import PartnerProfile

    return {p.org_id: p for p in PartnerProfile.objects.filter(program_id=program_id)}


def main_contact(program_id, org_id) -> dict:
    """Who to write to at a partner: {"name", "email", "phone", "connect_username", "copy_to": [...]}.

    The one place award emails and handovers learn a partner's contact. Every key is present; a
    partner with no profile (or no email) returns empty strings, so a caller can say "no main contact
    recorded" instead of claiming someone is copied.
    """
    profile = profile_of(program_id, org_id)
    return {
        "name": profile.contact_name if profile else "",
        "email": profile.contact_email if profile else "",
        "phone": profile.contact_phone if profile else "",
        "connect_username": profile.contact_connect_username if profile else "",
        "copy_to": list(profile.copy_to or []) if profile else [],
    }


def set_profile(program_id, org, **fields):
    """Create or update the partner's profile. Only the keys given change."""
    from connect_labs.supply_chain.models import PartnerProfile

    profile, _ = PartnerProfile.objects.get_or_create(program_id=program_id, org=org)
    for key, value in fields.items():
        setattr(profile, key, value)
    profile.save()
    return profile
