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
view of it. Signed in is not enough either: who answered a round, and what they
said, shows only to members of the organisation that ran it (and platform
support) -- `access.can_read_round_responses`. Everyone else sees the call and
how many answered it.
"""

from __future__ import annotations

import datetime

from canopy_sdk.django.pages import panel_context as canopy_panel_context
from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import Http404, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme
from django.views.decorators.http import require_POST

from connect_labs.labs.chrome import page_chrome
from connect_labs.labs.models import LabsOrg
from connect_labs.marketplace import programs, queries
from connect_labs.solicitations import access, past_calls, vocabulary
from connect_labs.solicitations.local_models import Solicitation, SolicitationResponse

# Nothing in the marketplace is scoped by the labs context selector, and on a
# phone it was the largest thing on the screen. The Pulse widget takes its
# place: the figures these pages quote are Pulse's own.
MARKETPLACE_CHROME = page_chrome(labs_context=False, pulse_widget=True)


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
    everyone = queries.all_rows_with_rounds(access.readable_round_ids(request))
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


# Typefaces on trial for the marketplace: ?font=<key> sets the page in one,
# and /marketplace/fonts/ shows them side by side. An allowlist, because the
# family name is written into the page's CSS. (key, Google family, one line.)
FONT_CANDIDATES = (
    ("inter", "Inter", "Neutral and precise. The safest choice, and the most common one."),
    ("dm-sans", "DM Sans", "Low contrast and slightly geometric. Calm, compact numbers."),
    ("plus-jakarta", "Plus Jakarta Sans", "Modern and warm, with a little flair in the capitals."),
    ("figtree", "Figtree", "Clean and friendly with round shapes. Easy on dense cards."),
    ("ibm-plex", "IBM Plex Sans", "Engineered and serious. Reads like infrastructure."),
    ("instrument", "Instrument Sans", "Narrow and crisp. Fits more on a line without shrinking."),
    ("public-sans", "Public Sans", "Government-grade neutral. Plain and very legible."),
    ("outfit", "Outfit", "Geometric and soft. More brand, less spreadsheet."),
    ("sora", "Sora", "Wide and technical, with a strong presence in headings."),
    ("lexend", "Lexend", "Built for readability. Generous spacing, very open."),
    ("source-sans", "Source Sans 3", "Humanist and efficient. Good in long, dense text."),
    ("nunito-sans", "Nunito Sans", "Rounded and approachable, the softest of the set."),
)
# Manrope and Geist were tried first and passed over; they stay usable by key.
TRIAL_FONTS = {"manrope": "Manrope", "geist": "Geist", **{key: family for key, family, _ in FONT_CANDIDATES}}


@login_required
@MARKETPLACE_CHROME
def fonts(request):
    """Every candidate typeface side by side, each on a slice of this page."""
    faces = [
        {"key": "", "family": "Work Sans", "note": "The current font. Wide and friendly, but loose at small sizes."}
    ]
    faces += [{"key": key, "family": family, "note": note} for key, family, note in FONT_CANDIDATES]
    families = "&".join(f"family={f['family'].replace(' ', '+')}:wght@400;500;600;700" for f in faces)
    return render(request, "marketplace/fonts.html", {"faces": faces, "families": families})


@login_required
@MARKETPLACE_CHROME
def home(request):
    """The marketplace: every kind of work on Connect, by program.

    The landing page used to be a list of rounds, which answered a question
    almost nobody arrives with. What the work IS comes first; the rounds are
    the drill-down from it, one click away.

    Spent and services are Pulse's own figures, so this page and the Pulse wall
    cannot quote different numbers for the same program.
    """
    from connect_labs.pulse import costs

    view = costs.parse_view(request.GET.get("costs"))
    font = request.GET.get("font", "")
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
            # A typeface trial (?font=<key>) — anything unlisted is the house face.
            "font": font if font in TRIAL_FONTS else "",
            "font_family": TRIAL_FONTS.get(font, ""),
            "network": queries.network_totals(),
            "open_round_count": queries.open_rounds().count(),
            # The suppliers' side of the marketplace: tenders a buyer is asking
            # suppliers to quote on now, as the public market lists them.
            "supply_tender_count": queries.supply_tenders_open(),
            "totals": {
                "programs": len(cards),
                "spent": queries._money(sum(c["spent"] for c in cards)),
                "fixed": queries._money(sum(c["fixed"] for c in cards)),
                "fixed_raw": sum(c["fixed"] for c in cards),
                "remaining": queries._money(sum(c["remaining"] for c in cards)),
                "services": sum(c["services"] for c in cards),
                "applied": len(
                    set(
                        SolicitationResponse.objects.directory_rounds()
                        .exclude(llo_entity=None)
                        .exclude(solicitation__delivery_type="")
                        .values_list("llo_entity_id", flat=True)
                    )
                ),
            },
        },
    )


@login_required
@MARKETPLACE_CHROME
def network(request):
    """The organizations, filterable, with the globe showing what is in scope."""
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
            # In the page rather than fetched after it: the globe draws from
            # the same population this render already holds, and a second
            # request recomputed all of it to say the same thing.
            "points": queries.map_points(rows, delivering),
            "unreadable": queries.unreadable_rounds(),
            # The agent panel is told WHICH organisations are on screen and how
            # the visitor narrowed to them — never the rows, which it reads for
            # itself through `marketplace_orgs_get`.
            "canopy_panel": canopy_panel_context(
                request,
                resource="labs-marketplace://orgs",
                backing_tool="marketplace_orgs_get",
                visible_ids=[row["org"].slug for row in listed],
                filters={k: v for k, v in state["selected"].items() if v},
                path=request.path,
            ),
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
@MARKETPLACE_CHROME
def rounds(request):
    """Every expression of interest and request for proposals, open first.

    The drill-down from the landing page: what the network has been asked, and
    what it answered.
    """
    return render(
        request,
        "marketplace/rounds.html",
        {
            "network": queries.network_totals(),
            "groups": [
                {"label": "Open now", "rounds": list(queries.open_rounds())},
                {"label": "Closed", "rounds": list(queries.closed_rounds())},
            ],
        },
    )


@login_required
@MARKETPLACE_CHROME
def round_detail(request, slug):
    """One round: who answered it, what it asked, and what became of them."""
    round_ = get_object_or_404(queries.rounds_with_counts().select_related("owner_org"), slug=slug)
    # Who applied, when, and what became of each is the submissions talking:
    # only the round's owner reads it. Anyone else gets the call and the count.
    can_read = access.can_read_round_responses(request, round_)
    applicants = queries.round_applicants(round_, queries.first_service_by_org_name()) if can_read else []
    trend = queries.submission_trend(round_) if can_read else []
    mix = queries.round_mix(round_, applicants) if can_read else {}
    reviewed = queries.review_status(round_, applicants) if can_read else {}
    return render(
        request,
        "marketplace/round.html",
        {
            "round": round_,
            "can_read": can_read,
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
            "mix": mix,
            "reviewed": reviewed,
            # Its public page on Solicitations, once it has stopped taking
            # applications: anyone can read that one, so it is the link to send.
            "public_call_url": (
                reverse("solicitations:past_detail", args=[round_.slug]) if past_calls.is_past_call(round_) else ""
            ),
            # This round, and the organisations that answered it. Naming the
            # round in the resource is what makes "draft an email for each of
            # these orgs to submit to THIS EOI" answerable without the visitor
            # having to say which one they are looking at.
            "canopy_panel": canopy_panel_context(
                request,
                resource=f"labs-marketplace://rounds/{round_.slug}",
                backing_tool="marketplace_rounds_list",
                visible_ids=[a["org"].slug for a in applicants if a.get("org")],
                filters={"round": round_.slug},
                path=request.path,
            ),
        },
    )


# A score's band and shade: green 7-10, amber 4-6, red 1-3. Colour deepens
# toward the ends of the scale (10 and 1) and stays pale in the middle, so the
# strongest and weakest cells draw the eye, not the middling ones.
_SHADES = {
    1: ("#f87171", "#450a0a"),
    2: ("#fca5a5", "#7f1d1d"),
    3: ("#fecaca", "#991b1b"),
    4: ("#fde68a", "#78350f"),
    5: ("#fef3c7", "#92400e"),
    6: ("#fefce8", "#854d0e"),
    7: ("#bbf7d0", "#14532d"),
    8: ("#86efac", "#14532d"),
    9: ("#4ade80", "#052e16"),
    10: ("#22c55e", "#052e16"),
}


def score_shade(score) -> str:
    """Inline style for a 1-10 score; empty when there is none."""
    if not isinstance(score, (int, float)):
        return ""
    background, text = _SHADES[min(max(int(round(score)), 1), 10)]
    return f"background: {background}; color: {text}"


def _score_cell(score) -> dict:
    return {"score": score if isinstance(score, (int, float)) else None, "style": score_shade(score)}


def _outcome_chip(key: str) -> str:
    """The classes of an outcome's chip, the same on every page."""
    return {
        "pick": "bg-green-100 text-green-800",
        "backup": "bg-teal-100 text-teal-800",
        "waitlist": "bg-amber-50 text-amber-800",
        "pass": "bg-gray-100 text-gray-600",
    }.get(key, "bg-white text-gray-400 border border-brand-border-light")


