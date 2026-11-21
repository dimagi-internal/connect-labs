"""The Connect Worker page at /labs/workers/<username>/.

Shown only to a person who may access at least one of the worker's opportunities
(`access.view_for`, Connect's own answer), and then only those opportunities'
memberships, events and coverage. A worker the person may not see is a 404, the
same as one that does not exist.
"""

from __future__ import annotations

from django.contrib import messages
from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.shortcuts import redirect
from django.utils import timezone
from django.views.generic import TemplateView

from connect_labs.utils.dimagi_user import is_dimagi_staff

from . import events as ev
from .access import view_for
from .models import ConnectWorker, FactStatus, StaffNote, WorkerFact
from .profile import build_profile

SOURCES = ("connect", "labs", "ocs", "personalid")


def _agent_replies(worker, view, limit: int = 20) -> list:
    """The agent's latest replies in the programs this person may see, each with its brief's hash."""
    from connect_labs.messaging.models import Message

    return list(
        Message.objects.filter(
            worker=worker, author_kind="agent", direction="out", thread__program_id__in=list(view.program_ids)
        )
        .exclude(delivery_status="note")
        .order_by("-created_at", "-id")[:limit]
    )


def _int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


class WorkerPageView(LoginRequiredMixin, TemplateView):
    login_url = "/labs/login/"
    template_name = "workers/worker.html"

    def _load(self):
        worker = ConnectWorker.objects.filter(username=self.kwargs["username"], erased_at__isnull=True).first()
        if worker is None:
            raise Http404("No such Connect Worker")
        view = view_for(self.request.user, worker, request=self.request, on_page=True)
        if not view.any:
            raise Http404("No such Connect Worker")
        return worker, view

    def get_context_data(self, **kwargs):
        ctx = super().get_context_data(**kwargs)
        worker, view = self._load()
        kinds = [k for k in self.request.GET.getlist("kind") if k]
        opportunity = _int(self.request.GET.get("opportunity"))
        source = self.request.GET.get("source") or ""
        profile = build_profile(worker, view, kinds=kinds or None, opportunity_id=opportunity)
        timeline = profile["timeline"]
        if source in SOURCES:
            timeline = [e for e in timeline if e["source"] == source]
        programs = sorted({m["program_id"] for m in profile["memberships"] if m["program_id"] is not None})
        ctx.update(
            {
                "worker": worker,
                "profile": profile,
                "timeline": timeline,
                "view": view,
                "kind_choices": sorted(ev.KIND_LABELS.items(), key=lambda kv: kv[1]),
                "source_choices": SOURCES,
                "selected_kinds": kinds,
                "selected_opportunity": opportunity,
                "selected_source": source,
                "program_choices": programs,
                "agent_replies": _agent_replies(worker, view),
                "can_erase": is_dimagi_staff(self.request.user),
            }
        )
        return ctx

    def post(self, request, *args, **kwargs):
        worker, view = self._load()
        action = request.POST.get("action")
        who = getattr(request.user, "username", "") or getattr(request.user, "email", "")
        if action == "add_note":
            body = (request.POST.get("body") or "").strip()
            program_id = _int(request.POST.get("program_id"))
            if not body:
                messages.error(request, "A note needs some text.")
            elif program_id is not None and program_id not in view.program_ids:
                raise Http404("No such program")
            else:
                StaffNote.objects.create(
                    worker=worker,
                    program_id=program_id,
                    body=body[:5000],
                    private=bool(request.POST.get("private")),
                    author=who,
                )
                messages.success(request, "Note added.")
        elif action == "erase":
            if not is_dimagi_staff(request.user):
                raise Http404("No such action")
            if (request.POST.get("confirm_username") or "").strip() != worker.username:
                messages.error(request, "Type the Connect Worker's username exactly to erase them.")
                return redirect("workers:worker", username=worker.username)
            from connect_labs.messaging.canopy_bridge.erasure import erase_worker

            erasure = erase_worker(worker, by=who)
            messages.success(
                request,
                f"Erased Connect Worker {worker.username} from labs"
                + ("; canopy is being told to forget them." if erasure.canopy_status == "pending" else "."),
            )
            return redirect("/labs/")
        elif action == "retract_fact":
            fact = WorkerFact.objects.filter(pk=_int(request.POST.get("fact_id")), worker=worker).first()
            if fact is None or (fact.program_id is not None and fact.program_id not in view.program_ids):
                raise Http404("No such fact")
            if fact.status == FactStatus.ACTIVE:
                fact.status = FactStatus.RETRACTED
                fact.retracted_at = timezone.now()
                fact.retracted_by = who
                fact.retraction_reason = (request.POST.get("reason") or "")[:300]
                fact.save(update_fields=["status", "retracted_at", "retracted_by", "retraction_reason"])
                messages.success(request, "Fact retracted.")
        return redirect("workers:worker", username=worker.username)
