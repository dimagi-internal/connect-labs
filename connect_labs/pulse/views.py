"""Page views for Pulse.

Three entry points matching the three delivery modes: an authenticated index,
an authenticated display (kiosk / presenter), and a public token-scoped link.

The public view is deliberately the only unauthenticated surface in the app,
and it is read-only. It exposes no Connect credentials and no drill-through to
raw records.
"""

from __future__ import annotations

import secrets

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect, render
from django.utils import timezone
from django.views import View

from connect_labs.pulse.models import PulseOpportunity, PulsePublicToken, PulseReport, PulseScalar

# Registered layouts. A layout is an arrangement of cards; adding one is a
# template plus an entry here, which is the point of the card/layout split.
LAYOUTS = {
    "nightmap": {
        "label": "Night map",
        "blurb": "Services ignite as points of light. The geography draws itself out of the work.",
    },
    "mission": {
        "label": "Mission control",
        "blurb": "Dense multi-panel telemetry — everything at once.",
    },
    "financial": {
        "label": "Financial view",
        "blurb": "Funds flow: committed, accrued, paid, invoiced.",
    },
}

DEFAULT_LAYOUT = "nightmap"


def _display_context(layout: str, *, public: bool, show_partner_names: bool = True) -> dict:
    from django.conf import settings

    scope = PulseScalar.objects.filter(key="scope").first()
    return {
        # Real basemap via the shared ConnectMap module, so the map carries
        # coastlines and country borders instead of asking a viewer to infer
        # geography from dots alone.
        "mapbox_token": getattr(settings, "MAPBOX_TOKEN", "") or "",
        "layout": layout,
        "layout_meta": LAYOUTS.get(layout, LAYOUTS[DEFAULT_LAYOUT]),
        "layouts": LAYOUTS,
        "is_public": public,
        "show_partner_names": show_partner_names,
        "scope": scope.value if scope else {},
        "opportunity_count": PulseOpportunity.objects.count(),
    }


class PulseIndexView(LoginRequiredMixin, View):
    """Layout picker plus ingest status and link management.

    Links are created and revoked here rather than from a shell. Handing someone
    an unauthenticated URL to production delivery data is the riskiest thing
    this app does, and it belongs somewhere that can show, at the moment of
    doing it, what the link exposes and how to take it back.
    """

    def post(self, request):
        action = request.POST.get("action", "")

        if action == "create":
            layout = request.POST.get("layout", DEFAULT_LAYOUT)
            if layout not in LAYOUTS:
                messages.error(request, f"Unknown layout {layout!r}.")
                return redirect("pulse:index")
            token = mint_public_token(
                request.user,
                label=request.POST.get("label", "").strip(),
                layout=layout,
                # Absent checkbox means names are shown, which is the more
                # disclosing branch -- so it is stated back in the message
                # rather than left to be discovered.
                show_partner_names=not request.POST.get("anonymise_partners"),
            )
            if token.show_partner_names:
                messages.warning(
                    request,
                    f"Created a {LAYOUTS[layout]['label']} link. Anyone with the URL sees partner "
                    "organisation names, their delivery volumes and per-service rates. No "
                    "beneficiary or worker identities. Use “Anonymise partners” to withhold names.",
                )
            else:
                messages.success(request, f"Created a {LAYOUTS[layout]['label']} link with partner names withheld.")

        elif action == "revoke":
            n = PulsePublicToken.objects.filter(token=request.POST.get("token", ""), revoked=False).update(
                revoked=True
            )
            messages.success(
                request,
                "Link revoked — that URL now 404s, indistinguishably from one that never existed."
                if n
                else "That link was already revoked.",
            )

        elif action == "partner_names":
            show = request.POST.get("show") == "on"
            n = PulsePublicToken.objects.filter(token=request.POST.get("token", ""), revoked=False).update(
                show_partner_names=show
            )
            if n:
                messages.success(
                    request,
                    "Link now names partner organisations."
                    if show
                    else "Partner names withheld on that link — it now shows descriptors instead.",
                )
            else:
                messages.error(request, "No live link with that token.")

        else:
            messages.error(request, "Unknown action.")

        # Redirect after POST so a refresh cannot mint a second link.
        return redirect("pulse:index")

    def get(self, request):
        from connect_labs.pulse.api import _ingest_state

        return render(
            request,
            "pulse/index.html",
            {
                "layouts": LAYOUTS,
                "ingest": _ingest_state(),
                "scope": (PulseScalar.objects.filter(key="scope").first() or PulseScalar(value={})).value,
                "tokens": PulsePublicToken.objects.filter(revoked=False).order_by("-created_at")[:20],
                # Every real engagement, ordered the way the display menus
                # order things: running first, then by lifetime volume. Test
                # scaffolding is excluded by the same two rules the partner
                # menu uses -- drop opportunities under a test programme, and
                # drop ones whose own name is scaffolding -- because a funder
                # picking "[TEST 02] ..." out of this list is a bad moment.
                "dossier_opps": self._dossier_opps(),
            },
        )

    @staticmethod
    def _dossier_opps() -> list:
        from connect_labs.pulse.models import PulseProgram
        from connect_labs.pulse.normalize import looks_like_test

        test_pids = set(PulseProgram.objects.filter(is_test=True).values_list("program_id", flat=True))
        return [
            o
            for o in PulseOpportunity.objects.order_by("-is_active", "-lifetime_visit_count").values(
                "opportunity_id", "name", "is_active", "lifetime_visit_count", "program_id"
            )
            if o["program_id"] not in test_pids and not looks_like_test(o["name"])
        ]


