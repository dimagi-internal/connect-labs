"""The organisation directory.

Two screens: filter the registry, and open one organisation. The point of the
second is that everything labs knows about an organisation appears in one place
— who they are, what they have delivered on Connect, and every EOI they have
ever answered — because that knowledge was previously spread across a
spreadsheet, a telemetry table and eighteen Google Forms.

Login-gated in full. Submission text is what an organisation wrote about itself
while applying for work; it is not public, and there is no anonymous view of it.
"""

from __future__ import annotations

from django.contrib.auth.decorators import login_required
from django.db.models import Count, Max, Q
from django.shortcuts import get_object_or_404, render

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace.models import OrgProfile
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse


def _delivering_names() -> set[str]:
    """Organisation names that have delivered anything on Connect.

    Computed from the pulse spine rather than stored, so it cannot go stale, and
    reusing pulse's own guards (the 2025-01-14 works floor, sync_ts over
    field_ts) rather than re-deriving them here.
    """
    from connect_labs.pulse.network_api import first_service_by_partner

    return set(first_service_by_partner())


@login_required
def directory(request):
    orgs = (
        LabsOrg.objects.select_related("marketplace_profile")
        .annotate(
            contact_count=Count("contacts", distinct=True),
            application_count=Count("solicitation_responses", distinct=True),
            last_applied=Max("solicitation_responses__submission_date"),
        )
        .order_by("name")
    )

    delivering = _delivering_names()

    country = request.GET.get("country", "").strip()
    sector = request.GET.get("sector", "").strip()
    status = request.GET.get("status", "").strip()
    applied = request.GET.get("applied", "").strip()
    query = request.GET.get("q", "").strip()

    if query:
        orgs = orgs.filter(Q(name__icontains=query) | Q(short_name__icontains=query))
    if country:
        orgs = orgs.filter(marketplace_profile__countries__icontains=country)
    if sector:
        orgs = orgs.filter(marketplace_profile__sectors__icontains=sector)
    if applied:
        orgs = orgs.filter(solicitation_responses__solicitation__slug=applied)
    if status == "no_contact":
        orgs = orgs.filter(contact_count=0)
    elif status == "contactable":
        orgs = orgs.filter(contact_count__gt=0)

    rows = []
    for org in orgs.distinct():
        is_delivering = org.name in delivering
        if status == "delivering" and not is_delivering:
            continue
        # The bench: recruited, never delivered. The single most useful segment
        # here — organisations that said yes once and were never activated.
        if status == "bench" and is_delivering:
            continue
        rows.append(
            {
                "org": org,
                "profile": getattr(org, "marketplace_profile", None),
                "delivering": is_delivering,
            }
        )

    profiles = list(OrgProfile.objects.all())
    countries = sorted({c for p in profiles for c in (p.countries or []) if c})
    sectors = sorted({s for p in profiles for s in (p.sectors or []) if s})

    return render(
        request,
        "marketplace/directory.html",
        {
            "rows": rows,
            "countries": countries,
            "sectors": sectors,
            "rounds": Solicitation.objects.order_by("-published_on"),
            "total": LabsOrg.objects.count(),
            "delivering_count": sum(1 for r in rows if r["delivering"]),
            "contactable_count": sum(1 for r in rows if r["org"].contact_count),
            "unmatched_count": SolicitationResponse.objects.filter(
                match_state=SolicitationResponse.MATCH_UNMATCHED
            ).count(),
            # A round labs cannot read is NOT a round with no applicants, and
            # the difference has to be visible or the first reads as the second.
            "unreadable_rounds": Solicitation.objects.exclude(sa_access_state="ok").order_by("title"),
            "selected": {
                "country": country,
                "sector": sector,
                "status": status,
                "applied": applied,
                "q": query,
            },
        },
    )


def _connect_slugs_for(org) -> set[str]:
    """Every Connect workspace slug that resolves to this organisation.

    Resolved the same way pulse decides an organisation is delivering, so the
    badge and the delivery panel cannot contradict each other. They did: an
    organisation could read "delivering" beside "no Connect workspace
    attributed to it", because the badge matched on NAME through pulse's
    resolver while the panel looked only at hand-curated slug attributions.
    Two true statements that together read as a bug.

    A curated attribution still counts — it is how a workspace the matcher
    cannot reach gets here at all.
    """
    from connect_labs.pulse.models import PulseOpportunity
    from connect_labs.pulse.partner_names import resolve as resolve_partner

    slugs = {s.slug for s in org.connect_slugs.all()}
    if org.connect_organization_slug:
        slugs.add(org.connect_organization_slug)
    for org_slug in PulseOpportunity.objects.exclude(org_slug="").values_list("org_slug", flat=True).distinct():
        if resolve_partner(org_slug)["parent"] == org.name:
            slugs.add(org_slug)
    return slugs


@login_required
def organisation(request, slug):
    org = get_object_or_404(LabsOrg.objects.select_related("marketplace_profile"), slug=slug)
    responses = list(org.solicitation_responses.select_related("solicitation").order_by("-submission_date"))

    # Pair each question with its answer here rather than in the template: the
    # question list belongs to the round and the answers to the submission, and
    # a template filter that reached into a dict by key would hide that join.
    for response in responses:
        answers = response.responses or {}
        response.answer_pairs = [
            (question.get("text", question.get("id", "")), answers.get(question.get("id"), ""))
            for question in (response.solicitation.questions or [])
            if answers.get(question.get("id"))
        ]

    from connect_labs.pulse.models import PulseOpportunity

    slugs = _connect_slugs_for(org)
    opportunities = (
        PulseOpportunity.objects.filter(org_slug__in=slugs).order_by("-lifetime_visit_count")
        if slugs
        else PulseOpportunity.objects.none()
    )

    return render(
        request,
        "marketplace/organisation.html",
        {
            "org": org,
            "profile": getattr(org, "marketplace_profile", None),
            "contacts": org.contacts.all(),
            "responses": responses,
            "connect_slugs": org.connect_slugs.all(),
            "opportunities": opportunities,
            "delivering": org.name in _delivering_names(),
            "visits": sum(o.lifetime_visit_count for o in opportunities),
        },
    )


@login_required
def unmatched(request):
    """Submissions no rule could safely attribute — the review queue.

    They are here rather than guessed at because a misattributed submission puts
    one organisation's words on another organisation's record, where they will
    be read as that organisation's own.
    """
    rows = (
        SolicitationResponse.objects.filter(match_state=SolicitationResponse.MATCH_UNMATCHED)
        .select_related("solicitation")
        .order_by("solicitation__slug", "source_row")
    )
    from connect_labs.marketplace.directory import DIRECTORY_ID, RESPONSE_MAPPING_TAB

    return render(
        request,
        "marketplace/unmatched.html",
        {
            "rows": rows,
            "mapping_tab": RESPONSE_MAPPING_TAB,
            "directory_url": f"https://docs.google.com/spreadsheets/d/{DIRECTORY_ID}/edit",
            "decided_count": SolicitationResponse.objects.filter(
                match_state__in=[SolicitationResponse.MATCH_HUMAN, SolicitationResponse.MATCH_NOT_LLO]
            ).count(),
        },
    )
