"""The tracker's saved state: versions of the content, which one is public, and the visit counts.

Three rules live here and nowhere else:

* a save never publishes; ``publish`` names the version to make public, and ``unpublish`` takes the page down;
* everything saved has been through ``content.clean``;
* visits are counted only by ``refresh_delivery`` (a timed job), never by a request.
"""

from __future__ import annotations

from django.db import transaction
from django.db.models import Count
from django.utils import timezone

from connect_labs.effectiveness.investigations import content
from connect_labs.effectiveness.models import InvestigationsPage, InvestigationsVersion

PAGE_PK = 1
#: Visits Connect has verified. Every other status (pending, rejected, over the limit, duplicate) is left out.
VERIFIED_STATUS = "approved"


def state() -> InvestigationsPage | None:
    """The page's row with its public version, or None before anything was ever saved. Reads only."""
    return InvestigationsPage.objects.select_related("live").filter(pk=PAGE_PK).first()


def live_content() -> dict | None:
    """The content the public page shows, or None when the page is not public."""
    page = state()
    return page.live.data if page and page.live_id else None


def latest() -> InvestigationsVersion | None:
    return InvestigationsVersion.objects.order_by("-id").first()


def draft() -> dict:
    """What the editor opens on: the newest saved version, or the starting copy."""
    version = latest()
    return version.data if version else content.default()


def history(limit: int = 20) -> list[InvestigationsVersion]:
    return list(InvestigationsVersion.objects.select_related("saved_by").order_by("-id")[:limit])


def save(user, raw, note: str = "") -> InvestigationsVersion:
    """Keep ``raw`` as a new version (``ContentError`` if it fails the check). Unchanged content is not a new one."""
    data = content.clean(raw)
    newest = latest()
    if newest is not None and newest.data == data:
        return newest
    return InvestigationsVersion.objects.create(data=data, note=(note or "").strip()[:200], saved_by=user)


def restore(user, version_id: int) -> InvestigationsVersion:
    """An earlier version, saved again as the newest (checked again, since the rules may have tightened)."""
    old = InvestigationsVersion.objects.get(pk=version_id)
    return save(user, old.data, note=f"Restored version {old.pk}")


@transaction.atomic
def publish(user, version_id: int) -> InvestigationsPage:
    version = InvestigationsVersion.objects.get(pk=version_id)
    newest = latest()
    if newest is not None and newest.pk != version.pk:
        # The preview shows the newest draft, so publishing an older one would publish something nobody just looked at.
        raise content.ContentError(
            [f"Version {newest.pk} was saved after version {version.pk}. Reload, preview it, then publish."]
        )
    content.clean(version.data)
    page, _ = InvestigationsPage.objects.select_for_update().get_or_create(pk=PAGE_PK)
    page.live, page.published_by, page.published_at = version, user, timezone.now()
    page.save(update_fields=["live", "published_by", "published_at"])
    return page


@transaction.atomic
def unpublish(user) -> None:
    page = InvestigationsPage.objects.select_for_update().filter(pk=PAGE_PK).first()
    if page is not None and page.live_id:
        page.live, page.published_by, page.published_at = None, user, timezone.now()
        page.save(update_fields=["live", "published_by", "published_at"])


def delivery() -> dict | None:
    """The counts the timed job last kept, or None before its first run."""
    page = state()
    return page.delivery if page and page.delivery else None


def count_delivery(as_of=None) -> dict:
    """Verified visits by Connect delivery type, test opportunities left out: totals only, no partner, no place."""
    from connect_labs.pulse.models import PulseEvent, PulseOpportunity

    test_opps = PulseOpportunity.objects.filter(is_test=True).values("opportunity_id")
    rows = (
        PulseEvent.objects.filter(status=VERIFIED_STATUS)
        .exclude(opportunity_id__in=test_opps)
        .values("service_slug")
        .annotate(n=Count("id"))
    )
    services = {(r["service_slug"] or "other"): r["n"] for r in rows}
    return {
        "services": services,
        "total": sum(services.values()),
        "as_of": (as_of or timezone.now()).date().isoformat(),
    }


def refresh_delivery() -> dict:
    """Count again and keep the answer. A count of nothing is not kept: it means Pulse is empty, not Connect."""
    counted = count_delivery()
    if not counted["total"]:
        return counted
    page, _ = InvestigationsPage.objects.get_or_create(pk=PAGE_PK)
    page.delivery, page.delivery_at = counted, timezone.now()
    page.save(update_fields=["delivery", "delivery_at"])
    return counted