def _flag_chips(keys: list[str]) -> list[dict]:
    """Each flag with the label and definition a reader sees."""
    return [
        {"key": key, **vocabulary.FLAGS.get(key, {"label": key, "definition": "Not one of the defined flags."})}
        for key in keys
    ]


@login_required
@require_POST
def round_summary(request, slug):
    """Link the round to its written summary, by hand: what `marketplace_round_summary_set` does for an agent."""
    from django.core.exceptions import PermissionDenied, ValidationError

    from connect_labs.solicitations.data_access import SolicitationsDataAccess

    back = request.POST.get("next") or reverse("marketplace:round", args=[slug])
    if not url_has_allowed_host_and_scheme(back, allowed_hosts={request.get_host()}):
        back = reverse("marketplace:round", args=[slug])
    try:
        SolicitationsDataAccess(request=request).set_round_summary(slug, request.POST.get("summary_url", ""))
    except Solicitation.DoesNotExist as e:
        raise Http404("No such round") from e
    except PermissionDenied as e:
        raise Http404("No such round") from e
    except ValidationError:
        messages.error(request, "That is not an https link. Paste the summary's full address.")
    return redirect(back)


@login_required
@require_POST
def round_version_delete(request, slug, version):
    """Delete an earlier criteria version and every review scored on it, by hand."""
    from django.core.exceptions import PermissionDenied, ValidationError

    from connect_labs.solicitations.data_access import SolicitationsDataAccess
    from connect_labs.solicitations.local_models import RubricVersion

    try:
        result = SolicitationsDataAccess(request=request).delete_round_version(slug, version)
    except (Solicitation.DoesNotExist, RubricVersion.DoesNotExist, PermissionDenied) as e:
        raise Http404("No such version") from e
    except ValidationError as e:
        messages.error(request, " ".join(e.messages))
    else:
        messages.success(
            request,
            f"Deleted criteria version {version.lstrip('v')} and the {result['reviews_deleted']} "
            f"review{'s' if result['reviews_deleted'] != 1 else ''} scored on it.",
        )
    return redirect("marketplace:round_scores", slug=slug)


