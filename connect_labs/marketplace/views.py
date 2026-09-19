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

import datetime

from django.conf import settings
from django.contrib.auth.decorators import login_required
from django.http import JsonResponse
from django.shortcuts import get_object_or_404, render

from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import programs, queries
from connect_labs.solicitations.local_models import SolicitationResponse


def _controls(request) -> dict:
    """The filter controls as the person set them."""
    return {
        "q": request.GET.get("q", "").strip(),
        "countries": [v for v in request.GET.getlist("country") if v.strip()],
        "delivered": [v for v in request.GET.getlist("delivered") if v.strip()],
        "applied": [v for v in request.GET.getlist("applied") if v.strip()],
        "segment": request.GET.get("segment", "all").strip() or "all",
    }


def _population(request) -> dict:
    """Everything the network page needs, from one fetch.

    The page filters the same population five ways — the list, each of the
    three facet dimensions, and the globe — and re-querying for each was four
    round-trips plus three aggregates to draw one page. At a few hundred
    organisations the whole population is smaller than a page of most tables,
    so it is fetched once here and sliced in memory.

    `scope_excluding` is what makes a faceted rail work: each dimension is
    counted with its OWN filter dropped and every other applied, otherwise
    choosing Uganda removes Malawi from the country list and a second value can
    never be added.
    """
    selected = _controls(request)
    everyone = queries.all_rows_with_rounds()
    delivering = queries.delivering_names()
    delivered_by_name = queries.delivered_programs_by_org_name()

    def scope_excluding(dimension=None):
        return [
            org
            for org in everyone
            if queries.matches(
                org,
                query=selected["q"],
                countries=() if dimension == "country" else selected["countries"],
                delivered=() if dimension == "delivered" else selected["delivered"],
                applied=() if dimension == "applied" else selected["applied"],
                delivered_by_name=delivered_by_name,
            )
        ]

    scope = scope_excluding()
    shown = [org for org in scope if queries.in_segment(org, selected["segment"], delivering)]

    return {
        "rows": shown,
        "scope": scope,
        "scope_excluding": scope_excluding,
        "everyone": everyone,
        "delivering": delivering,
        "delivered_by_name": delivered_by_name,
        "selected": selected,
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
    state = _population(request)
    rows = state["rows"]
    delivering = state["delivering"]
    counts = queries.segment_counts(state["scope"], delivering)

    delivered_by_name = state["delivered_by_name"]
    listed = [
        {
            "org": org,
            "profile": getattr(org, "marketplace_profile", None),
            "delivering": org.name in delivering,
            # What this organisation has run, and what it has asked to run.
            # The FLW count that used to sit here was self-reported on a form
            # and written as "20-100 FLWs", "Medium" and "50-80 sampling sites
            # capacity" as often as a number, so it could be displayed but
            # never compared or sorted.
            "delivered": programs.chips(delivered_by_name.get(org.name, ())),
            "applied": programs.chips(queries.applied_programs_of(org)),
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
                    "countries": queries.facet_counts(state["scope_excluding"]("country"), delivering)["countries"],
                    "delivered": queries.facet_counts(state["scope_excluding"]("delivered"), delivering)["delivered"],
                    "applied": queries.facet_counts(state["scope_excluding"]("applied"), delivering)["applied"],
                },
                state["selected"],
                request.GET,
            ),
            "selected": state["selected"],
            "shown": len(rows),
            "total": len(state["everyone"]),
            "why": next((s["why"] for s in segments if s["selected"]), ""),
            "any_facet": bool(
                state["selected"]["countries"] or state["selected"]["delivered"] or state["selected"]["applied"]
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
    state = _population(request)
    return JsonResponse({"points": queries.map_points(state["rows"], state["delivering"])})


@login_required
def programs_page(request):
    """The marketplace by program: what has been paid, what is still funded,
    and who is waiting to do the work.

    Spent and services are Pulse's own figures, so this page and the Pulse wall
    cannot quote different numbers for the same program.
    """
    from connect_labs.pulse import costs

    view = costs.parse_view(request.GET.get("costs"))
    cards = queries.program_cards(view)
    by_state = {key: [] for key, _, _ in queries.STATES}
    for card in cards:
        by_state[card["state"]].append(card)
    sections = [
        {"key": key, "title": title, "why": why, "cards": by_state[key]}
        for key, title, why in queries.STATES
        if by_state[key]
    ]
    return render(
        request,
        "marketplace/programs.html",
        {
            "sections": sections,
            "costs_view": view,
            "totals": {
                "programs": len(cards),
                "spent": queries._money(sum(c["spent"] for c in cards)),
                "fixed": queries._money(sum(c["fixed"] for c in cards)),
                "fixed_raw": sum(c["fixed"] for c in cards),
                "remaining": queries._money(sum(c["remaining"] for c in cards)),
                "services": sum(c["services"] for c in cards),
                "applied": len(
                    set(
                        SolicitationResponse.objects.exclude(llo_entity=None)
                        .exclude(solicitation__delivery_type="")
                        .values_list("llo_entity_id", flat=True)
                    )
                ),
            },
        },
    )


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
    applicants = queries.round_applicants(round_, queries.first_service_by_org_name())
    trend = queries.submission_trend(round_)
    return render(
        request,
        "marketplace/round.html",
        {
            "round": round_,
            "applicants": applicants,
            "trend": trend,
            "span_end": trend[-1]["start"] + datetime.timedelta(days=trend[-1]["days"] - 1) if trend else None,
            "since": queries.round_since(round_),
            # Split deliberately: "started after this round" is the number
            # somebody wants when they ask what a round produced, and lumping
            # it together with organisations that were already delivering
            # answers a different question while looking like that one.
            "after_count": sum(1 for a in applicants if a["outcome"] == "after"),
            "before_count": sum(1 for a in applicants if a["outcome"] == "before"),
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
            # Delivered comes off the opportunities already fetched above, so
            # the panel and the badge cannot disagree about what this
            # organisation runs.
            "delivered_programs": programs.chips(o.service_slug for o in opportunities),
            "applied_programs": programs.chips(
                r.solicitation.delivery_type for r in responses if programs.is_program(r.solicitation.delivery_type)
            ),
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
