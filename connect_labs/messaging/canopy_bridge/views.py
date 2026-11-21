"""Staff pages of the canopy bridge: "what the agent saw" -- one stored Worker Brief."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.views.generic import TemplateView

from connect_labs.messaging.canopy_bridge.models import WorkerBrief


class BriefView(LoginRequiredMixin, TemplateView):
    """A Worker Brief by its hash, for a person who may see the worker in the thread's program
    (the Connect Worker page's own rule, ``workers.access.view_for``); anyone else gets a 404."""

    login_url = "/labs/login/"
    template_name = "canopy_bridge/brief.html"

    def get_context_data(self, **kwargs):
        from connect_labs.messaging.models import Message
        from connect_labs.workers.access import view_for

        ctx = super().get_context_data(**kwargs)
        brief = WorkerBrief.objects.select_related("worker", "thread").filter(hash=self.kwargs["brief_hash"]).first()
        if brief is None or brief.worker.erased_at is not None:
            raise Http404("No such brief")
        view = view_for(self.request.user, brief.worker, request=self.request, on_page=True)
        if brief.thread is None or brief.thread.program_id not in view.program_ids:
            raise Http404("No such brief")
        ctx.update(
            {
                "brief": brief,
                "worker": brief.worker,
                "replies": Message.objects.filter(brief_hash=brief.hash, author_kind="agent").order_by("created_at"),
            }
        )
        return ctx
