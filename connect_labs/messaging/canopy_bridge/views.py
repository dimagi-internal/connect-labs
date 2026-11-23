"""Staff pages of the canopy bridge: "what the agent saw" -- one stored Worker Brief."""

from __future__ import annotations

from django.contrib.auth.mixins import LoginRequiredMixin
from django.http import Http404
from django.views.generic import TemplateView

from connect_labs.messaging.canopy_bridge.models import WorkerBrief


class BriefView(LoginRequiredMixin, TemplateView):
    """A Worker Brief by its hash, for a person who may read the thread it was built for (the
    Inbox's rule, ``messaging.access``); anyone else gets a 404."""

    login_url = "/labs/login/"
    template_name = "canopy_bridge/brief.html"

    def get_context_data(self, **kwargs):
        from connect_labs.messaging import access
        from connect_labs.messaging.models import Message

        ctx = super().get_context_data(**kwargs)
        brief = WorkerBrief.objects.select_related("worker", "thread").filter(hash=self.kwargs["brief_hash"]).first()
        if brief is None or brief.worker.erased_at is not None:
            raise Http404("No such brief")
        if brief.thread is None or access.refusal(access.caller_for(self.request), brief.thread):
            raise Http404("No such brief")
        ctx.update(
            {
                "brief": brief,
                "worker": brief.worker,
                "replies": Message.objects.filter(brief_hash=brief.hash, author_kind="agent").order_by("created_at"),
            }
        )
        return ctx