class PulseDisplayView(LoginRequiredMixin, View):
    def get(self, request, layout=DEFAULT_LAYOUT):
        if layout not in LAYOUTS:
            raise Http404("Unknown layout")
        return render(request, "pulse/display.html", _display_context(layout, public=False))


class PulseOppView(LoginRequiredMixin, View):
    """The opportunity dossier: one engagement's full record, on one page.

    Server-renders only the identity (name, id); everything quantitative comes
    from ``/api/opp/`` so the page opens instantly and the data arrives keyed
    on the indexed opportunity_id. Authenticated-only -- the dossier names the
    partner and itemises money, which no public link is entitled to.
    """

    def get(self, request, opp_id: int):
        from django.conf import settings

        opp = PulseOpportunity.objects.filter(opportunity_id=opp_id).first()
        if opp is None:
            raise Http404("No such opportunity")
        return render(
            request,
            "pulse/opp.html",
            {
                "opp_id": opp.opportunity_id,
                "opp_name": opp.name or f"Opportunity {opp.opportunity_id}",
                "mapbox_token": getattr(settings, "MAPBOX_TOKEN", "") or "",
            },
        )


class PulsePublicView(View):
    """Unauthenticated, token-scoped, revocable.

    Tokens are individually scoped so a link given to one funder can be killed
    without breaking anyone else's — a single shared public URL would be a
    one-way door.
    """

    def get(self, request, token):
        row = PulsePublicToken.objects.filter(token=token).first()
        if row is None or not row.is_usable:
            # Same response for unknown and revoked, so a revoked link cannot be
            # distinguished from a wrong guess.
            raise Http404("No such display")

        PulsePublicToken.objects.filter(pk=row.pk).update(last_viewed_at=timezone.now(), view_count=row.view_count + 1)

        context = _display_context(row.layout_slug, public=True, show_partner_names=row.show_partner_names)
        context["public_token"] = row.token
        response = render(request, "pulse/display.html", context)
        # Labs already serves Disallow: / — belt and braces for a public URL.
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


def _report_scope(report: PulseReport):
    """Resolve a report's stored scope through the live API's own resolver.

    Deliberately routed through ``_program_scope`` rather than reimplemented:
    the report and the dashboard must agree about what "this programme, this
    window" selects, and the only way to guarantee that is one resolver.
    """
    from connect_labs.pulse.api import _program_scope

    class _Req:
        # `_program_scope` reads request.GET and, for partner scoping, consults
        # `_partner_names_allowed`. A report carries its own disclosure setting,
        # so it answers that question itself rather than borrowing a session's.
        GET = {}
        pulse_partner_names_allowed = True

    req = _Req()
    req.GET = report.scope_params()
    req.pulse_partner_names_allowed = report.show_partner_names
    return _program_scope(req)


class PulseReportListView(LoginRequiredMixin, View):
    """Index of donor reports, and the place new ones are started."""

    def get(self, request):
        from connect_labs.pulse import reports as reports_module

        return render(
            request,
            "pulse/report_list.html",
            {
                "reports": PulseReport.objects.filter(revoked=False)[:50],
                "programs": reports_module.programs_for_picker(),
            },
        )

    def post(self, request):
        from connect_labs.pulse import reports as reports_module

        program_id = (request.POST.get("program") or "").strip()
        report = PulseReport.objects.create(
            slug=secrets.token_urlsafe(18),
            title=(request.POST.get("title") or "").strip() or "Untitled report",
            program_id=int(program_id) if program_id.isdigit() else None,
            deliverables=reports_module.default_deliverables(),
            created_by=request.user,
        )
        messages.success(request, "Report created. Fill in what Pulse can't compute.")
        return redirect("pulse:report_edit", slug=report.slug)


