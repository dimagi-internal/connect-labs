"""Past calls: the marketplace's historical EOI rounds, shown read-only.

The rounds imported from the LLO Directory sheet (``source="directory"``) are a
record of calls that already ran. All of them were run by Dimagi, and every
announcement was public (confirmed by the product owner, 2026-10-15), so the
*call* -- title, type, program, countries, dates, the announcement -- is shown
on the Solicitations pages to anyone.

What this module never hands a page: the application form, the response
sheet and tab, the column map, the import notes, or anything about who
applied. A signed-in user may see how many organisations applied; nobody sees
which.

Dimagi owns these rows (``owner_org``, set by ``marketplace.eoi.upsert_rounds``),
and nothing here grants a right: ``access.standing`` treats every directory
round as one nobody manages on the solicitations side, and the data access
reaches them only through the read-only functions below. Owning one lets
Dimagi's members read its responses (``access.can_read_round_responses``) --
through the marketplace tools, never through these pages.
"""

from __future__ import annotations

import datetime
import re
from collections import OrderedDict
from dataclasses import dataclass

from django.db.models import Q

from connect_labs.labs.models import LabsOrg
from connect_labs.solicitations.local_models import Solicitation


def _still_open() -> Q:
    """Still taking applications -- the marketplace's own rule (`queries._still_open`):
    said to be active, not yet decided, and not past its deadline. Such a round
    is not a past call, and its page would have no honest way to respond."""
    today = datetime.date.today()
    return (
        Q(status="active")
        & Q(decision_on__isnull=True)
        & (Q(application_deadline__isnull=True) | Q(application_deadline__gte=today))
    )


def queryset():
    """Every directory round that has finished taking applications."""
    return Solicitation.objects.directory_rounds().exclude(_still_open())


def ran_on(call) -> datetime.date | None:
    """When the call ran: published, else its deadline."""
    return call.published_on or call.application_deadline


def all_past_calls() -> list[Solicitation]:
    """Newest first, each decorated for a card (see `decorate`)."""
    calls = [decorate(c) for c in queryset()]
    calls.sort(key=lambda c: (ran_on(c) or datetime.date.min, c.title), reverse=True)
    return calls


def get(key) -> Solicitation | None:
    """A past call by slug, or by id."""
    text = str(key or "").strip()
    if not text:
        return None
    call = queryset().filter(slug=text).first()
    if call is None and text.isdigit():
        call = queryset().filter(pk=int(text)).first()
    return decorate(call) if call is not None else None


def is_past_call(call) -> bool:
    return call is not None and queryset().filter(pk=call.pk).exists()


@dataclass(frozen=True)
class Owner:
    """Who ran the call, for display. `slug` is set when Dimagi's org page exists."""

    name: str
    slug: str = ""


def owner() -> Owner:
    """Dimagi, by the slug the supply runner uses. Looked up, never created."""
    from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG

    org = LabsOrg.objects.filter(slug=DIMAGI_ORG_SLUG).first()
    return Owner(name=org.name, slug=org.slug) if org is not None else Owner(name="Dimagi")


def owner_slug() -> str:
    from connect_labs.supply_chain.runner import DIMAGI_ORG_SLUG

    return DIMAGI_ORG_SLUG


_SAFE_URL = re.compile(r"^https?://", re.I)


def decorate(call: Solicitation) -> Solicitation:
    """Attach what a card or page draws: the program's picture and colour, the
    date it ran, a safe announcement link. Reads nothing private."""
    from connect_labs.marketplace import programs

    slug = call.delivery_type or ""
    call.past_program = {
        "slug": slug,
        "label": call.program_label,
        "hue": programs.hue(slug),
        "image": programs.image(slug),
    }
    call.past_ran_on = ran_on(call)
    call.past_dated_by = "published" if call.published_on else ("deadline" if call.application_deadline else "")
    url = (call.announcement_url or "").strip()
    call.past_announcement = url if _SAFE_URL.match(url) else ""
    return call


def by_year(calls) -> list[dict]:
    """[{year, calls}], newest year first; undated calls last."""
    groups: OrderedDict = OrderedDict()
    for call in calls:
        when = getattr(call, "past_ran_on", None) or ran_on(call)
        groups.setdefault(when.year if when else None, []).append(call)
    dated = sorted((y for y in groups if y is not None), reverse=True)
    ordered = [{"year": y, "calls": groups[y]} for y in dated]
    if None in groups:
        ordered.append({"year": None, "calls": groups[None]})
    return ordered


def haystack(call) -> str:
    """What search reads: the call's public words only."""
    return " ".join(
        [call.title, call.description, call.scope_of_work, call.program_label, call.target_countries, "Dimagi"]
    ).lower()


def organisations_applied(call) -> int:
    """How many organisations applied -- the marketplace's own count, so the
    two pages agree (identified organisations plus unattributed submissions)."""
    from connect_labs.marketplace.queries import rounds_with_counts

    row = rounds_with_counts().filter(pk=call.pk).values_list("organisations", flat=True).first()
    return int(row or 0)
