"""Audit-trail review surface (§164.308(a)(1)(ii)(D) information system
activity review).

Admin-gated dashboard: filterable event list, anomaly summary cards, and a
"record review" action that logs the review itself as an audit event — the
documented, practiced review process auditors ask about.
"""
from datetime import timedelta

from django.contrib import messages
from django.core.paginator import Paginator
from django.db.models import Count, Sum
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import TemplateView

from connect_labs.audit_trail import service
from connect_labs.audit_trail.models import Action, AuditEvent, Outcome
from connect_labs.labs.view_mixins import AdminRequiredMixin

PAGE_SIZE = 50


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
        paginator = Paginator(self.get_queryset(), PAGE_SIZE)
        context.update(
            {
                "page_obj": paginator.get_page(page_number),
                "total_events": paginator.count,
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