@login_required
@MARKETPLACE_CHROME
def round_scores(request, slug):
    """A round's scores side by side: every applicant against every criterion, to filter and compare."""
    round_ = get_object_or_404(queries.rounds_with_counts().select_related("owner_org"), slug=slug)
    # The scores are the submissions read and judged: the owner's only. To
    # anyone else this page does not exist, as a submission does not.
    if not access.can_read_round_responses(request, round_):
        raise Http404("No such round")
    history = queries.version_history(round_)
    names = [v["version"] for v in history]
    shown = request.GET.get("version") if request.GET.get("version") in names else round_.rubric_version
    first_service = queries.first_service_by_org_name()
    applicants, reviewed, summary, _ = queries.scores_at(round_, shown, first_service)
    # Compared with the version before the one shown, unless another is asked for.
    earlier = [n for n in names if n != shown]
    compare_with = request.GET.get("compare") if request.GET.get("compare") in earlier else None
    if compare_with is None and shown in names and names.index(shown) > 0:
        compare_with = names[names.index(shown) - 1]
    comparison = None
    if compare_with:
        first, second = sorted([compare_with, shown], key=names.index)
        comparison = queries.compare_at(round_, first, second, first_service)
        for move in comparison["moved"]:
            move["from_style"], move["to_style"] = (_outcome_chip(move["from_key"]), _outcome_chip(move["to_key"]))
    for a in applicants:
        a["cells"] = [_score_cell(a["review"]["criteria_scores"].get(c["id"])) for c in summary["criteria"]]
        a["flag_chips"] = _flag_chips(a["review"]["flags"])
    # Shades are the page's: the figures stay as `marketplace_round_scores` returns them.
    criteria_rows = [
        {
            **c,
            "mean_style": score_shade(c["mean"]),
            "spread": [{**b, "colour": _SHADES[b["score"]][0] if b["count"] else "#f3f4f6"} for b in c["spread"]],
        }
        for c in summary["criteria"]
    ]
    countries = sorted({a["country"] for a in applicants if a["country"]})
    return render(
        request,
        "marketplace/round_scores.html",
        {
            "round": round_,
            "applicants": applicants,
            "reviewed": reviewed,
            "summary": summary,
            "history": history,
            "shown_version": shown,
            "is_current": shown == round_.rubric_version,
            "compare_with": compare_with,
            "comparison": comparison,
            "criteria_rows": criteria_rows,
            "countries": countries,
            "flag_counts": [
                {**chip, "count": count}
                for chip, (_, count) in zip(_flag_chips([k for k, _ in summary["flags"]]), summary["flags"])
            ],
            "outcomes": [
                {"key": key, **meaning, "count": summary["outcomes"][key]}
                for key, meaning in vocabulary.OUTCOMES.items()
            ]
            + [{"key": "", **vocabulary.NO_OUTCOME, "count": summary["outcomes"]["undecided"]}],
            "flag_vocabulary": list(vocabulary.FLAGS.items()),
            # The same shades for the figures the filter recomputes in the page.
            "shade_scale": {str(score): list(shade) for score, shade in _SHADES.items()},
            "set_by_people": sum(1 for a in applicants if a["review"]["by_person"] and a["review"]["outcome"]),
            "can_edit": access.can_set_round_criteria(request, round_),
            # The same figures an agent reads through `marketplace_round_scores`.
            "canopy_panel": canopy_panel_context(
                request,
                resource=f"labs-marketplace://rounds/{round_.slug}/scores",
                backing_tool="marketplace_round_scores",
                visible_ids=[a["org"].slug for a in applicants if a.get("org")],
                filters={"round": round_.slug},
                path=request.path,
            ),
        },
    )


