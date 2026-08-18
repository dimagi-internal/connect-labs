"""Audit-trail review surface (§164.308(a)(1)(ii)(D) information system
activity review).

Admin-gated dashboard: filterable event list, anomaly summary cards, and a
"record review" action that logs the review itself as an audit event — the
documented, practiced review process auditors ask about.
"""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Max, Sum
from django.shortcuts import redirect
from django.utils import timezone
from django.utils.functional import cached_property
from django.views.generic import TemplateView

from connect_labs.audit_trail import service
from connect_labs.audit_trail.models import Action, AuditEvent, Outcome
from connect_labs.audit_trail.timeline import build_session_timeline
from connect_labs.labs.view_mixins import AdminRequiredMixin

PAGE_SIZE = 50

# Stop counting matching rows past this many. The pager shows "10,000+" instead
# of an exact total beyond it.
COUNT_CAP = 10_000


class CappedPaginator(Paginator):
    """A Paginator that refuses to count the whole table.

    ``Paginator.count`` is a bare ``COUNT(*)`` over the filtered queryset, and
    the default dashboard filter (``labs_only=False`` minus canary and page
    views) is not index-coverable, so it degenerates into a full scan of every
    audit row ever written. Measured on production 2026-08-18 at 2,577,392 rows:

        exact count, cold cache   15.668s
        exact count, warm          0.422s
        capped count (10,001)      0.006s

    Two things make the exact count the wrong trade rather than merely a slow
    one. It was the single largest cost on the page AND the only one a bigger
    instance did not fix -- resizing the database from db.t3.small to
    db.m6g.large left it unchanged at ~5.2s, because it is bounded by rows
    examined, not by IO. And it is *unstable*: the same query is 0.4s or 15.7s
    depending on cache state, so the page's latency swings 37x for a number
    nobody acts on. Nobody pages to row 2.5 millionth of an audit log; they
    filter.

    Counting one row past the cap is what distinguishes "exactly at the cap"
    from "more than the cap", so ``is_capped`` can be honest about which.
    """

    cap = COUNT_CAP

    @cached_property
    def count(self):
        return self.object_list[: self.cap + 1].count()

    @property
    def is_capped(self) -> bool:
        return self.count > self.cap


