"""The Inbox at /labs/messaging/: conversations with Connect Workers, and staff replies.

A person sees only threads in programs or opportunities they hold (`access.py`); anything
else is a 404, never a "forbidden" that would say it exists.
"""

from __future__ import annotations

from django.conf import settings
from django.contrib import messages as flash
from django.contrib.auth.mixins import LoginRequiredMixin
from django.core.paginator import Paginator
from django.http import Http404
from django.shortcuts import redirect
from django.urls import reverse
from django.views.generic import TemplateView

from connect_labs.audit_trail.service import record as audit_record
from connect_labs.messaging import access, inbox, service
from connect_labs.messaging.models import DeliveryStatus, HandlerKind, ThreadState
from connect_labs.workers.models import Visibility

PAGE_SIZE = 50
PREVIEW_CHARS = 90


class _InboxPage(LoginRequiredMixin, TemplateView):
    login_url = "/labs/login/"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        ctx["messaging_enabled"] = getattr(settings, "MESSAGING_ENABLED", False)
        return ctx


class InboxView(_InboxPage):
    template_name = "messaging/inbox.html"

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        filters = {key: self.request.GET.get(key, "") for key in inbox.FILTERS}
        filters["state"] = filters["state"] or ThreadState.OPEN
        try:
            qs = inbox.threads(self.request, **filters)
            facets = inbox.facets(self.request)
        except access.NoAccess as e:
            ctx.update({"unavailable": str(e), "filters": filters, "page": None, "facets": {}})
            return ctx
        except ValueError as e:
            raise Http404("Not found") from e
        page = Paginator(qs, PAGE_SIZE).get_page(self.request.GET.get("page"))
        rows = []
        for thread in page.object_list:
            last = thread.messages.exclude(delivery_status=DeliveryStatus.NOTE).order_by("-created_at", "-id").first()
            rows.append({"thread": thread, "last": last, "preview": _preview(last)})
        audit_record(
            "read",
            resource_type="messaging_inbox",
            record_count=len(rows),
            metadata={"filters": {k: v for k, v in filters.items() if v}},
        )
        ctx.update(
            {
                "filters": filters,
                "page": page,
                "rows": rows,
                "facets": facets,
                "states": [("open", "Open"), ("closed", "Closed"), ("all", "All")],
                "handler_kinds": HandlerKind.choices,
            }
        )
        return ctx


def _preview(message) -> str:
    if message is None:
        return ""
    text = " ".join((message.text or "").split())
    return text if len(text) <= PREVIEW_CHARS else text[: PREVIEW_CHARS - 1] + "…"


class ThreadView(_InboxPage):
    template_name = "messaging/thread.html"

    def _thread(self):
        try:
            return inbox.get_thread(self.request, int(self.kwargs["thread_id"]))
        except access.NoAccess as e:
            raise Http404("Not found") from e

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        thread = self._thread()
        transcript = list(thread.messages.order_by("created_at", "id"))
        audit_record(
            "read",
            resource_type="messaging_thread",
            resource_id=thread.pk,
            record_count=len(transcript),
            program_id=thread.program_id,
            opportunity_id=thread.opportunity_id,
        )
        address = thread.address
        ctx.update(
            {
                "thread": thread,
                "worker": thread.worker,
                "transcript": transcript,
                "summaries": list(thread.summaries.all()[:5]),
                "address": address,
                "stop_reason": address.stop_reason if address else "no channel",
                "can_reply": thread.is_open and address is not None and address.can_send,
                "can_take_over": thread.is_open and thread.handler_kind != HandlerKind.HUMAN,
                "can_hand_back": thread.is_open and bool((thread.previous_handler or {}).get("kind")),
                "restricted": Visibility.RESTRICTED,
                "note": DeliveryStatus.NOTE,
            }
        )
        return ctx

    def post(self, request, *args, **kwargs):
        thread = self._thread()
        action = request.POST.get("action", "")
        try:
            if action == "reply":
                inbox.reply(request, thread.pk, request.POST.get("text", ""))
                flash.success(request, "Sent.")
            elif action == "take_over":
                inbox.take_over(request, thread.pk)
                flash.success(request, "You are answering this conversation now.")
            elif action == "hand_back":
                inbox.hand_back(request, thread.pk)
                flash.success(request, "Handed back.")
            elif action == "mark_handled":
                inbox.mark_handled(request, thread.pk)
                flash.success(request, "Marked as handled.")
            elif action == "close":
                inbox.close(request, thread.pk, request.POST.get("reason", ""))
                flash.success(request, "Closed.")
            else:
                flash.error(request, "Unknown action.")
        except service.MessagingError as e:
            flash.error(request, e.public_message)
        except access.NoAccess as e:
            raise Http404("Not found") from e
        return redirect(reverse("messaging:thread", args=[thread.pk]))
