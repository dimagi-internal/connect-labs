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

from django.db.models import Min, Sum
from django.http import JsonResponse
from django.views import View

from connect_labs.microplans.core import iso as iso_codes
from connect_labs.pulse.models import PulseEvent, PulseOpportunity, PulsePartner, PulseWork
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


def countries_table(delivering: set[str]) -> list[dict]:
    """One row per country, over the UNION of two different facts.

    Where a partner is headquartered and where work happens are not the same
    question, and the difference is the point of the table: the network reaches
    countries Connect has not started delivering in, and delivery reaches
    countries no partner is headquartered in — a partner in one country running
    a programme across the border.

    Taking only one side would hide whichever countries sit on the other.
    """
    rows: dict[str, dict] = {}

    def row(iso3: str) -> dict:
        return rows.setdefault(
            iso3,
            {
                "iso3": iso3,
                "name": iso_codes.country_name(iso3) or iso3,
                "partners": 0,
                "delivering": 0,
                "services": 0,
                "usd": 0.0,
            },
        )

    for partner in PulsePartner.objects.exclude(country_iso3=""):
        entry = row(partner.country_iso3)
        entry["partners"] += 1
        if partner.name in delivering:
            entry["delivering"] += 1

    # Opportunities carry alpha-2 and the lifetime visit count, which is the
    # honest "services delivered here" figure -- works are a payment unit and
    # count differently per programme.
    for record in (
        PulseOpportunity.objects.exclude(country="").values("country").annotate(visits=Sum("lifetime_visit_count"))
    ):
        iso3 = iso_codes.to_alpha3(record["country"])
        if iso3:
            row(iso3)["services"] += record["visits"] or 0

    for record in PulseWork.objects.exclude(country="").values("country").annotate(usd=Sum("usd_to_org")):
        iso3 = iso_codes.to_alpha3(record["country"])
        if iso3:
            row(iso3)["usd"] += float(record["usd"] or 0)

    out = sorted(rows.values(), key=lambda r: (-r["services"], -r["partners"], r["name"]))
    for entry in out:
        entry["usd"] = round(entry["usd"], 2)
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

    countries = countries_table(set(delivering))
    return {
        "countries": countries,
        "totals": {
            "partners": len(partners),
            "delivering": len(delivering),
            "countries": sum(1 for c in countries if c["partners"]),
            "countries_delivering": sum(1 for c in countries if c["services"]),
            "located": len(points),
            "with_join_date": sum(1 for p in partners if p.joined_at),
            # Dated from an EOI, as opposed to stamped when we first saw them.
            "dated_from_eoi": sum(1 for p in partners if p.joined_at and p.joined_basis),
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
            # Nothing to draw is the un-imported state, not an error — but say
            # WHICH un-imported state. "No partners" and "partners with no
            # locations" need different fixes, and after a migration that adds
            # location columns the second is the one you actually hit.
            total = payload["totals"]["partners"]
            payload["empty_reason"] = (
                "No partners imported yet — run pulse_partner_import."
                if not total
                else f"{total} partners imported, but none carry a location yet — re-run pulse_partner_import."
            )
        return JsonResponse(payload)
