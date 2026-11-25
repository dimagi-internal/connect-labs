"""Connect Web's pages: pick a pool worker, then do what the worker's Android app does.

Every page goes through `service`; every POST is CSRF-protected and redirects back with a message.
A PersonalID / Connect / HQ failure is shown in plain language (`errors.user_message`), never as
a stack trace, a response body or a token.
"""

from __future__ import annotations

import logging

from django.contrib import messages
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect
from django.urls import reverse
from django.views import View
from django.views.generic import TemplateView

from . import service
from .access import ConnectWebAccessMixin
from .errors import ConnectWebError, user_message
from .models import PoolWorker

logger = logging.getLogger(__name__)

TABS = (
    ("about", "About"),
    ("learn", "Learn"),
    ("claim", "Claim"),
    ("deliver", "Deliver"),
    ("payments", "Payments"),
)


class _SessionMixin(ConnectWebAccessMixin):
    """Pages that act as the checked-out worker: no session sends you to the picker."""

    def dispatch(self, request, *args, **kwargs):
        if request.user.is_authenticated:
            self.worker_session = service.current_session(request.user)
        return super().dispatch(request, *args, **kwargs)

    def _require_session(self):
        if self.worker_session is None:
            messages.info(self.request, "Pick a worker to act as first.")
            return redirect("connect_web:workers")
        return None

    def shell_context(self) -> dict:
        return {"worker_session": self.worker_session}


def _log_failure(action: str, exc: Exception) -> None:
    # The error's own text carries a status and a code only, never a body or a secret.
    logger.info("connect_web %s failed: %s", action, exc)


class HomeView(ConnectWebAccessMixin, View):
    def get(self, request):
        if service.current_session(request.user):
            return redirect("connect_web:opportunities")
        return redirect("connect_web:workers")


class WorkerPickerView(ConnectWebAccessMixin, TemplateView):
    template_name = "connect_web/workers.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        mine = service.current_session(self.request.user)
        rows = []
        for worker in PoolWorker.objects.filter(active=True):
            held = worker.active_session()
            rows.append(
                {
                    "worker": worker,
                    "held": held,
                    "held_by_me": bool(held and held.user_id == self.request.user.pk),
                    "held_by": service.display_user(held.user) if held else "",
                    "can_take_over": bool(
                        held and held.user_id != self.request.user.pk and service.can_take_over(held)
                    ),
                }
            )
        ctx.update({"rows": rows, "worker_session": mine})
        return ctx

    def post(self, request):
        try:
            worker_pk = int(request.POST.get("pool_worker") or "")
        except ValueError:
            raise Http404("No such pool worker") from None
        worker = get_object_or_404(PoolWorker, pk=worker_pk, active=True)
        try:
            service.check_out(request.user, worker, take_over=request.POST.get("take_over") == "1")
        except ConnectWebError as exc:
            messages.error(request, user_message(exc))
            return redirect("connect_web:workers")
        messages.success(request, f"You're now acting as {worker.display_name}.")
        return redirect("connect_web:opportunities")


class ReleaseView(ConnectWebAccessMixin, View):
    def post(self, request):
        session = service.current_session(request.user)
        if session:
            service.release(session)
            messages.success(request, f"Released {session.pool_worker.display_name}.")
        return redirect("connect_web:workers")


class OpportunityListView(_SessionMixin, TemplateView):
    template_name = "connect_web/opportunities.html"

    def get(self, request, *args, **kwargs):
        if response := self._require_session():
            return response
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.shell_context())
        try:
            ctx["opportunities"] = service.list_opportunities(self.worker_session)
        except ConnectWebError as exc:
            _log_failure("opportunity list", exc)
            ctx["opportunities"] = []
            ctx["error"] = user_message(exc)
        return ctx


class OpportunityDetailView(_SessionMixin, TemplateView):
    template_name = "connect_web/opportunity.html"

    def get(self, request, *args, **kwargs):
        if response := self._require_session():
            return response
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.shell_context())
        opportunity_id = kwargs["opportunity_id"]
        try:
            detail = service.opportunity_detail(self.worker_session, opportunity_id)
        except ConnectWebError as exc:
            _log_failure("opportunity detail", exc)
            ctx.update({"error": user_message(exc), "detail": None, "opportunity_id": opportunity_id, "tabs": TABS})
            return ctx
        default_tab = "deliver" if detail["claimed"] else "learn"
        tab = self.request.GET.get("tab") or default_tab
        if tab not in dict(TABS):
            tab = default_tab
        ctx.update(
            {"detail": detail, "opp": detail["opp"], "tab": tab, "tabs": TABS, "opportunity_id": opportunity_id}
        )
        return ctx


class _ActionView(_SessionMixin, View):
    """A POST that acts as the worker, then goes back to the opportunity's tab with a message."""

    tab = "about"
    action = ""

    def post(self, request, opportunity_id):
        if response := self._require_session():
            return response
        try:
            text = self.act(request, opportunity_id)
        except ConnectWebError as exc:
            _log_failure(self.action, exc)
            messages.error(request, user_message(exc))
        else:
            if text:
                messages.success(request, text)
        return redirect(f"{reverse('connect_web:opportunity', args=[opportunity_id])}?tab={self.tab}")

    def act(self, request, opportunity_id) -> str:
        raise NotImplementedError


class StartLearningView(_ActionView):
    tab = "learn"
    action = "start learning"

    def act(self, request, opportunity_id):
        service.start_learning(self.worker_session, opportunity_id)
        return "Learning started. The learn app is ready for this worker."


class ClaimView(_ActionView):
    tab = "claim"
    action = "claim"

    def act(self, request, opportunity_id):
        created = service.claim(self.worker_session, opportunity_id)
        return "Opportunity claimed. You can start delivering." if created else "This opportunity was already claimed."


class ConfirmPaymentView(_ActionView):
    tab = "payments"
    action = "confirm payment"

    def act(self, request, opportunity_id):
        payment_id = (request.POST.get("payment_id") or "").strip()
        decision = request.POST.get("decision")
        if not payment_id or decision not in ("confirm", "dispute"):
            raise ConnectWebError("Choose a payment and whether you received it.")
        service.confirm_payment(self.worker_session, payment_id, decision == "confirm")
        return "Payment confirmed." if decision == "confirm" else "Payment marked as not received."


class FormEntryView(_SessionMixin, TemplateView):
    """Where the form player will run a learn or deliver form. A placeholder until that track lands."""

    template_name = "connect_web/form_entry.html"

    def get(self, request, *args, **kwargs):
        if kwargs["app_kind"] not in service.APP_KINDS:
            raise Http404("Unknown app kind")
        if response := self._require_session():
            return response
        return super().get(request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx.update(self.shell_context())
        opportunity_id, app_kind = kwargs["opportunity_id"], kwargs["app_kind"]
        ctx.update({"opportunity_id": opportunity_id, "app_kind": app_kind, "back_tab": app_kind})
        try:
            opp = service.get_opportunity(self.worker_session, opportunity_id)
            ctx["opp"] = opp
            # No HQ call yet: the engine will ask for the token itself.
            ctx["engine"] = service.form_engine_context(self.worker_session, opp, app_kind, with_token=False)
        except ConnectWebError as exc:
            _log_failure("form entry", exc)
            ctx["error"] = user_message(exc)
        return ctx