class PulseReportEditView(LoginRequiredMixin, View):
    """The editor: derived figures shown read-only, manual copy editable."""

    def _get_report(self, slug) -> PulseReport:
        report = PulseReport.objects.filter(slug=slug, revoked=False).first()
        if report is None:
            raise Http404("No such report")
        return report

    def get(self, request, slug):
        from django.conf import settings

        from connect_labs.pulse import reports as reports_module

        report = self._get_report(slug)
        context = reports_module.compute(report, _report_scope(report))
        context.update(
            {
                "programs": reports_module.programs_for_picker(),
                "basis_choices": PulseReport.BASIS_CHOICES,
                "retention_days": getattr(settings, "PULSE_EVENT_RETENTION_DAYS", 30),
            }
        )
        return render(request, "pulse/report_edit.html", context)

    def post(self, request, slug):
        report = self._get_report(slug)

        if request.POST.get("action") == "revoke":
            report.revoked = True
            report.save(update_fields=["revoked", "updated_at"])
            messages.success(request, "Report revoked — its share link now 404s.")
            return redirect("pulse:report_list")

        for f in ("eyebrow", "title", "prepared_for", "gift_line", "org_slug", "service_slug"):
            setattr(report, f, (request.POST.get(f) or "").strip())
        for f in ("intro", "where_we_worked", "partner_funding", "footnote", "photo_caption"):
            setattr(report, f, (request.POST.get(f) or "").strip())

        for f in ("program_id", "opportunity_id"):
            raw = (request.POST.get(f) or "").strip()
            setattr(report, f, int(raw) if raw.isdigit() else None)
        for f in ("window_start", "window_end"):
            setattr(report, f, (request.POST.get(f) or "").strip() or None)

        report.show_partner_names = bool(request.POST.get("show_partner_names"))
        report.site_chips = [c.strip() for c in (request.POST.get("site_chips") or "").split(",") if c.strip()]
        report.deliverables = _deliverables_from_post(request.POST)

        if request.FILES.get("photo"):
            report.photo = request.FILES["photo"]

        report.save()
        messages.success(request, "Saved.")
        return redirect("pulse:report_edit", slug=report.slug)


def _deliverables_from_post(post) -> list[dict]:
    """Rebuild the deliverable list from the editor's parallel field arrays.

    Rows with no label are dropped rather than saved blank: an empty tile on a
    donor report reads as a number nobody bothered to fill in.
    """
    labels = post.getlist("d_label")
    descriptions = post.getlist("d_description")
    bases = post.getlist("d_basis")
    multipliers = post.getlist("d_multiplier")
    overrides = post.getlist("d_override")
    emphases = set(post.getlist("d_emphasis"))

    rows = []
    for i, label in enumerate(labels):
        label = (label or "").strip()
        if not label:
            continue

        def at(seq, idx=i, default=""):
            return (seq[idx] if idx < len(seq) else default) or default

        try:
            multiplier = float(at(multipliers, default="1") or 1)
        except (TypeError, ValueError):
            multiplier = 1.0
        override_raw = at(overrides).strip()

        rows.append(
            {
                "label": label,
                "description": at(descriptions).strip(),
                "basis": at(bases, default=PulseReport.BASIS_SERVICES),
                "multiplier": multiplier,
                "override": override_raw or None,
                "emphasis": str(i) in emphases,
            }
        )
    return rows


class PulseReportView(View):
    """The report itself — web page and print surface in one.

    Unauthenticated and slug-scoped, matching ``PulsePublicView``: a donor
    report exists to be sent to a donor. Revoking is the same one-way switch,
    and a revoked report is indistinguishable from one that never existed.
    """

    def get(self, request, slug):
        from connect_labs.pulse import reports as reports_module

        report = PulseReport.objects.filter(slug=slug).first()
        if report is None or not report.is_usable:
            raise Http404("No such report")

        context = reports_module.compute(report, _report_scope(report))
        context["is_print_surface"] = True
        response = render(request, "pulse/report.html", context)
        response["X-Robots-Tag"] = "noindex, nofollow"
        return response


def mint_public_token(user, *, label: str = "", layout: str = DEFAULT_LAYOUT, show_partner_names: bool = True):
    return PulsePublicToken.objects.create(
        token=secrets.token_urlsafe(24),
        label=label,
        layout_slug=layout,
        show_partner_names=show_partner_names,
        created_by=user,
    )
