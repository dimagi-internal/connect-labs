"""A pinned workflow (models.py), rendered inside the supply header and tabs.

The same runner as the workflow's own page (workflow/run.html), mounted in the
supply base: `WorkflowRunView` builds the page data, as the viewer, for the
workflow's current open run -- the newest one still in progress, or a new one
started for today, so a supply tab simply shows what is current. The supply page
gives the run its context and two ways out: the workflow's editor, and the tab's own page if it replaced one.
"""

from django.http import Http404
from django.urls import reverse
from django.views.generic import TemplateView

from connect_labs.supply_chain.workflow_views.models import SupplyWorkflowView
from connect_labs.supply_chain.workflow_views.runs import current_run_id
from connect_labs.workflow.views import WorkflowRunView


class SupplyWorkflowPageView(WorkflowRunView):
    template_name = "supply_chain/workflow_view.html"

    def get(self, request, *args, **kwargs):
        context = getattr(request, "labs_context", None) or {}
        program_id = context.get("program_id")
        pin = None
        if program_id:
            pin = SupplyWorkflowView.objects.filter(program_id=int(program_id), slug=kwargs.get("slug")).first()
        if pin is None:
            raise Http404("no workflow is pinned under that name in this programme")
        self.pin = pin
        self.supply_context = context
        # The run belongs to the workflow's own opportunity, whichever one the supply page was
        # browsing, and is read as the workflow's own URL reads it: by that opportunity alone.
        # Records scoped to an opportunity carry no programme, so a programme in the scope too
        # finds none of them. The supply frame gets the programme back in get_context_data.
        if pin.opportunity_id:
            request.labs_context = {
                **{k: v for k, v in context.items() if k != "program_id"},
                "opportunity_id": pin.opportunity_id,
            }
        self.kwargs["definition_id"] = pin.workflow_definition_id
        params = request.GET.copy()
        if not params.get("run_id"):
            run_id = current_run_id(request, pin)
            if run_id:
                params["run_id"] = str(run_id)
        request.GET = params
        # Not WorkflowRunView.get: its redirects re-scope a bare workflow URL, which this is not.
        return TemplateView.get(self, request, *args, **kwargs)

    def get_context_data(self, **kwargs):
        from connect_labs.supply_chain.banner import program_line
        from connect_labs.supply_chain.navigation import SUPPLY_TABS, supply_tabs

        context = super().get_context_data(**kwargs)
        self.request.labs_context = self.supply_context
        pin = self.pin
        run_id = self.request.GET.get("run_id")
        scope = f"opportunity_id={pin.opportunity_id}" if pin.opportunity_id else ""
        context.update(
            pin=pin,
            supply_tabs=supply_tabs(self.request),
            supply_program_line=program_line(self.request),
            edit_url=(
                reverse("labs:workflow:run", args=[pin.workflow_definition_id]) + f"?{scope}&run_id={run_id}&edit=true"
            ),
            open_url=reverse("labs:workflow:run", args=[pin.workflow_definition_id]) + f"?{scope}&run_id={run_id}",
            classic_url=reverse(pin.replaces) if pin.replaces else None,
            classic_label=dict(SUPPLY_TABS).get(pin.replaces, ""),
        )
        return context
