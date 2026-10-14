"""Platform support's screen for declaring which organisation runs a programme (#2458)."""

from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.utils.decorators import method_decorator

from connect_labs.supply_chain.api_views import has_program_context
from connect_labs.supply_chain.form_views import OperationActionView, OperationFormView
from connect_labs.supply_chain.navigation import scoped_url
from connect_labs.supply_chain.runner import declared_runner, is_operator
from connect_labs.supply_chain.runner.forms import ProgrammeRunnerForm, TeamMemberForm
from connect_labs.supply_chain.views import OperationBase


class ProgrammeRunnerView(OperationFormView):
    operation = "programme_runner_set"
    form_class = ProgrammeRunnerForm
    title = "Who runs this program"
    intro = (
        'Platform support only. The organisation you name becomes "we" on this program\'s pages: its members '
        "tender, award, order and pay, and record first-hand. Everyone else who can see the program reads it. "
        "Any update link already issued to that organisation here is revoked, because its own people sign in."
    )
    submit_label = "Declare"

    def dispatch(self, request, *args, **kwargs):
        # The operation refuses anyone else too; this keeps them from a form
        # they can never submit.
        if request.user.is_authenticated and not is_operator(request.user):
            raise PermissionDenied("only platform support declares which organisation runs a program")
        return super().dispatch(request, *args, **kwargs)

    def get_initial(self):
        initial = super().get_initial()
        program_id = getattr(self.access(), "program_id", None)
        current = declared_runner(program_id) if program_id else None
        if current is not None:
            initial["org"] = current.pk
        return initial

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Overview", "href": scoped_url(self.request, "supply_chain:overview")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return scoped_url(self.request, "supply_chain:overview")

    def redirect_to(self, result):
        return scoped_url(self.request, "supply_chain:overview")


# ---- the running organisation's team ----------------------------------------


@method_decorator(login_required, name="dispatch")
class ProgrammeTeamView(OperationBase):
    """Who acts for the program: its running organisation's team (#2458)."""

    template_name = "supply_chain/programme_team.html"

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["has_program_context"] = has_program_context(self.request)
        if context["has_program_context"]:
            try:
                context["team"] = self.op("programme_team_list")
            except (PermissionDenied, ValueError) as exc:
                context["team_refusal"] = str(exc)
        return context


class ProgrammeTeamAddView(OperationFormView):
    operation = "programme_team_add"
    form_class = TeamMemberForm
    title = "Add a person to the team"
    intro = (
        "They act for the program: tender, award, order and pay, and record first-hand. "
        "Platform support, or an admin of the organisation that runs the program, adds people here."
    )
    submit_label = "Add"

    def breadcrumb(self, **kwargs):
        return [
            {"label": "Team", "href": scoped_url(self.request, "supply_chain:programme_team")},
            {"label": self.title},
        ]

    def cancel_href(self, **kwargs):
        return scoped_url(self.request, "supply_chain:programme_team")

    def redirect_to(self, result):
        return scoped_url(self.request, "supply_chain:programme_team")


class ProgrammeTeamRemoveView(OperationActionView):
    operation = "programme_team_remove"
    success_message = "Removed from the team."

    def fixed(self, **kwargs):
        return {"user_id": int(kwargs["user_id"])}

    def redirect_to(self, **kwargs):
        return scoped_url(self.request, "supply_chain:programme_team")