class AuditTrailDashboardView(AdminRequiredMixin, TemplateView):
    template_name = "audit_trail/dashboard.html"

    def get_queryset(self):
        qs = AuditEvent.objects.all()
        params = self.request.GET
        if params.get("username"):
            qs = qs.filter(username__icontains=params["username"].strip())
        if params.get("action"):
            qs = qs.filter(action=params["action"])
        if params.get("resource_type"):
            qs = qs.filter(resource_type__icontains=params["resource_type"].strip())
        if params.get("opportunity_id"):
            try:
                qs = qs.filter(opportunity_id=int(params["opportunity_id"]))
            except ValueError:
                pass
        if params.get("outcome"):
            qs = qs.filter(outcome=params["outcome"])
        if params.get("date_from"):
            qs = qs.filter(occurred_at__date__gte=params["date_from"])
        if params.get("date_to"):
            qs = qs.filter(occurred_at__date__lte=params["date_to"])
        if params.get("include_labs_only") != "1":
            qs = qs.filter(labs_only=False)
        if params.get("include_canary") != "1":
            qs = qs.exclude(action=Action.CANARY)
        # Page views are the high-volume navigation record — hidden by default,
        # surfaced when reconstructing a specific user's session.
        if params.get("include_page_views") != "1" and params.get("action") != Action.PAGE_VIEW:
            qs = qs.exclude(action=Action.PAGE_VIEW)
        return qs

    def get_anomaly_stats(self):
        now = timezone.now()
        day_ago = now - timedelta(hours=24)
        week_ago = now - timedelta(days=7)
        base = AuditEvent.objects.all()

        exports_24h = (
            base.filter(action=Action.EXPORT, occurred_at__gte=day_ago).aggregate(n=Sum("record_count"))["n"] or 0
        )
        # Baseline: mean daily export rows over the prior 7 days (excluding the last 24h)
        prior = (
            base.filter(action=Action.EXPORT, occurred_at__gte=week_ago, occurred_at__lt=day_ago).aggregate(
                n=Sum("record_count")
            )["n"]
            or 0
        )
        export_baseline = round(prior / 6) if prior else 0

        # "Off hours" heuristic: 00:00–05:59 UTC, the team's global quiet window.
        off_hours = (
            base.filter(occurred_at__gte=week_ago, occurred_at__hour__lt=6).exclude(action__in=[Action.CANARY]).count()
        )

        last_canary = base.filter(action=Action.CANARY).order_by("-occurred_at").first()
        last_review = base.filter(action=Action.REVIEW).order_by("-occurred_at").first()

        return {
            "failed_logins_24h": base.filter(action=Action.LOGIN_FAILED, occurred_at__gte=day_ago).count(),
            "access_denied_7d": base.filter(action=Action.ACCESS_DENIED, occurred_at__gte=week_ago).count(),
            "failures_24h": base.filter(outcome=Outcome.FAILURE, occurred_at__gte=day_ago)
            .exclude(action__in=[Action.LOGIN_FAILED, Action.ACCESS_DENIED])
            .count(),
            "exports_24h": exports_24h,
            "export_baseline": export_baseline,
            "export_spike": bool(export_baseline and exports_24h > 5 * export_baseline),
            "off_hours_7d": off_hours,
            "last_canary": last_canary.occurred_at if last_canary else None,
            "canary_stale": (not last_canary or last_canary.occurred_at < now - timedelta(hours=2)),
            "last_review": last_review,
            "top_users_7d": list(
                base.filter(occurred_at__gte=week_ago, labs_only=False)
                .exclude(username="")
                .values("username")
                .annotate(n=Count("id"))
                .order_by("-n")[:8]
            ),
        }

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        page_number = self.request.GET.get("page") or 1
        paginator = CappedPaginator(self.get_queryset(), PAGE_SIZE)
        context.update(
            {
                "page_obj": paginator.get_page(page_number),
                # Capped: show the cap, and let the template mark it a floor.
                "total_events": min(paginator.count, paginator.cap),
                "total_events_capped": paginator.is_capped,
                "action_choices": Action.choices,
                "outcome_choices": Outcome.choices,
                "stats": self.get_anomaly_stats(),
                "filter_params": {k: v for k, v in self.request.GET.items() if k != "page" and v},
            }
        )
        return context

    def post(self, request, *args, **kwargs):
        """Record that a review took place (the review itself is an audit event)."""
        notes = (request.POST.get("notes") or "").strip()[:2000]
        service.record(
            Action.REVIEW,
            resource_type="audit_trail",
            user=request.user,
            metadata={"notes": notes, "filters": {k: v for k, v in request.GET.items() if v}},
        )
        messages.success(request, "Review recorded in the audit trail.")
        return redirect(request.get_full_path())


class SessionTimelineView(AdminRequiredMixin, TemplateView):
    """ "What exactly did this user do" — per-user session reconstruction.

    Groups a user's audit events into sessions (30-min idle gaps) and renders
    each as a timeline: page navigations (full URL, redacted query) with the
    data effects of that request nested underneath. Investigating a user is
    itself PHI-adjacent surveillance, so opening this view records an audit
    event naming who looked at whom.
    """

    template_name = "audit_trail/session_timeline.html"
    MAX_EVENTS = 5000

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        username = (self.request.GET.get("username") or "").strip()
        try:
            days = min(int(self.request.GET.get("days") or 7), 90)
        except ValueError:
            days = 7
        include_labs_only = self.request.GET.get("include_labs_only") == "1"
        since = timezone.now() - timedelta(days=days)

        context.update({"username": username, "days": days, "include_labs_only": include_labs_only})

        recent = (
            AuditEvent.objects.filter(occurred_at__gte=since)
            .exclude(username="")
            .exclude(action=Action.CANARY)
            .values("username")
            .annotate(n=Count("id"), last=Max("occurred_at"))
            .order_by("-last")[:30]
        )
        context["recent_users"] = recent
        if not username:
            return context

        service.record(
            Action.READ,
            resource_type="session_timeline",
            metadata={"viewed_username": username, "days": days},
        )

        qs = AuditEvent.objects.filter(username=username, occurred_at__gte=since).exclude(action=Action.CANARY)
        if not include_labs_only:
            qs = qs.filter(labs_only=False)
        events = list(qs.order_by("occurred_at")[: self.MAX_EVENTS])
        sessions = build_session_timeline(events)
        sessions.reverse()  # newest session first
        context.update(
            {
                "sessions": sessions,
                "event_count": len(events),
                "truncated": len(events) == self.MAX_EVENTS,
            }
        )
        return context