@login_required
@MARKETPLACE_CHROME
def organisation(request, slug):
    org = get_object_or_404(LabsOrg.objects.select_related("marketplace_profile"), slug=slug)
    # Only the rounds this viewer may read: an application history is the
    # organisation's submissions, and those belong to whoever ran each round.
    responses = list(
        org.solicitation_responses.directory_rounds()
        .filter(solicitation_id__in=access.readable_round_ids(request))
        .select_related("solicitation")
        .order_by("-submission_date")
    )

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
            # Calls for partners this organisation owns and lists publicly:
            # the header links to its solicitations page when there are any.
            "listed_calls": (
                Solicitation.objects.calls().filter(owner_org=org, listed=True).exclude(status="draft").count()
            ),
        },
    )


@login_required
@MARKETPLACE_CHROME
def unmatched(request):
    """Submissions no rule could safely attribute — the review queue.

    They are here rather than guessed at because a misattributed submission puts
    one organisation's words on another organisation's record, where they will
    be read as that organisation's own.
    """
    from connect_labs.marketplace.directory import DIRECTORY_ID, RESPONSE_MAPPING_TAB

    readable = access.readable_round_ids(request)
    rows = (
        SolicitationResponse.objects.directory_rounds()
        .filter(solicitation_id__in=readable)
        .filter(match_state=SolicitationResponse.MATCH_UNMATCHED)
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
            "decided_count": SolicitationResponse.objects.directory_rounds()
            .filter(solicitation_id__in=readable)
            .filter(match_state__in=[SolicitationResponse.MATCH_HUMAN, SolicitationResponse.MATCH_NOT_LLO])
            .count(),
        },
    )
