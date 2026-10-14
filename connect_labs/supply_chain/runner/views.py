"""Platform support's screen for declaring which organisation runs a programme (#2458)."""

from django.core.exceptions import PermissionDenied

from connect_labs.supply_chain.form_views import OperationFormView
from connect_labs.supply_chain.navigation import scoped_url
from connect_labs.supply_chain.runner import declared_runner, is_operator
from connect_labs.supply_chain.runner.forms import ProgrammeRunnerForm


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
