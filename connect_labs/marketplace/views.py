"""The Connect Marketplace.

A marketplace has two sides, and the first version of this app only had one.
The organisations are the supply; the EOI and RFP rounds they answer are the
demand, and a directory showing only the first reads as an internal filter over
a spreadsheet — which is what it was.

So: a home that states the network and what is open, a page per round showing
who answered it and what became of them, and the network itself, filterable and
drawn on the same globe the wall display uses.

Login-gated throughout. Submission text is what an organisation wrote about
itself while applying for work; it is not public, and there is no anonymous
view of it.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.db.models import Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import queries
from connect_labs.solicitations.local_models import SolicitationResponse


def _filtered(request, exclude=None):
    """The organisations in scope, plus the controls that put them there.

    One place, because the list, the counts, the facets and the globe must all
    be looking at the same set — a count that disagrees with the list is worse
    than no count at all.

    `exclude` drops ONE facet's own filter while keeping every other. That is
    how a faceted rail has to count: if choosing Uganda also removed Malawi
    from the country list, a second country could never be added, and the
    multi-select the rail exists for would be unreachable through the UI.
    """
    rows = queries.org_rows()

    query = request.GET.get("q", "").strip()
    # Facets are multi-select. Picking two countries should widen the answer,
    # not replace it — that is the whole reason a rail beats a dropdown.
    countries = [v for v in request.GET.getlist("country") if v.strip()]
    sectors = [v for v in request.GET.getlist("sector") if v.strip()]
    applied = [v for v in request.GET.getlist("applied") if v.strip()]
    segment = request.GET.get("segment", "all").strip() or "all"

    if query:
        rows = rows.filter(Q(name__icontains=query) | Q(short_name__icontains=query))
    if countries and exclude != "country":
        match = Q()
        for value in countries:
            match |= Q(marketplace_profile__countries__icontains=value)
        rows = rows.filter(match)
    if sectors and exclude != "sector":
        match = Q()
        for value in sectors:
            match |= Q(marketplace_profile__sectors__icontains=value)
        rows = rows.filter(match)
    if applied and exclude != "applied":
        rows = rows.filter(solicitation_responses__solicitation__slug__in=applied)

    scope = list(rows.distinct())
    delivering = queries.delivering_names()
    shown = [r for r in scope if queries.in_segment(r, segment, delivering)]

    return {
        "rows": shown,
        "scope": scope,
        "delivering": delivering,
        "selected": {
            "q": query,
            "countries": countries,
            "sectors": sectors,
            "applied": applied,
            "segment": segment,
        },
    }


@login_required
def home(request):
    """The marketplace: what the network is, and what is open."""
    return render(
        request,
        "marketplace/home.html",
        {
            "totals": queries.network_totals(),
            "open_rounds": queries.open_rounds(),
            "closed_rounds": queries.closed_rounds(),
            "unreadable": queries.unreadable_rounds(),
            "unmatched_count": SolicitationResponse.objects.filter(
                match_state=SolicitationResponse.MATCH_UNMATCHED
            ).count(),
        },
    )


@login_required
def network(request):
    """The organisations, filterable, with the globe showing what is in scope."""
    state = _filtered(request)
    rows = state["rows"]
    delivering = state["delivering"]
    counts = queries.segment_counts(state["scope"], delivering)

    slugs_by_name = queries.workspace_slugs_by_org_name()
    rounds_by_org = queries.rounds_by_org(rows)
    listed = [
        {
            "org": org,
            "profile": getattr(org, "marketplace_profile", None),
            "delivering": org.name in delivering,
            "rounds": rounds_by_org.get(org.pk, []),
            "workspaces": len(slugs_by_name.get(org.name, ())),
        }
        for org in rows
    ]

    segments = [
        {
            "key": key,
            "label": label,
            "why": why,
            "count": counts[key],
            "selected": key == state["selected"]["segment"],
        }
        for key, label, why in queries.SEGMENTS
    ]

    return render(
        request,
        "marketplace/network.html",
        {
            "listed": listed,
            "segments": segments,
            "rail": queries.facet_rail(
                {
                    "countries": queries.facet_counts(_filtered(request, exclude="country")["scope"], delivering)[
                        "countries"
                    ],
                    "sectors": queries.facet_counts(_filtered(request, exclude="sector")["scope"], delivering)[
                        "sectors"
                    ],
                    "rounds": queries.facet_counts(_filtered(request, exclude="applied")["scope"], delivering)[
                        "rounds"
                    ],
                },
                state["selected"],
                request.GET,
            ),
            "selected": state["selected"],
            "shown": len(rows),
            "total": LabsOrg.objects.count(),
            "why": next((s["why"] for s in segments if s["selected"]), ""),
            "any_facet": bool(
                state["selected"]["countries"] or state["selected"]["sectors"] or state["selected"]["applied"]
            ),
            "mapbox_token": getattr(settings, "MAPBOX_TOKEN", "") or "",
            "unreadable": queries.unreadable_rounds(),
        },
    )


@login_required
def network_points(request):
    """The organisations in scope, as points for the globe.

    Its own endpoint rather than markup, so changing a filter moves the map
    without reloading the page or re-rendering every row.
    """
    state = _filtered(request)
    return JsonResponse({"points": queries.map_points(state["rows"], state["delivering"])})


@login_required
def rounds(request):
    """Every round, open first."""
    return render(
        request,
        "marketplace/rounds.html",
        {
            "groups": [
                {"label": "Open now", "rounds": list(queries.open_rounds())},
                {"label": "Closed", "rounds": list(queries.closed_rounds())},
            ]
        },
    )


@login_required
def round_detail(request, slug):
    """One round: who answered it, what it asked, and what became of them."""
    round_ = get_object_or_404(queries.rounds_with_counts(), slug=slug)
    applicants = queries.round_applicants(round_, queries.delivering_names())
    return render(
        request,
        "marketplace/round.html",
        {
            "round": round_,
            "applicants": applicants,
            "delivering_count": sum(1 for a in applicants if a["outcome"] == "delivering"),
            "never_count": sum(1 for a in applicants if a["outcome"] == "never"),
            "unresolved_count": sum(1 for a in applicants if a["outcome"] == "unresolved"),
            "questions": (round_.questions or [])[:8],
            "question_total": len(round_.questions or []),
        },
    )


@login_required
def organisation(request, slug):
    org = get_object_or_404(LabsOrg.objects.select_related("marketplace_profile"), slug=slug)
    responses = list(org.solicitation_responses.select_related("solicitation").order_by("-submission_date"))

    # Pair each question with its answer here rather than in the template: the
    # question list belongs to the round and the answers to the submission, and
    # a template filter reaching into a dict by key would hide that join.
    for response in responses:
        answers = response.responses or {}
        response.answer_pairs = [
            (question.get("text", question.get("id", "")), answers.get(question.get("id"), ""))
            for question in (response.solicitation.questions or [])
            if answers.get(question.get("id"))
        ]

    from connect_labs.pulse.models import PulseOpportunity

    # Resolved the same way pulse decides an organisation is delivering, so the
    # badge and this panel cannot contradict each other.
    slugs = {s.slug for s in org.connect_slugs.all()}
    if org.connect_organization_slug:
        slugs.add(org.connect_organization_slug)
    slugs |= queries.workspace_slugs_by_org_name().get(org.name, set())

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
            "delivering": org.name in queries.delivering_names(),
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
    from connect_labs.marketplace.directory import DIRECTORY_ID, RESPONSE_MAPPING_TAB

    rows = (
        SolicitationResponse.objects.filter(match_state=SolicitationResponse.MATCH_UNMATCHED)
        .select_related("solicitation")
        .order_by("solicitation__slug", "source_row")
    )
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
