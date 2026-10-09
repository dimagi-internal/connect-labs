"""A pinned workflow (models.py), rendered inside the supply header and tabs.

The same runner as the workflow's own page (workflow/run.html), mounted in the
supply base: `WorkflowRunView` builds the page data, as the viewer, for the
workflow's current open run -- the newest one still in progress, or a new one
started for today, so a supply tab simply shows what is current. The supply page
gives the run its context and two ways out: the workflow's editor, and the tab's own page if it replaced one.
The editor links are for the people who build the view (builds_the_view), not for everyone who reads it.

`?as_of=` is the supply pages' own date. The page is not rewound (the runner reads live
records of its own); instead the date rides on the runner's supply endpoints, so every
supply source that can read a past day reads that one, and the supply header shows the
date and carries it on its links as it does on any rewound page.
"""

from urllib.parse import urlencode

from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils import timezone
from django.views.generic import TemplateView

from connect_labs.supply_chain.history.as_of import HAPPENED, parse_as_of
from connect_labs.supply_chain.workflow_views.models import SupplyWorkflowView
from connect_labs.supply_chain.workflow_views.runs import current_run_id
from connect_labs.workflow.views import WorkflowRunView


class SupplyWorkflowPageView(WorkflowRunView):
    template_name = "supply_chain/workflow_view.html"

    def get(self, request, *args, **kwargs):
        try:
            as_of = parse_as_of(request.GET.get("as_of"))
        except ValueError:
            return HttpResponseBadRequest("as_of must be a date written YYYY-MM-DD.")
        if as_of and as_of > timezone.localdate():
            return HttpResponseBadRequest("as_of cannot be after today.")
        request.supply_as_of = as_of
        # Its supply sources read stock as it had happened by that day (worker_stock with
        # as_of), as the stock pages do; the banner said "records as they stood that evening".
        request.supply_as_of_basis = HAPPENED
        context = getattr(request, "labs_context", None) or {}
        program_id = context.get("program_id")
        pin = None
        if program_id:
            pin = SupplyWorkflowView.objects.filter(program_id=int(program_id), slug=kwargs.get("slug")).first()
        if pin is None:
            if not program_id:
                # No programme in view: Supply's home asks for one, rather than a bare 404.
                return redirect("supply_chain:home")
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
        as_of = self.request.supply_as_of
        endpoints = (context.get("workflow_data") or {}).get("apiEndpoints") or {}
        if as_of:
            for key in ("getSupplyData", "querySupply"):
                if endpoints.get(key):
                    endpoints[key] += "?" + urlencode({"as_of": as_of.isoformat()})
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
            builder=builds_the_view(self.request.user, context.get("definition"), pin),
            classic_url=reverse(pin.replaces) if pin.replaces else None,
            classic_label=dict(SUPPLY_TABS).get(pin.replaces, ""),
        )
        return context


def builds_the_view(user, definition, pin: SupplyWorkflowView) -> bool:
    """Whether to offer this viewer the workflow's editor: its author, whoever pinned it, or staff.

    Anyone in the programme may be ABLE to edit the workflow (write access to its scope is all
    labs asks), but a programme manager reading a supply tab is not building one, and
    "Edit workflow" beside the title reads as part of the page. Not a permission check: the
    workflow's own page still decides who may save.
    """
    if not getattr(user, "is_authenticated", False):
        return False
    if user.is_staff or user.is_superuser:
        return True
    author = definition.get("username") if isinstance(definition, dict) else getattr(definition, "username", None)
    return user.username in {author, pin.created_by} - {None, ""}
