"""The partner network over time, and where it is.

Two questions on one payload, because they are the same story told twice: how
many organisations joined the network by a given month, and how many of them had
delivered anything by then. The gap between those lines is the bench — partners
recruited and not yet activated — and it is most of the network.

Joining is a directory fact: it is the date they answered an EOI, which Connect
never sees. Delivering is a Connect fact, computed here from the spine rather
than stored, so it cannot go stale.

Dating a partner's first delivery needs two guards, both learned the hard way:

**completed_works cannot date anything before 2025-01-14.** Connect bulk-created
81k rows at one instant that day, so every partner already active then shares a
single false timestamp. Reading it naively collapses the entire founding cohort
onto one date.

**Device clocks lie.** A visit's ``field_ts`` comes off a handset and can be
years out — one partner's earliest claims 2010. The server-assigned ``sync_ts``
is what is trusted, with a sanity floor under it.
"""

from __future__ import annotations

import datetime as dt
from collections import Counter

from django.db.models import Min
from django.http import JsonResponse
from django.views import View

from connect_labs.microplans.core import iso as iso_codes
from connect_labs.pulse.models import PulseEvent, PulsePartner, PulseWork
from connect_labs.pulse.partner_names import resolve as resolve_partner

# Connect bulk-created its completed_works table at this instant.
WORKS_FLOOR = dt.datetime(2025, 1, 15, tzinfo=dt.timezone.utc)
# Connect did not exist before this; anything earlier is a bad device clock.
SANE_FLOOR = dt.datetime(2023, 1, 1, tzinfo=dt.timezone.utc)


def first_service_by_partner() -> dict[str, dt.date]:
    """Earliest verified delivery per partner NAME, across all its workspaces."""
    synced = {
        row["org_slug"]: row["v"]
        for row in PulseEvent.objects.exclude(org_slug="")
        .filter(sync_ts__gte=SANE_FLOOR)
        .values("org_slug")
        .annotate(v=Min("sync_ts"))
    }
    worked = {
        row["org_slug"]: row["v"]
        for row in PulseWork.objects.exclude(org_slug="")
        .filter(created_ts__gte=WORKS_FLOOR)
        .values("org_slug")
        .annotate(v=Min("created_ts"))
    }
    out: dict[str, dt.date] = {}
    for slug in set(synced) | set(worked):
        candidates = [d for d in (synced.get(slug), worked.get(slug)) if d]
        if not candidates:
            continue
        # One partner can run several workspaces; the earliest of them is when
        # that partner started delivering.
        parent = resolve_partner(slug)["parent"]
        if not parent:
            continue
        when = min(candidates).date()
        if parent not in out or when < out[parent]:
            out[parent] = when
    return out


def build_payload() -> dict:
    partners = list(PulsePartner.objects.all())
    delivering = first_service_by_partner()

    joined_months = Counter(p.joined_at.strftime("%Y-%m") for p in partners if p.joined_at)
    serving_months = Counter(d.strftime("%Y-%m") for d in delivering.values())

    months = sorted(set(joined_months) | set(serving_months))
    series, in_network, active = [], 0, 0
    for month in months:
        in_network += joined_months.get(month, 0)
        active += serving_months.get(month, 0)
        series.append({"m": month, "network": in_network, "delivering": active})

    points = []
    for p in partners:
        if p.lat is None or p.lon is None:
            continue
        points.append(
            {
                "name": p.name,
                "short": p.short,
                "lat": round(p.lat, 4),
                "lon": round(p.lon, 4),
                "precision": p.location_precision,
                "place": p.location_label,
                "iso3": p.country_iso3,
                "country": iso_codes.country_name(p.country_iso3) or "",
                "joined": p.joined_at.isoformat() if p.joined_at else "",
                "delivering": p.name in delivering,
                "since": delivering[p.name].isoformat() if p.name in delivering else "",
            }
        )
    points.sort(key=lambda r: (r["joined"] or "9999", r["name"]))

    by_country = Counter(p["iso3"] for p in points if p["iso3"])
    return {
        "totals": {
            "partners": len(partners),
            "delivering": len(delivering),
            "countries": len(by_country),
            "located": len(points),
            "with_join_date": sum(1 for p in partners if p.joined_at),
        },
        "precision": dict(Counter(p["precision"] for p in points)),
        "series": series,
        "points": points,
    }


class NetworkView(View):
    """Gated exactly like the partner menu, and for the same reason.

    This payload names partners and says where they are. ``api.py`` already
    decided that partner identity requires positive authorisation and fails
    closed, because this read API is otherwise unauthenticated and a client that
    merely hid the names would still have been sent them. Shipping a second
    endpoint that hands the same identities out freely would quietly undo that
    decision rather than reconsider it.
    """

    def get(self, request):
        from connect_labs.pulse.api import _partner_names_allowed

        if not _partner_names_allowed(request):
            return JsonResponse({"error": "not_authorised"}, status=403)
        payload = build_payload()
        if not payload["points"]:
            # An empty table is the un-imported state, not an error. Say so,
            # rather than rendering an empty map that looks like a dead network.
            payload["empty_reason"] = "No partners imported yet — run pulse_partner_import."
        return JsonResponse(payload)
